"""Foreground composition and one atomic owner for decisions and execution state."""
import copy
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from pathlib import Path
import signal
import time

from configuration import load_config
from executor import LimitChaser, PaperExecutor, execution_state
from feed import FixtureFeed, HyperliquidCandleFeed
from models import Bar, INTERVALS, validate_market
from reporting import ConsoleReporter, TelegramReporter
from risk import Stop, number
from runtime_io import Admission, SerialIO
from runtime_journal import MAX_HISTORY, MAX_REPORTS, RuntimeJournal
from strategy import BandBreakout


def fingerprint(config):
    # Source changes to the replaceable strategy require a reviewed migration,
    # not silently restoring state into a different implementation.
    source = Path(__file__).with_name("strategy.py").read_bytes()
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode() + source).hexdigest()


class Runtime:
    """Tick budget: no network waits, <=8 bars, one executor transition.

    The foreground loop waits at most 100 ms between stop checks. The shared
    daemon worker may still be in an SDK request after a bounded shutdown
    grace. That leaves a durable unresolved identity, never a canceled request.
    """
    def __init__(self, config, journal, mode="paper", worker=None, adapter=None, reporter=None,
                 feed=None, strategy=None, executor_factory=None, session_seconds=None, clock=None, recover_only=False):
        self.config, self.journal, self.mode = config, journal, mode
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
        filled = number(execution["filledSize"])
        report = {"eventId": self.policy[:12] + ":" + str(self.state["reportSequence"]), "sequence": self.state["reportSequence"],
                  "status": status, "mode": self.mode, "intentId": intent["id"] if intent else None,
                  "position": execution["position"], "target": intent["quantity"] if intent else None,
                  "residual": str(number(intent["quantity"], signed=True) - number(execution["position"], signed=True)) if intent else None,
                  "filledSize": str(filled), "vwap": str(number(execution["filledValue"]) / filled) if filled else None,
                  "feesUsdc": execution["fees"], "makerFills": execution["makerFills"], "takerFills": execution["takerFills"],
                  **(extra or {})}
        self.state["reports"].append(report)
        # The checkpoint is a documented rolling local report window. Active
        # child fill evidence is separate and is never pruned to fit reports.
        self.state["reports"] = self.state["reports"][-MAX_REPORTS:]

    def nominate(self, target, bar, now_ms):
        normalized = str(target.fraction.normalize())
        if self.state["normalized"] == normalized:
            return
        state = self.state["execution"]
        active = state["active"]
        if active and active["fraction"] == normalized:
            state["pending"] = None
            self.admission.generation = active["id"]
            self.state["normalized"] = normalized
            return
        size = (number(self.config["execution"]["targetNotional"]) * abs(target.fraction) / bar.close).quantize(
            Decimal(1).scaleb(-self.config["sizeDecimals"]), rounding=ROUND_DOWN)
        self.state["decisionSequence"] += 1
        identity = hashlib.sha256((self.policy + ":" + str(bar.open_ms) + ":" + str(self.state["decisionSequence"])).encode()).hexdigest()[:32]
        intent = {"id": identity, "fraction": normalized, "quantity": str(size if target.fraction >= 0 else -size),
                  "arrivalPrice": str(bar.close), "createdMs": now_ms,
                  "deadlineMs": now_ms + self.config["execution"]["chaseSeconds"] * 1000,
                  "market": bar.market, "sourceBar": bar.open_ms, "policy": self.policy,
                  "sizeDecimals": self.config["sizeDecimals"], "reason": target.reason}
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
        if ready:
            self.admission.feed_ready.set()
        else:
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
        self.executor.step(now_ms)

    def stop(self):
        self.admission.stopping.set()
        self.admission.feed_ready.clear()
        self.executor.shutdown()

    def result(self):
        execution = self.state["execution"]
        return {"schema": "2n20-runner-result-v1", "stage": "runtime_stopped", "mode": self.mode,
                "simulated": self.mode == "paper", "barsProcessed": self.bars_processed,
                "cursor": self.state["cursor"], "phase": execution["phase"], "position": execution["position"],
                "unresolvedChild": bool(execution["child"]), "reason": self.stop_reason,
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
                return {"schema": "2n20-runner-result-v1", "stage": "runtime_preview", "scope": access.scope,
                        "config": config, "sessionSeconds": args.max_seconds, "ownedCleanupGraceSeconds": 60,
                        "newSession": args.new_session, "recoverOnly": args.recover_only, "ordersSubmitted": 0, "signerLoaded": False,
                        "accountHasConflicts": bool(snapshot["positions"] or snapshot["orders"]),
                        "requiredHumanAction": "Review these limits and then explicitly use --execute LIVE_RUNTIME. Reuse the same state directory."}, 0
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
        runtime = Runtime(config, journal, args.mode, worker, adapter, reporter, session_seconds=args.max_seconds if args.mode == "live" else None, recover_only=args.recover_only)
        if args.feed == "public" and not args.recover_only:
            runtime.feed = HyperliquidCandleFeed(worker, config["market"], config["interval"], runtime.history)
            runtime.admission.feed_check = runtime.feed.admission_ready
        result = drive(runtime, args.max_bars, args.max_seconds)
        # Continuous runtime ownership survives shutdown even when currently
        # flat. Its activity baseline and future targets cannot be handed to a
        # different state directory without an explicit reviewed recovery.
        return result, 2 if result["unresolvedChild"] or result["reason"] else 0
