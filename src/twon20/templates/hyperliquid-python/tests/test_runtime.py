"""Disposable runtime failure traces. No signing, orders or Telegram delivery."""
import copy
import io
import json
import tempfile
import time
from decimal import Decimal
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from executor import LimitChaser, execution_state, plan_child
from feed import FixtureFeed, HyperliquidCandleFeed
from models import Bar, BarBatch, Target
from risk import Stop
from configuration import load_config
from runtime import Runtime
from runtime_adapter import RuntimeAdapter
from runtime_io import Admission, SerialIO
from runtime_journal import MAX_BYTES, RuntimeJournal
from strategy import BandBreakout

ROOT = Path(__file__).resolve().parents[1]


def bar(index, price="100"):
    price = Decimal(price)
    return Bar("BTC", "1m", index * 60000, (index + 1) * 60000 - 1, price, price, price, price, Decimal(1))


def config():
    return load_config(ROOT / "config.public.example.json")


def snapshot(position="0"):
    size = Decimal(position)
    return {"market": "BTC", "decimals": 5, "mark": Decimal(100), "bid": Decimal("99.9"), "ask": Decimal("100.1"),
            "positions": {"BTC": size} if size else {}, "orders": [], "perp_available": Decimal(10000),
            "taker_fee": Decimal("0.00045"), "bidDepth": Decimal(1000), "askDepth": Decimal(1000), "dayNotionalVolume": Decimal(1000000)}


def intent(identity="one", fraction="1", quantity="1", now=1000):
    return {"id": identity, "fraction": fraction, "quantity": quantity, "arrivalPrice": "100", "createdMs": now,
            "deadlineMs": now + 60000, "market": "BTC", "sourceBar": 0, "policy": "test", "reason": "test"}


class MemoryJournal:
    scope = {"mode": "paper"}
    def __init__(self): self.value, self.saved = None, []
    def read_runtime(self): return copy.deepcopy(self.value)
    def save_runtime(self, value):
        self.value = copy.deepcopy(value)
        self.saved.append(copy.deepcopy(value))
    def public_path(self, name): return "/disposable/" + name


class ImmediateIO:
    def __init__(self): self.token, self.result = None, None
    @property
    def idle(self): return self.token is None
    def submit(self, token, function):
        if not self.idle: return False
        self.token = token
        try: self.result = (True, function())
        except Stop as error: self.result = (False, error.code)
        return True
    def take(self, token):
        if token != self.token: return None
        result, self.token, self.result = self.result, None, None
        return result
    def close(self): pass


class FakeAdapter:
    def __init__(self):
        self.calls, self.position, self.fill_size = [], Decimal(0), None
        self.canceled, self.delay_fill, self.foreign = False, False, False
        self.order = None
        self.unknown = False
        self.classification = "accepted_unverified"

    def submit_child(self, child, state, target, admission):
        admission.check(target["id"], target["deadlineMs"])
        self.calls.append(("submit", copy.deepcopy(child)))
        self.order = copy.deepcopy(child)
        self.canceled = False
        return {"classification": self.classification}

    def cancel_child(self, child, state, target, admission):
        admission.check(target["id"], target["deadlineMs"], cleanup=True)
        self.calls.append(("cancel", child["oid"]))
        self.canceled = True
        return {"classification": "cancel_submitted"}

    def observe(self, market, state):
        if self.foreign:
            raise Stop("ACCOUNT_CONFLICT", "Disposable unowned order")
        child = state["child"]
        evidence = {"known": child is not None and not self.unknown, "terminal": False, "complete": False,
                    "consistent": True, "filled": "0", "value": "0", "fees": "0", "maker": 0, "taker": 0, "fills": []}
        if child and not self.unknown:
            fill = self.fill_size if self.fill_size is not None else Decimal(0)
            evidence.update(oid=10 + state["sequence"], filled=str(fill), value=str(fill * 100), terminal=self.canceled)
            if fill:
                evidence["fills"] = [{"oid": evidence["oid"], "tid": 1, "time": 1001, "coin": "BTC", "size": str(fill), "price": "100", "fee": "0", "crossed": False}]
                evidence["maker"] = 1
            self.position = Decimal(child["basePosition"]) + fill * (1 if child["plan"]["buy"] else -1)
            evidence["consistent"] = not self.delay_fill
            evidence["complete"] = self.canceled and not self.delay_fill
        return {"snapshot": snapshot(str(self.position)), "evidence": evidence, "position": str(self.position)}


