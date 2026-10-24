"""Foreground composition and one atomic owner for decisions and execution state."""
import copy
import hashlib
import json
from pathlib import Path
import signal
import time

from configuration import load_config
from executor import LimitChaser, PaperExecutor, execution_state
from feed import FixtureFeed, HyperliquidCandleFeed
from models import Bar, INTERVALS, Target, validate_market
from reporting import ConsoleReporter, TelegramReporter
from risk import Stop, number
from runtime_io import Admission, SerialIO
from runtime_journal import MAX_HISTORY, MAX_REPORTS, RuntimeJournal
from strategy import BandBreakout
from twon20.withdrawals import withdrawal_policy

RELEASE_POLL_MS = 5000
# New risk waits for a release read of a block at most 20 s old. Reads start every
# 5 s, so only reads slower than about 9 s reach that age, and at least 100 s of
# the vault's 120 s notice remain to cancel an entry order before the first round.
RELEASE_STALE_MS = 20000
# A read in flight at shutdown gets one RPC request timeout to finish. Its daemon
# thread only reads, so a hung request cannot hold the process open after that.
RELEASE_JOIN_SECONDS = 15


def fingerprint(config):
    # Source changes to the replaceable strategy require a reviewed migration,
    # not silently restoring state into a different implementation.
    source = Path(__file__).with_name("strategy.py").read_bytes()
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode() + source).hexdigest()


def execution_progress(execution):
    child = execution["child"]
    position = number(execution["position"], signed=True)
    filled, value = number(execution["filledSize"]), number(execution["filledValue"])
    fees = number(execution["fees"], signed=True)
    maker, taker = execution["makerFills"], execution["takerFills"]
    if child:
        partial = number(child["filled"])
        position = number(child["basePosition"], signed=True) + partial * (1 if child["plan"]["buy"] else -1)
        filled += partial
        value += number(child.get("value", "0"))
        fees += number(child.get("fees", "0"), signed=True)
        maker += child.get("maker", 0)
        taker += child.get("taker", 0)
    return {"position": None if child else str(position), "knownPosition": str(position),
            "unresolvedChild": child is not None, "filledSize": str(filled),
            "vwap": str(value / filled) if filled else None, "feesUsdc": str(fees),
            "makerFills": maker, "takerFills": taker}