class StrategyRuntimeTests(unittest.TestCase):
    def runtime(self, journal=None, strategy=None, feed=None):
        return Runtime(config(), journal or MemoryJournal(), worker=ImmediateIO(), reporter=Mock(),
                       strategy=strategy, feed=feed, clock=lambda: 1000)

    def test_band_component_breakouts_and_mean_crossing(self):
        strategy = BandBreakout(5, "1")
        bars, state, signals = [], {}, []
        for i, price in enumerate([100, 100, 100, 100, 110, 112, 90, 89, 88]):
            bars.append(bar(i, str(price)))
            target, state = strategy.evaluate(bars, state)
            signals.append(None if target is None else target.fraction)
        self.assertEqual(signals[4], 1)
        self.assertEqual(signals[6], 0)
        self.assertEqual(signals[7], -1)

    def test_repeated_normalized_target_freezes_quantity_through_restart(self):
        journal = MemoryJournal()
        runtime = self.runtime(journal)
        runtime.nominate(Target(Decimal(1), "test"), bar(0, "100"), 1000)
        runtime.commit()
        first = copy.deepcopy(runtime.state["execution"]["pending"])
        restored = self.runtime(journal)
        restored.nominate(Target(Decimal("1.0"), "test"), bar(1, "200"), 2000)
        self.assertEqual(restored.state["execution"]["pending"], first)

    def test_cursor_strategy_and_target_saved_atomically(self):
        strategy = Mock()
        strategy.evaluate.return_value = (Target(Decimal(1), "test"), {"fraction": "1"})
        runtime = self.runtime(strategy=strategy, feed=FixtureFeed(closes=[100]))
        runtime.tick()
        decisions = [value for value in runtime.journal.saved if value["cursor"] is not None]
        self.assertTrue(decisions)
        self.assertIsNotNone(decisions[0]["execution"]["pending"])
        self.assertEqual(decisions[0]["strategy"], {"fraction": "1"})

    def test_history_replay_does_not_trade(self):
        strategy = Mock()
        strategy.evaluate.return_value = (Target(Decimal(1), "test"), {"fraction": "1"})
        feed = Mock(healthy=True)
        feed.poll.return_value = BarBatch(tuple(bar(i) for i in range(10)), False)
        runtime = self.runtime(strategy=strategy, feed=feed)
        runtime.tick()
        self.assertIsNone(runtime.state["execution"]["active"])
        runtime.tick()
        self.assertEqual(runtime.state["cursor"], bar(9).open_ms)

    def test_duplicate_decisions_are_not_replayed_and_conflicts_stop(self):
        feed = Mock(healthy=True)
        feed.poll.return_value = BarBatch((bar(0),), True)
        runtime = self.runtime(feed=feed)
        runtime.tick(); runtime.tick()
        self.assertEqual(runtime.bars_processed, 1)
        feed.poll.return_value = BarBatch((bar(0, "101"),), True)
        with self.assertRaises(Stop): runtime.tick()

    def test_changed_policy_refuses_restore(self):
        journal = MemoryJournal()
        self.runtime(journal)
        changed = config(); changed["strategy"]["window"] = 30
        with self.assertRaises(Stop):
            Runtime(changed, journal, worker=ImmediateIO(), reporter=Mock())

    def test_paper_never_constructs_access(self):
        with patch("adapter.Access", side_effect=AssertionError()), patch("adapter.sdk_types", side_effect=AssertionError()):
            runtime = self.runtime()
            for _ in range(33): runtime.tick()
        self.assertTrue(runtime.result()["simulated"])
        self.assertFalse(runtime.result()["signerLoaded"])

    def test_report_failure_cannot_repeat_trade_and_reports_are_bounded(self):
        runtime = self.runtime()
        runtime.reporter.emit.side_effect = RuntimeError()
        for index in range(100): runtime.report("test", extra={"counter": index})
        runtime.commit()
        self.assertEqual(len(runtime.state["reports"]), 64)