class Runtime:
    """Tick budget: no network waits, <=8 bars, one executor transition.

    The foreground loop waits at most 100 ms between stop checks. The shared
    daemon worker may still be in an SDK request after a bounded shutdown
    grace. That leaves a durable unresolved identity, never a canceled request.
    Release reads use a second worker, so a slow RPC cannot hold back an order
    or cancel on the shared one.
    """
    def __init__(self, config, journal, mode="paper", worker=None, adapter=None, reporter=None,
                 feed=None, strategy=None, executor_factory=None, session_seconds=None, clock=None, recover_only=False,
                 lifecycle=None, release_worker=None):
        self.config, self.journal, self.mode, self.adapter, self.lifecycle = config, journal, mode, adapter, lifecycle
        self.release_read, self.release_started = None, None
        self.recover_only = recover_only
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.worker = worker or SerialIO()
        self.reporter = reporter or ConsoleReporter()
        self.strategy = strategy or BandBreakout(**config["strategy"])
        self.policy = fingerprint(config)
        now = self.clock()
        old = journal.read_runtime()
        if old:
            if old.get("mode") != mode or not recover_only and old.get("policy") != self.policy:
                raise Stop("RUNTIME_POLICY_CHANGED", "Retained strategy/configuration or mode changed. Reconcile exposure before an explicit state migration.")
            self.state = old
            if recover_only:
                self.policy = old["policy"]
        else:
            self.state = {"schema": "2n20-runtime-checkpoint-v1", "scope": journal.scope, "mode": mode, "policy": self.policy,
                          "cursor": None, "history": [], "strategy": {}, "normalized": None,
                          "execution": execution_state(now), "reports": [], "reportSequence": 0,
                          "decisionSequence": 0, "sessionDeadlineMs": now + session_seconds * 1000 if session_seconds else None,
                          "cleanupDeadlineMs": now + (session_seconds + 60) * 1000 if session_seconds else None}
        self.admission = Admission(self.clock, self.state["sessionDeadlineMs"], self.state["cleanupDeadlineMs"])
        self.history = [Bar.parse(value, config["market"], config["interval"]) for value in self.state["history"]]
        if len(self.history) > MAX_HISTORY or (self.history and self.history[-1].open_ms != self.state["cursor"]):
            raise Stop("RUNTIME_CHECKPOINT_INVALID", "Retained history and bar cursor disagree.")
        self.feed = feed or FixtureFeed(config["market"], config["interval"], cursor=self.state["cursor"])
        if hasattr(self.feed, "admission_ready"):
            self.admission.feed_check = self.feed.admission_ready
        if executor_factory:
            self.executor = executor_factory(self.state["execution"], self.commit, self.report, self.admission)
        elif mode == "paper":
            self.executor = PaperExecutor(self.state["execution"], self.commit, self.report, self.admission)
        else:
            self.executor = LimitChaser(self.state["execution"], self.commit, self.report, self.admission, self.worker, adapter, config["execution"])
        self.published = self.state["reportSequence"]
        self.bars_processed = 0
        self.stop_reason = None
        self.pending_batch = None
        self.batch_offset = 0
        self.commit()
        self.executor.recover()
        self.executor.open_risk_paused = lifecycle is not None
        self.release_worker = (release_worker or SerialIO()) if lifecycle else None
        if recover_only:
            self.stop()

    def commit(self):
        self.journal.save_runtime(self.state)
        # Delivery follows a successful durable write. Failed delivery never
        # affects execution or changes a report's stable identity.
        for item in self.state["reports"]:
            if item["sequence"] > getattr(self, "published", 0):
                try:
                    self.reporter.emit(item)
                except Exception:
                    pass
        self.published = self.state["reportSequence"]

    def report(self, status, intent=None, extra=None):
        self.state["reportSequence"] += 1
        execution = self.state["execution"]
        progress = execution_progress(execution)
        report = {"eventId": self.policy[:12] + ":" + str(self.state["reportSequence"]), "sequence": self.state["reportSequence"],
                  "status": status, "mode": self.mode, "intentId": intent["id"] if intent else None,
                  "target": intent["quantity"] if intent else None,
                  "residual": str(number(intent["quantity"], signed=True) - number(progress["position"], signed=True)) if intent and progress["position"] is not None else None,
                  **progress,
                  **(extra or {})}
        self.state["reports"].append(report)
        # The checkpoint is a documented rolling local report window. Active
        # child fill evidence is separate and is never pruned to fit reports.
        self.state["reports"] = self.state["reports"][-MAX_REPORTS:]

    def nominate(self, target, bar, now_ms):
        self.state["latestTarget"] = {"fraction": str(target.fraction), "reason": target.reason, "bar": bar.public(), "createdMs": now_ms}
        budget = number(self.config["execution"]["targetNotional"])
        ready_to_increase = self.lifecycle is None
        if self.lifecycle is not None:
            policy = withdrawal_policy(self.release_read or {}, self.clock() // 1000)
            ready_to_increase = policy.ready_to_increase
            if policy.capital.remaining_usdc is None:
                if target.fraction != 0:
                    return
            else:
                budget = min(budget, policy.capital.remaining_usdc * number(self.config["execution"]["capitalMultiple"]))
            if not policy.ready_to_increase:
                desired = budget * target.fraction / bar.close
                current = number(self.state["execution"]["position"], signed=True)
                if desired != 0 and (current == 0 or desired * current < 0 or abs(desired) >= abs(current)):
                    return
        fraction = str(target.fraction.normalize())
        normalized = fraction + ":" + str(budget.normalize())
        if self.state["normalized"] == normalized and (not ready_to_increase or self.state.get("readyNormalized") == normalized):
            return
        if ready_to_increase:
            self.state["readyNormalized"] = normalized
        state = self.state["execution"]
        active = state["active"]
        size = budget * abs(target.fraction) / bar.close
        quantity = str(size if target.fraction >= 0 else -size)
        if active and active["fraction"] == fraction and active["quantity"] == quantity and self.clock() < active["deadlineMs"]:
            state["pending"] = None
            self.admission.generation = active["id"]
            self.state["normalized"] = normalized
            return
        self.state["decisionSequence"] += 1
        identity = hashlib.sha256((self.policy + ":" + str(bar.open_ms) + ":" + str(self.state["decisionSequence"])).encode()).hexdigest()[:32]
        intent = {"id": identity, "fraction": fraction, "quantity": quantity,
                  "capitalMultiple": self.config["execution"]["capitalMultiple"], "targetNotional": self.config["execution"]["targetNotional"],
                  "arrivalPrice": str(bar.close), "createdMs": now_ms,
                  "deadlineMs": now_ms + self.config["execution"]["chaseSeconds"] * 1000,
                  "market": bar.market, "sourceBar": bar.open_ms, "policy": self.policy,
                  "reason": target.reason}
        if state["pending"]:
            self.report("superseded", state["pending"])
        self.executor.set_target(intent)
        self.state["normalized"] = normalized
        self.report("target_decided", intent)

    def tick(self):
        now_ms = self.clock()
        if self.state["sessionDeadlineMs"] is not None and now_ms >= self.state["sessionDeadlineMs"]:
            self.stop_reason = "SESSION_EXPIRED"
            self.stop()
        try:
            batch = self.pending_batch or self.feed.poll(now_ms)
        except Stop as error:
            self.feed.healthy = False
            self.stop_reason = error.code
            self.admission.stopping.set()
            batch = None
        ready = self.feed.healthy and not self.admission.stopping.is_set()
        if not ready or batch:
            self.admission.feed_ready.clear()
        if batch and not self.admission.stopping.is_set():
            if len(batch.bars) > 512:
                raise Stop("FEED_BATCH_BOUND", "The feed exceeded its bounded batch contract.")
            self.pending_batch = batch
            stop_at = min(len(batch.bars), self.batch_offset + 8)
            for index in range(self.batch_offset, stop_at):
                if self.admission.stopping.is_set():
                    break
                bar = batch.bars[index]
                if not isinstance(bar, Bar):
                    raise Stop("CANDLE_INVALID", "A feed must return validated Bar observations.")
                bar = Bar.parse(bar.public(), self.config["market"], self.config["interval"])
                if self.state["cursor"] is not None and bar.open_ms <= self.state["cursor"]:
                    previous = next((known for known in self.history if known.open_ms == bar.open_ms), None)
                    if previous is not None and previous != bar:
                        raise Stop("COMMITTED_CANDLE_CHANGED", "A finalized bar changed after a durable decision.")
                    continue
                if self.state["cursor"] is not None and bar.open_ms != self.state["cursor"] + INTERVALS[self.config["interval"]]:
                    raise Stop("CANDLE_GAP", "A feed batch skipped an interval; no target is admitted.")
                self.history.append(bar)
                self.history = self.history[-MAX_HISTORY:]
                target, checkpoint = self.strategy.evaluate(self.history, copy.deepcopy(self.state["strategy"]))
                self.state.update(cursor=bar.open_ms, history=[item.public() for item in self.history], strategy=checkpoint)
                self.bars_processed += 1
                if target is not None and batch.trade_last and index == len(batch.bars) - 1 and ready:
                    self.nominate(target, bar, now_ms)
            self.batch_offset = stop_at
            if stop_at >= len(batch.bars):
                self.pending_batch, self.batch_offset = None, 0
            # At most eight strategy updates and one local checkpoint per
            # tick. Cursor and resulting target commit before any effect.
            self.commit()
        if ready and self.pending_batch is None:
            self.admission.feed_ready.set()
        self.apply_release(now_ms)
        self.executor.step(now_ms)

    def apply_release(self, now_ms):
        if self.lifecycle is None or self.admission.stopping.is_set():
            return
        result = self.release_worker.take("release-read")
        if result is not None:
            ok, read = result
            self.release_read = read if ok else None
        # An age below zero means the clock moved back: read again, and treat the last read as stale.
        due = self.release_started is None or not 0 <= now_ms - self.release_started < RELEASE_POLL_MS
        if due and self.release_worker.submit("release-read", lambda: self.read_release(now_ms)):
            self.release_started = now_ms
        read = self.release_read or {}
        policy = withdrawal_policy(read, now_ms // 1000)
        paused = not policy.ready_to_increase
        self.executor.open_risk_paused = paused
        execution = self.state["execution"]
        before = copy.deepcopy((self.state.get("release"), execution, self.state["reportSequence"], self.state["decisionSequence"], self.state["normalized"], self.state.get("readyNormalized")))
        previous = self.state.get("release", {})
        hold_since = previous.get("holdSinceMs") if paused else None
        if paused and hold_since is None:
            hold_since = now_ms
        attention = bool(previous.get("attentionReported")) if paused else False
        if paused and now_ms - hold_since >= 120000 and not attention:
            self.report("withdrawal_attention", execution["active"], {"reason": policy.reason})
            attention = True
        if policy.state == "yielding":
            epoch = execution.get("settlementEpoch", {})
            since = epoch.get("sinceMs", now_ms) if now_ms <= epoch.get("throughMs", now_ms) else now_ms
            execution["settlementEpoch"] = {"sinceMs": since, "throughMs": policy.observed_at * 1000 + RELEASE_STALE_MS}
        entry = {"paused": paused, "observedMs": policy.observed_at * 1000 if policy.observed_at else None,
                 "holdSinceMs": hold_since, "attentionReported": attention, "reason": policy.reason,
                 "signal": read}
        # Rebase the existing intent without extending its authorization or
        # resetting child counts. A fresh signal remains separately retained.
        if policy.capital.remaining_usdc is not None:
            budget = min(number(self.config["execution"]["targetNotional"]),
                         policy.capital.remaining_usdc * number(self.config["execution"]["capitalMultiple"]))
            for key in ("active", "pending"):
                intent = execution[key]
                if intent:
                    intent["quantity"] = str(budget * number(intent["fraction"], signed=True) / number(intent["arrivalPrice"]))
                    intent["capitalMultiple"] = self.config["execution"]["capitalMultiple"]
                    intent["targetNotional"] = self.config["execution"]["targetNotional"]
        self.state["release"] = entry
        latest = self.state.get("latestTarget")
        if (policy.ready_to_increase and latest
                and latest["createdMs"] + self.config["execution"]["chaseSeconds"] * 1000 > now_ms):
            self.nominate(Target(number(latest["fraction"], signed=True), latest["reason"]),
                          Bar.parse(latest["bar"], self.config["market"], self.config["interval"]), latest["createdMs"])
        after = (self.state["release"], execution, self.state["reportSequence"], self.state["decisionSequence"], self.state["normalized"], self.state.get("readyNormalized"))
        if before != after:
            self.commit()

    def read_release(self, started_ms):
        """Runs on the release worker and touches no runtime state."""
        return self.lifecycle(started_ms // 1000)

    def stop(self):
        self.admission.stopping.set()
        self.admission.feed_ready.clear()
        self.executor.shutdown()

    def result(self):
        execution = self.state["execution"]
        return {"schema": "2n20-runner-result-v1", "stage": "runtime_stopped", "mode": self.mode,
                "simulated": self.mode == "paper", "barsProcessed": self.bars_processed,
                "cursor": self.state["cursor"], "phase": execution["phase"], **execution_progress(execution),
                "reason": self.stop_reason,
                "ordersSubmitted": 0 if self.mode == "paper" else "see retained child and public evidence",
                "signerLoaded": False if self.mode == "paper" else "only for authorized effects",
                "stateFile": self.journal.public_path("runtime.public.json"),
                "limitations": "Paper fills are fictional. Public feeds and mocks do not prove live fill quality. Shutdown does not flatten positions."}


def drive(runtime, max_bars=None, max_seconds=None):
    started = time.monotonic()
    old_handlers = {}
    def stop_signal(signum, frame):
        runtime.stop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, stop_signal)
        while not runtime.admission.stopping.is_set():
            runtime.tick()
            if runtime.feed.done or max_bars is not None and runtime.bars_processed >= max_bars or max_seconds is not None and time.monotonic() - started >= max_seconds:
                break
            runtime.admission.stopping.wait(0.1)
    finally:
        runtime.stop()
        # The signal handler may interrupt submit() while it holds the release
        # queue's lock, so the worker closes here rather than in stop().
        if runtime.release_worker is not None:
            runtime.release_worker.close()
        runtime.feed.close()
        # Retain any request still in flight after the cleanup budget. Do not
        # clear the child or account reservation because a thread timed out.
        cleanup_ms = runtime.state.get("cleanupDeadlineMs")
        cleanup_seconds = min(60, max(0, (cleanup_ms - runtime.clock()) / 1000)) if cleanup_ms else 10
        cleanup_until = time.monotonic() + cleanup_seconds
        while runtime.mode == "live" and runtime.state["execution"]["child"] and time.monotonic() < cleanup_until:
            getattr(runtime.feed, "drain", lambda: None)()
            runtime.executor.step(runtime.clock())
            time.sleep(0.1)
        getattr(runtime.feed, "drain", lambda: None)()
        runtime.commit()
        getattr(runtime.reporter, "close", lambda: None)()
        if isinstance(runtime.worker, SerialIO):
            runtime.worker.close(finalizer=getattr(runtime.feed, "dispose", None))
        else:
            runtime.worker.close()
        if isinstance(runtime.release_worker, SerialIO):
            runtime.release_worker.thread.join(RELEASE_JOIN_SECONDS)
        for sig, previous in old_handlers.items():
            signal.signal(sig, previous)
    return runtime.result()


def rearm_session(journal, config, adapter, seconds, recover_only=False, clock=None):
    """Explicit lifecycle flags, never an automatic renewal on resume."""
    now = (clock or (lambda: int(time.time() * 1000)))()
    state = journal.read_runtime()
    if not state or state.get("mode") != "live":
        raise Stop("RUNTIME_RECOVERY_MISSING", "Use the retained existing live state directory.")
    execution = state["execution"]
    if recover_only:
        if execution["child"] is None:
            raise Stop("NO_OWNED_CHILD", "There is no retained child to cancel. Review --new-session for another trading session.")
        # Existing submission IDs, plans, targets and expired session deadline
        # stay unchanged. This flag authorizes a new cleanup-only deadline.
        state["cleanupDeadlineMs"] = now + seconds * 1000
    else:
        if state["policy"] != fingerprint(config):
            raise Stop("RUNTIME_POLICY_CHANGED", "New-session cannot migrate changed strategy/configuration with retained exposure.")
        if execution["child"] is not None:
            raise Stop("OWNED_CHILD_UNRESOLVED", "Use --recover-only with the original state before authorizing another session.")
        observed = adapter.observe(config["market"], copy.deepcopy(execution))
        if number(observed["position"], signed=True) != number(execution["position"], signed=True) or observed["snapshot"]["orders"]:
            raise Stop("OWNERSHIP_CONFLICT", "The retained position and account activity must be verified before rearming.")
        # Capture deadlines only after verification. Each explicit rearm is a
        # reviewed authorization, not replay of an earlier signal.
        now = (clock or (lambda: int(time.time() * 1000)))()
        state["sessionDeadlineMs"] = now + seconds * 1000
        state["cleanupDeadlineMs"] = state["sessionDeadlineMs"] + 60000
        for key in ("active", "pending"):
            if execution[key]:
                execution[key]["createdMs"] = now
                execution[key]["deadlineMs"] = now + config["execution"]["chaseSeconds"] * 1000
        execution.update(phase="ready", error=None, childCount=0)
    journal.save_runtime(state)


def release_reader(scope):
    """Read the vault's release state through a fresh Rpc each time.

    Each read gets the whole retry allowance, so a slow read cannot spend the
    next one's. The CLI's commands keep one Rpc, and one allowance, per run.
    """
    from twon20.rpc import Rpc
    from twon20.withdrawals import poll_pending_withdrawals
    return lambda now: poll_pending_withdrawals(scope, Rpc("mainnet"), now)


def run_command(args):
    config = load_config(args.config)
    if args.max_bars is not None and not 1 <= args.max_bars <= 100000:
        raise Stop("INVALID_RUN_BOUND", "Choose max-bars from 1 to 100000.")
    if args.max_seconds is not None and not 1 <= args.max_seconds <= 86400:
        raise Stop("INVALID_RUN_BOUND", "Choose max-seconds from 1 to 86400.")
    if args.mode == "live" and (not args.vault or not args.directory or args.max_seconds is None or args.feed != "public"):
        raise Stop("LIVE_RUNTIME_SCOPE_REQUIRED", "Live preview needs --vault, --directory, --max-seconds and --feed public.")
    if args.mode == "paper" and (args.execute or args.new_session or args.recover_only):
        raise Stop("MODE_CONFLICT", "Paper mode does not accept live execution authorization.")
    from contextlib import ExitStack
    with ExitStack() as stack:
        adapter = None
        scope = {"mode": "paper", "market": config["market"], "interval": config["interval"], "feed": args.feed}
        guard = None
        if args.mode == "live":
            from adapter import Access
            from journal import account_lock
            from runtime_adapter import RuntimeAdapter
            access = Access(args.vault, args.directory)
            stack.callback(access.close)
            adapter = RuntimeAdapter(access)
            if not args.execute:
                snapshot = adapter.snapshot(config["market"])
                validate_market(config, snapshot)
                capital = withdrawal_policy(access.pending_withdrawals())
                return {"schema": "2n20-runner-result-v1", "stage": "runtime_preview", "scope": access.scope,
                        "config": config, "sizeDecimals": snapshot["decimals"], "sessionSeconds": args.max_seconds, "ownedCleanupGraceSeconds": 60,
                        "newSession": args.new_session, "recoverOnly": args.recover_only, "ordersSubmitted": 0, "signerLoaded": False,
                        "accountHasConflicts": bool(snapshot["positions"] or snapshot["orders"]),
                        "capital": {"state": capital.state, "reason": capital.reason,
                                    "remainingUsdc": str(capital.capital.remaining_usdc) if capital.capital.remaining_usdc is not None else None,
                                    "readyToIncrease": capital.ready_to_increase},
                        "requiredHumanAction": ("Review these limits and then explicitly use --execute LIVE_RUNTIME. Reuse the same state directory."
                                                if capital.ready_to_increase else
                                                "Capital is not ready for new risk. Resolve the reported reason and repeat this preview before authorizing execution.")}, 0
            scope = access.scope
            guard = stack.enter_context(account_lock(scope["tradingAccount"]))
        journal = RuntimeJournal(Path(args.state_directory or (".runtime-" + args.mode + "-" + args.feed)).expanduser().absolute(), scope)
        stack.callback(journal.close)
        if guard:
            guard.reserve(journal, scope)
            for name in ("smoke.public.json", "collateral.public.json"):
                old = journal.read(name)
                if old and old.get("phase") not in {"complete", "entry_rejected", "human_acknowledged"}:
                    raise Stop("OTHER_INTENT_UNRESOLVED", "Reconcile the existing smoke or collateral intent before starting a runtime.")
        if args.new_session or args.recover_only:
            rearm_session(journal, config, adapter, args.max_seconds, args.recover_only)
        worker = SerialIO()
        stack.callback(worker.close)
        reporter = TelegramReporter() if config["reporting"]["telegram"] else ConsoleReporter()
        stack.callback(reporter.close)
        lifecycle = release_reader(access.scope) if args.mode == "live" and args.execute else None
        runtime = Runtime(config, journal, args.mode, worker, adapter, reporter, session_seconds=args.max_seconds if args.mode == "live" else None, recover_only=args.recover_only, lifecycle=lifecycle)
        if args.feed == "public" and not args.recover_only:
            runtime.feed = HyperliquidCandleFeed(worker, config["market"], config["interval"], runtime.history)
            runtime.admission.feed_check = runtime.feed.admission_ready
        result = drive(runtime, args.max_bars, args.max_seconds)
        # Continuous runtime ownership survives shutdown even when currently
        # flat. Its activity baseline and future targets cannot be handed to a
        # different state directory without an explicit reviewed recovery.
        return result, 2 if result["unresolvedChild"] or result["reason"] else 0