class FeedTests(unittest.TestCase):
    def feed(self, history=()):
        self.now = 60 * 60000 + 5000
        self.worker = ImmediateIO()
        return HyperliquidCandleFeed(self.worker, history=history, clock=lambda: self.now)

    def test_rest_ws_overlap_deduplicates_and_recovery_is_not_tradable(self):
        feed = self.feed([bar(58)])
        feed.callback({"channel": "candle", "data": bar(59, "101").public()})
        result = feed._merge([bar(58).public(), bar(59, "101").public(), bar(60).public()], self.now, True, feed.repair_generation)
        self.assertEqual([item.open_ms for item in result.bars], [bar(59).open_ms])
        self.assertFalse(result.trade_last)
        self.assertTrue(feed.healthy)
        self.assertEqual(feed._merge([bar(59, "101").public()], self.now, False, feed.repair_generation).bars, ())

    def test_gap_and_committed_revision_fail_closed(self):
        feed = self.feed([bar(57)])
        with self.assertRaises(Stop): feed._merge([bar(59).public()], self.now, True, 0)
        feed = self.feed([bar(58)])
        with self.assertRaises(Stop) as failure:
            feed._merge([bar(58, "999").public(), bar(59).public()], self.now, False, 0)
        self.assertEqual(failure.exception.code, "COMMITTED_CANDLE_CHANGED")

    def test_overflow_is_sticky_even_if_repair_response_arrives(self):
        feed = self.feed([bar(58)])
        feed.CAPACITY = 2
        generation = feed.repair_generation
        for index in (57, 58, 59): feed.callback({"channel": "candle", "data": bar(index).public()})
        result = feed._merge([bar(59).public()], self.now, False, generation)
        self.assertFalse(feed.healthy)
        self.assertTrue(feed.repair.is_set())
        self.assertFalse(result.trade_last)

    def test_callback_data_single_dict_and_bad_schema_latches_repair(self):
        feed = self.feed()
        feed.repair.clear()
        feed.callback({"channel": "candle", "data": [bar(59).public()]})
        self.assertTrue(feed.repair.is_set())
        feed.callback({"channel": "candle", "data": bar(59).public()})
        self.assertEqual(feed.messages_received, 1)

    def test_elapsed_close_and_grace_exclude_forming_bar(self):
        feed = self.feed([bar(58)])
        self.now = 60 * 60000 + 1000
        result = feed._merge([bar(59).public()], self.now, True, 0)
        self.assertEqual(result.bars, ())
        self.assertEqual(feed.cursor, bar(58).open_ms)

    def test_silence_closes_admission_while_other_worker_is_busy(self):
        feed = self.feed([bar(59)])
        feed.healthy = True; feed.repair.clear()
        self.worker.token = "executor-submit"
        self.now += 121000
        self.assertFalse(feed.admission_ready())
        self.assertTrue(feed.repair.is_set())

    def test_dead_socket_is_recreated_and_backfill_is_nontradable(self):
        feed = self.feed([bar(58)])
        old = Mock(); old.ws_manager.is_alive.return_value = False
        new = Mock(); new.ws_manager.is_alive.return_value = True
        new.candles_snapshot.return_value = [bar(58).public(), bar(59).public()]
        feed.info = old
        feed.factory = Mock(return_value=new)
        feed.poll(self.now); feed.poll(self.now)
        result = feed.poll(self.now)
        self.assertEqual(feed.socket_generation, 1)
        old.disconnect_websocket.assert_called_once()
        self.assertFalse(result.trade_last)
        self.assertTrue(feed.healthy)


class ChaserTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.state = execution_state(self.now)
        self.saved, self.reports = [], []
        self.worker, self.adapter = ImmediateIO(), FakeAdapter()
        self.admission = Admission(lambda: self.now)
        self.admission.feed_ready.set()
        self.chaser = LimitChaser(self.state, lambda: self.saved.append(copy.deepcopy(self.state)),
                                 lambda *args: self.reports.append(args), self.admission, self.worker, self.adapter, config()["execution"])
        self.chaser.set_target(intent())

    def steps(self, count=1, advance=100):
        for _ in range(count):
            self.chaser.step(self.now)
            self.now += advance

    def open_child(self):
        self.steps(5)
        self.assertIsNotNone(self.state["child"])
        self.assertIsNotNone(self.state["child"]["oid"])

    def test_child_identity_is_durable_before_submission(self):
        self.steps(3)
        submission = [value for value in self.saved if value["phase"] == "submission_uncertain"]
        self.assertTrue(submission)
        self.assertEqual(self.adapter.calls[0][1]["cloid"], submission[0]["child"]["cloid"])
        self.assertEqual(self.adapter.calls[0][1]["plan"]["tif"], "Alo")

    def test_partial_cancel_waits_for_complete_fills_across_restart(self):
        self.open_child()
        self.adapter.fill_size = Decimal("0.1")
        self.adapter.delay_fill = True
        self.chaser.set_target(intent("new", "-1", "-1"))
        self.steps(8, 500)
        old_id = self.state["child"]["cloid"]
        self.assertEqual(len([call for call in self.adapter.calls if call[0] == "submit"]), 1)
        self.chaser = LimitChaser(self.state, lambda: self.saved.append(copy.deepcopy(self.state)), lambda *args: self.reports.append(args),
                                 self.admission, ImmediateIO(), self.adapter, config()["execution"])
        self.chaser.recover()
        self.steps(3, 500)
        self.assertEqual(self.state["child"]["cloid"], old_id)
        self.adapter.delay_fill = False
        self.steps(8, 500)
        submissions = [call[1] for call in self.adapter.calls if call[0] == "submit"]
        self.assertGreaterEqual(len(submissions), 2)
        self.assertTrue(submissions[1]["plan"]["reduceOnly"])
        self.assertLessEqual(Decimal(submissions[1]["plan"]["size"]), Decimal("0.1"))

    def test_unknown_submission_never_replaced_by_pending_target(self):
        self.adapter.unknown = True
        self.steps(6, 1000)
        original = self.state["child"]["cloid"]
        self.chaser.set_target(intent("new", "-1", "-1"))
        self.steps(8, 1000)
        self.assertEqual(self.state["child"]["cloid"], original)
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.state["pending"]["id"], "new")

    def test_feed_failure_allows_exact_owned_cancel_but_no_replacement(self):
        self.open_child()
        self.admission.feed_ready.clear()
        self.steps(12, 500)
        self.assertEqual([call[0] for call in self.adapter.calls], ["submit", "cancel"])
        self.assertEqual(self.state["position"], "0")
        self.assertIsNone(self.state["child"])

    def test_shutdown_drains_feed_result_before_owned_child_cleanup(self):
        self.open_child()
        feed = HyperliquidCandleFeed(self.worker, clock=lambda: self.now)
        feed.info = Mock()
        feed.job = "feed-snapshot"
        self.assertTrue(self.worker.submit(feed.job, lambda: []))
        self.chaser.shutdown()
        feed.close()
        self.chaser.step(self.now)
        self.assertEqual([call[0] for call in self.adapter.calls], ["submit"])
        feed.drain()
        self.steps(8, 500)
        self.assertEqual([call[0] for call in self.adapter.calls], ["submit", "cancel"])
        self.assertIsNone(self.state["child"])

    def test_stop_cancels_known_open_child_even_with_lagging_fill_coverage(self):
        self.open_child()
        self.adapter.fill_size = Decimal("0.1")
        self.adapter.delay_fill = True
        self.steps(3, 500)
        self.chaser.shutdown()
        self.steps(10, 500)
        self.assertEqual([call[0] for call in self.adapter.calls], ["submit", "cancel"])
        self.assertIsNotNone(self.state["child"])
        self.assertEqual(self.state["child"]["phase"], "evidence_pending")

    def test_definite_preflight_failure_and_alo_rejection_can_reconcile_and_retry(self):
        for classification in ("not_submitted", "rejected"):
            self.setUp()
            self.adapter.classification = classification
            self.steps(3)
            self.assertIsNone(self.state["child"])
            self.adapter.classification = "accepted_unverified"
            self.steps(15)
            ids = [call[1]["cloid"] for call in self.adapter.calls if call[0] == "submit"]
            self.assertEqual(len(set(ids)), len(ids))
            self.assertGreaterEqual(len(ids), 2)

    def test_foreign_orders_stop_without_cancel(self):
        self.adapter.foreign = True
        self.steps(5, 1000)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.state["error"], "ACCOUNT_CONFLICT")

    def test_chase_minimum_is_not_uplifted(self):
        limits = config()["execution"]; limits["childNotional"] = "9"
        self.assertIsNone(plan_child(intent(), self.state, snapshot(), limits, 1000))
        limits["childNotional"] = "25"; limits["depthFraction"] = "0.001"
        self.assertIsNone(plan_child(intent(), self.state, snapshot(), limits, 1000))

    def test_price_bounds_and_optional_ioc(self):
        limits = config()["execution"]
        current = snapshot(); current.update(bid=Decimal(110), ask=Decimal(111), mark=Decimal(110))
        with self.assertRaises(Stop): plan_child(intent(), self.state, current, limits, 1000)
        limits["iocAfterSeconds"] = 3
        self.assertEqual(plan_child(intent(), self.state, snapshot(), limits, 5000)["tif"], "Ioc")

    def test_fill_of_one_child_does_not_complete_whole_target(self):
        self.open_child()
        self.adapter.fill_size = Decimal(self.state["child"]["plan"]["size"])
        self.adapter.canceled = True
        self.steps(8, 500)
        self.assertNotEqual(self.state["position"], "1")
        self.assertGreaterEqual(len([call for call in self.adapter.calls if call[0] == "submit"]), 2)

    def test_side_flip_opens_only_after_verified_zero_checkpoint(self):
        self.state["position"] = "0.1"
        self.adapter.position = Decimal("0.1")
        self.chaser.set_target(intent("short", "-1", "-1"))
        self.open_child()
        self.adapter.fill_size = Decimal("0.1")
        self.adapter.canceled = True
        self.steps(8, 500)
        submissions = [call[1] for call in self.adapter.calls if call[0] == "submit"]
        self.assertTrue(submissions[0]["plan"]["reduceOnly"])
        self.assertEqual(submissions[0]["plan"]["size"], "0.10000")
        self.assertFalse(submissions[1]["plan"]["reduceOnly"])
        self.assertEqual(submissions[1]["basePosition"], "0.0")
        self.assertTrue(any(item["child"] is None and Decimal(item["position"]) == 0 for item in self.saved))

    def test_shutdown_does_not_flatten_owned_position(self):
        self.state["position"] = "1"
        self.chaser.shutdown()
        self.steps(5)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.state["position"], "1")


class SchedulerJournalTests(unittest.TestCase):
    def test_slow_worker_stays_single_and_stop_revokes_late_effect(self):
        worker, entered, release = SerialIO(), Event(), Event()
        admission = Admission(lambda: 1000)
        admission.feed_ready.set(); admission.generation = "one"
        writes = []
        def slow():
            entered.set(); release.wait(2)
            admission.check("one", 60000)
            writes.append("order")
        self.assertTrue(worker.submit("slow", slow))
        self.assertTrue(entered.wait(1))
        self.assertFalse(worker.submit("another", lambda: writes.append("bad")))
        admission.stopping.set()
        start = time.monotonic()
        self.assertIsNone(worker.take("slow"))
        self.assertLess(time.monotonic() - start, 0.05)
        release.set()
        for _ in range(100):
            result = worker.take("slow")
            if result: break
            time.sleep(0.001)
        self.assertEqual(result, (False, "ADMISSION_CLOSED"))
        self.assertEqual(writes, [])
        worker.close()

    def test_feed_stop_and_result_drain_never_wait_for_sdk_disconnect(self):
        worker = ImmediateIO()
        feed = HyperliquidCandleFeed(worker)
        client = Mock()
        client.disconnect_websocket.side_effect = AssertionError("No foreground SDK shutdown")
        feed.info = client
        feed.job = "feed-connect"
        worker.submit(feed.job, lambda: client)
        feed.close()
        feed.drain()
        client.disconnect_websocket.assert_not_called()
        self.assertTrue(worker.idle)

    def test_session_expiry_stops_new_orders_but_retained_cleanup_has_grace(self):
        admission = Admission(lambda: 2000, 1500, 3000)
        admission.feed_ready.set(); admission.generation = "one"
        with self.assertRaises(Stop): admission.check("one", 60000)
        admission.check("one", 60000, cleanup=True)
        admission.clock = lambda: 3000
        with self.assertRaises(Stop): admission.check("one", 60000, cleanup=True)

    def test_atomic_journal_roundtrip_over_16k_and_oversized_save_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = RuntimeJournal(Path(temporary).resolve() / "state", {"mode": "paper"})
            try:
                runtime = Runtime(config(), journal, worker=ImmediateIO(), reporter=Mock())
                runtime.state["history"] = [bar(index).public() for index in range(128)]
                runtime.state["cursor"] = bar(127).open_ms
                runtime.commit()
                self.assertGreater((journal.output.path / "runtime.public.json").stat().st_size, 16384)
                old = journal.read_runtime()
                runtime.state["oversized"] = "x" * MAX_BYTES
                with self.assertRaises(Stop): runtime.commit()
                self.assertEqual(journal.read_runtime(), old)
            finally: journal.close()

    def test_checkpoint_failure_prevents_dispatch(self):
        state = execution_state(1000)
        worker, adapter = ImmediateIO(), FakeAdapter()
        admission = Admission(lambda: 1000); admission.feed_ready.set()
        def failure(): raise OSError("disposable failed atomic write")
        chaser = LimitChaser(state, failure, Mock(), admission, worker, adapter, config()["execution"])
        chaser.set_target(intent())
        with self.assertRaises(OSError): chaser.step(1000)
        self.assertEqual(adapter.calls, [])




class AdapterEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = 2000
        self.info = Mock()
        self.adapter = RuntimeAdapter(SimpleNamespace(scope={"tradingAccount": "disposable"}), self.info)
        self.child = {"cloid": "0x" + "1" * 32, "startedMs": 1000, "basePosition": "0", "oid": 11,
                      "plan": {"market": "BTC", "buy": True, "size": "0.25", "price": "100", "reduceOnly": False,
                               "decimals": 5, "maxNotional": "25", "slippageBps": 50, "tif": "Alo"}}
        self.state = execution_state(1000)
        self.state["child"] = self.child
        self.order = {"cloid": self.child["cloid"], "coin": "BTC", "side": "B", "origSz": "0.25", "sz": "0.15",
                      "limitPx": "100", "reduceOnly": False, "oid": 11, "timestamp": 1000}
        self.info.query_order_by_cloid.return_value = {"status": "order", "order": {"order": self.order, "status": "canceled"}}
        self.fill = {"oid": 11, "tid": 7, "time": 1500, "coin": "BTC", "side": "B", "sz": "0.1", "px": "100",
                     "fee": "0.002", "feeToken": "USDC", "crossed": False}
        self.info.user_fills_by_time.return_value = [self.fill]
        self.adapter.snapshot = Mock(return_value=snapshot("0.1"))
        self.time_patch = patch("runtime_adapter.time.time", return_value=2)
        self.time_patch.start(); self.addCleanup(self.time_patch.stop)

    def test_partial_canceled_remaining_size_fills_position_must_all_agree(self):
        evidence = self.adapter.observe("BTC", self.state)["evidence"]
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["filled"], "0.1")
        self.assertEqual(evidence["value"], "10.0")
        self.info.user_fills_by_time.return_value = []
        evidence = self.adapter.observe("BTC", self.state)["evidence"]
        self.assertTrue(evidence["terminal"])
        self.assertFalse(evidence["complete"])
        self.adapter.snapshot.return_value = snapshot()
        self.assertFalse(self.adapter.observe("BTC", self.state)["evidence"]["complete"])

    def test_missing_remaining_size_or_zeroed_canceled_size_stays_unresolved(self):
        self.order.pop("sz")
        self.assertFalse(self.adapter.observe("BTC", self.state)["evidence"]["complete"])
        self.order["sz"] = "0"
        self.assertFalse(self.adapter.observe("BTC", self.state)["evidence"]["complete"])

    def test_foreign_fill_order_and_overflow_are_rejected(self):
        self.info.user_fills_by_time.return_value = [{**self.fill, "oid": 999}]
        with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)
        self.info.user_fills_by_time.return_value = [self.fill] * 2000
        with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)
        self.info.user_fills_by_time.return_value = [self.fill]
        self.adapter.snapshot.return_value = {**snapshot("0.1"), "orders": [{"oid": 999}]}
        with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)

    def test_unknown_is_not_terminal_and_duplicate_fills_fail_closed(self):
        self.info.query_order_by_cloid.return_value = {"status": "unknownOid"}
        self.info.user_fills_by_time.return_value = []
        evidence = self.adapter.observe("BTC", self.state)["evidence"]
        self.assertFalse(evidence["known"])
        self.assertFalse(evidence["complete"])
        self.setUp()
        self.info.user_fills_by_time.return_value = [self.fill, self.fill]
        with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)

    def test_fresh_participation_caps_are_rechecked_before_signing(self):
        limits = config()["execution"]
        limits["depthFraction"] = "0.1"
        state = execution_state(1000)
        target = intent()
        plan = plan_child(target, state, snapshot(), limits, 1000)
        admission = Admission(lambda: 2000)
        admission.feed_ready.set(); admission.generation = target["id"]
        current = snapshot(); current["bidDepth"] = Decimal(1)
        self.adapter.observe = Mock(return_value={"snapshot": current, "position": "0"})
        with self.assertRaises(Stop) as failure:
            self.adapter._preflight(plan, state, target, admission)
        self.assertEqual(failure.exception.code, "PARTICIPATION_BOUND_CHANGED")
        current["bidDepth"] = None
        with self.assertRaises(Stop): self.adapter._preflight(plan, state, target, admission)
        plan["depthFraction"] = None
        plan["dailyVolumeFraction"] = "0.001"
        current["dayNotionalVolume"] = Decimal(1)
        with self.assertRaises(Stop): self.adapter._preflight(plan, state, target, admission)

    def test_preflight_stop_proves_not_submitted_without_loading_signer(self):
        admission = Admission(lambda: 2000)
        self.adapter._preflight = Mock(side_effect=Stop("ADMISSION_CLOSED", "Disposable"))
        self.adapter.exchange = Mock(side_effect=AssertionError())
        result = self.adapter.submit_child(self.child, self.state, intent(), admission)
        self.assertEqual(result["classification"], "not_submitted")
        self.adapter.exchange.assert_not_called()

    def test_precision_change_rejects_planned_child_before_loading_signer(self):
        target = intent()
        admission = Admission(lambda: 2000)
        admission.feed_ready.set()
        admission.generation = target["id"]
        self.adapter.observe = Mock(return_value={"snapshot": {**snapshot(), "decimals": 4}, "position": "0"})
        self.adapter.exchange = Mock(side_effect=AssertionError("signer read"))
        result = self.adapter.submit_child(self.child, execution_state(1000), target, admission)
        self.assertEqual(result, {"classification": "not_submitted", "reason": "PRECISION_UNAVAILABLE"})
        self.adapter.exchange.assert_not_called()

    def test_cancellation_only_calls_exact_verified_owned_oid(self):
        self.info.query_order_by_cloid.return_value["order"]["status"] = "open"
        self.adapter.snapshot.return_value["orders"] = [{"oid": 11}]
        exchange = Mock()
        self.adapter.exchange = Mock(return_value=exchange)
        admission = Admission(lambda: 2000, 1500, 3000)
        admission.stopping.set()
        result = self.adapter.cancel_child(self.child, self.state, intent(), admission)
        self.assertEqual(result["classification"], "cancel_submitted")
        exchange.cancel.assert_called_once_with("BTC", 11)
        self.assertEqual(self.adapter.exchange.call_args.args, (3000,))


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.journal = MemoryJournal()
        self.runtime = Runtime(config(), self.journal, mode="live", worker=ImmediateIO(), adapter=FakeAdapter(),
                               reporter=Mock(), session_seconds=30, clock=lambda: 1000)
        self.runtime.state["execution"]["active"] = intent()
        self.runtime.state["normalized"] = "1"
        self.runtime.commit()

    def test_default_restart_never_extends_session(self):
        restored = Runtime(config(), self.journal, mode="live", worker=ImmediateIO(), adapter=FakeAdapter(),
                           reporter=Mock(), session_seconds=99, clock=lambda: 10000)
        self.assertEqual(restored.state["sessionDeadlineMs"], 31000)

    def test_new_session_verifies_position_and_keeps_frozen_quantity(self):
        from runtime import rearm_session
        rearm_session(self.journal, config(), FakeAdapter(), 60, clock=lambda: 100000)
        saved = self.journal.read_runtime()
        self.assertEqual(saved["sessionDeadlineMs"], 160000)
        self.assertEqual(saved["execution"]["active"]["quantity"], "1")
        self.assertEqual(saved["execution"]["active"]["id"], "one")
        self.assertEqual(saved["execution"]["active"]["deadlineMs"], 160000)
        adapter = FakeAdapter(); adapter.foreign = True
        with self.assertRaises(Stop): rearm_session(self.journal, config(), adapter, 60)

    def test_new_session_refuses_child_but_recovery_retains_plan_and_bypasses_policy(self):
        from runtime import rearm_session
        child = {"cloid": "0x" + "1" * 32, "phase": "submission_uncertain", "plan": {"market": "BTC"}, "fills": []}
        self.runtime.state["execution"]["child"] = copy.deepcopy(child)
        self.runtime.commit()
        with self.assertRaises(Stop): rearm_session(self.journal, config(), FakeAdapter(), 60)
        changed = config(); changed["strategy"]["window"] = 30
        rearm_session(self.journal, changed, FakeAdapter(), 60, recover_only=True, clock=lambda: 100000)
        recovered = Runtime(changed, self.journal, mode="live", worker=ImmediateIO(), adapter=FakeAdapter(),
                            reporter=Mock(), session_seconds=60, clock=lambda: 100000, recover_only=True)
        self.assertEqual(recovered.state["sessionDeadlineMs"], 31000)
        self.assertEqual(recovered.state["cleanupDeadlineMs"], 160000)
        self.assertEqual(recovered.state["execution"]["child"], child)
        self.assertTrue(recovered.admission.stopping.is_set())
        self.assertFalse(recovered.admission.feed_ready.is_set())

if __name__ == "__main__": unittest.main()
