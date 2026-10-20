"""Cooperative capital and observed external reduction traces."""
import copy
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from executor import execution_state
from models import Target
from risk import Stop
from runtime import Runtime
from runtime_adapter import RuntimeAdapter
from test_runtime import FakeAdapter, ImmediateIO, MemoryJournal, bar, config, snapshot


def signal(now=1000, pending=False, capital=1000, remaining=None):
    return {"evidence": "verified-public-reads", "blockTimestamp": now,
            "pendingRequestCount": "1" if pending else "0", "escrowedShares": "200" if pending else "0",
            "restricted": False, "operationInProgress": False, "totalAssets": str(capital * 1000000),
            "remainingAssets": str((capital if remaining is None else remaining) * 1000000)}


class CapitalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now, self.current = 1000000, signal()
        self.settings = config()
        self.settings["execution"].update(targetNotional="3000", capitalMultiple="3")
        self.journal, self.adapter = MemoryJournal(), FakeAdapter()
        self.runtime = self.create()

    def create(self):
        return Runtime(self.settings, self.journal, mode="live", worker=ImmediateIO(), adapter=self.adapter,
                       reporter=Mock(), clock=lambda: self.now, session_seconds=600,
                       feed=SimpleNamespace(healthy=True, poll=lambda now: None),
                       lifecycle=lambda now: self.current, release_worker=ImmediateIO())

    def poll(self):
        self.runtime.apply_release(self.now)
        self.runtime.apply_release(self.now)

    def test_previous_config_requires_explicit_reviewed_adaptation(self):
        import json
        import tempfile
        from pathlib import Path
        from configuration import load_config
        previous = copy.deepcopy(self.settings)
        previous["schema"] = "2n20-runtime-config-v2"
        previous["execution"].pop("capitalMultiple")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strategy.public.json"
            path.write_text(json.dumps(previous))
            with self.assertRaises(Stop): load_config(path)

    def test_cash_covered_pending_demand_holds_before_restriction(self):
        self.current = signal(pending=True, remaining=800)
        self.poll()
        self.assertTrue(self.runtime.executor.open_risk_paused)

    def test_worker_cut_is_adopted_and_settlement_does_not_reopen_it(self):
        self.poll()
        self.runtime.nominate(Target(Decimal(1), "same"), bar(0), self.now)
        state = self.runtime.state["execution"]
        state["active"], state["pending"] = state["pending"], None
        state["position"] = "30"
        self.now += 5000
        self.current = signal(self.now // 1000, pending=True, remaining=800)
        self.poll()
        info = Mock()
        adapter = RuntimeAdapter(SimpleNamespace(scope={"tradingAccount": "disposable"}), info)
        info.user_fills_by_time.return_value = [{"oid": 77, "tid": 88, "time": self.now,
            "coin": "BTC", "side": "A", "sz": "6", "startPosition": "30"}]
        adapter.snapshot = Mock(return_value=snapshot("24"))
        adapter.submit_child = Mock(side_effect=AssertionError("No reopen permitted"))
        self.runtime.executor.adapter = adapter
        with patch("runtime_adapter.time.time", side_effect=lambda: self.now / 1000):
            self.runtime.tick(); self.runtime.tick()
            self.assertEqual(state["position"], "24")
            self.assertEqual(len(state["externalReductions"]), 1)
            self.assertEqual(state["filledSize"], "0")
            self.runtime.commit()
            self.runtime = self.create()
            self.runtime.executor.adapter = adapter
            self.now += 5000
            self.current = signal(self.now // 1000, capital=800)
            self.poll()
            self.runtime.tick(); self.runtime.tick()
            self.assertEqual(self.runtime.state["execution"]["position"], "24")
            self.assertEqual(len(self.runtime.state["execution"]["externalReductions"]), 1)
            adapter.submit_child.assert_not_called()

    def test_pending_hold_rebases_same_signal_after_long_hold_and_restart(self):
        self.assertTrue(self.runtime.executor.open_risk_paused)
        self.poll()
        self.runtime.nominate(Target(Decimal(1), "same"), bar(0), self.now)
        state = self.runtime.state["execution"]
        state["active"], state["pending"] = state["pending"], None
        state.update(position="30", childCount=4)
        self.adapter.position = Decimal(30)
        original = copy.deepcopy(state["active"])
        self.now += 5000
        self.current = signal(self.now // 1000, pending=True, remaining=800)
        self.poll()
        self.assertTrue(self.runtime.executor.open_risk_paused)
        self.assertEqual(Decimal(state["active"]["quantity"]), Decimal(24))
        self.assertEqual(state["active"]["deadlineMs"], original["deadlineMs"])
        self.assertEqual(state["childCount"], 4)
        state["position"], self.adapter.position = "24", Decimal(24)
        self.now += 125000
        self.current = signal(self.now // 1000, pending=True, remaining=800)
        self.poll(); self.poll()
        self.assertEqual(sum(r["status"] == "withdrawal_attention" for r in self.runtime.state["reports"]), 1)
        self.assertTrue(self.runtime.executor.open_risk_paused)
        self.runtime.commit()
        self.runtime = self.create()
        self.assertTrue(self.runtime.executor.open_risk_paused)
        self.now += 5000
        self.current = signal(self.now // 1000, capital=800)
        self.poll()
        self.runtime.nominate(Target(Decimal(1), "same"), bar(3), self.now)
        pending = self.runtime.state["execution"]["pending"]
        self.assertEqual(Decimal(pending["quantity"]), Decimal(24))
        self.assertNotEqual(pending["id"], original["id"])
        identity, deadline = pending["id"], pending["deadlineMs"]
        self.runtime.nominate(Target(Decimal(1), "same"), bar(4), self.now + 1000)
        self.poll()
        self.assertEqual(self.runtime.state["execution"]["pending"]["id"], identity)
        self.assertEqual(self.runtime.state["execution"]["pending"]["deadlineMs"], deadline)
        self.assertFalse(self.adapter.calls)

    def test_multiple_hard_ceiling_and_latest_signal_during_unknown_policy(self):
        self.current = signal(capital=2000)
        self.poll()
        self.runtime.nominate(Target(Decimal("0.5"), "half"), bar(0), self.now)
        self.assertEqual(Decimal(self.runtime.state["execution"]["pending"]["quantity"]), Decimal(15))
        self.now += 5000
        self.current = {}
        self.poll()
        self.assertTrue(self.runtime.executor.open_risk_paused)
        self.runtime.nominate(Target(Decimal(-1), "latest"), bar(1), self.now)
        self.assertEqual(self.runtime.state["latestTarget"]["fraction"], "-1")


    def test_flat_and_reducing_signals_remain_available_during_hold(self):
        self.poll()
        self.runtime.state["execution"]["position"] = "30"
        self.now += 5000
        self.current = signal(self.now // 1000, pending=True, remaining=800)
        self.poll()
        self.runtime.nominate(Target(Decimal("0.5"), "reduce"), bar(1), self.now)
        self.assertEqual(Decimal(self.runtime.state["execution"]["pending"]["quantity"]), Decimal(12))
        self.current = {}
        self.now += 5000
        self.poll()
        self.runtime.nominate(Target(Decimal(0), "flat"), bar(2), self.now)
        self.assertEqual(Decimal(self.runtime.state["execution"]["pending"]["quantity"]), Decimal(0))


class ExternalReductionTests(unittest.TestCase):
    def setUp(self):
        self.info = Mock()
        self.adapter = RuntimeAdapter(SimpleNamespace(scope={"tradingAccount": "disposable"}), self.info)
        self.state = execution_state(1000)
        self.state.update(position="30", settlementEpoch={"sinceMs": 1000, "throughMs": 22000})
        self.fill = {"oid": 99, "tid": 7, "time": 1500, "coin": "BTC", "side": "A", "sz": "6", "startPosition": "30"}
        self.info.user_fills_by_time.return_value = [self.fill]
        self.adapter.snapshot = Mock(return_value=snapshot("24"))
        patcher = patch("runtime_adapter.time.time", return_value=2)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_reduction_reconciles_once_and_requires_complete_history(self):
        view = self.adapter.observe("BTC", self.state)
        self.assertEqual(view["externalDelta"], "-6")
        self.assertEqual(view["evidence"]["filled"], "0")
        self.assertEqual(view["evidence"]["fees"], "0")
        self.state.update(position=view["position"], externalReductions=view["externalReductions"])
        self.assertEqual(self.adapter.observe("BTC", copy.deepcopy(self.state))["externalReductions"], [])
        self.info.user_fills_by_time.return_value = []
        with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)

    def test_increases_reversals_missing_epoch_and_incomplete_delta_rejected(self):
        for changes in ({"side": "B"}, {"sz": "31"}, {"startPosition": "29"}, {"coin": "ETH"}, {"time": 23000}):
            with self.subTest(changes=changes):
                self.info.user_fills_by_time.return_value = [{**self.fill, **changes}]
                with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)
        self.info.user_fills_by_time.return_value = [self.fill]
        self.state.pop("settlementEpoch")
        with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)

    def test_foreign_protection_is_never_canceled_or_adopted(self):
        self.adapter.snapshot.return_value = {**snapshot("24"), "orders": [{"oid": 500, "reduceOnly": True}]}
        with self.assertRaises(Stop): self.adapter.observe("BTC", self.state)
        self.info.cancel.assert_not_called()

    def test_open_entry_can_be_canceled_before_external_delta_is_reconciled(self):
        from test_runtime import AdapterEvidenceTests
        from runtime_io import Admission
        fixture = AdapterEvidenceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.state["settlementEpoch"] = {"sinceMs": 1000, "throughMs": 22000}
        external = {**self.fill, "startPosition": "0.1", "sz": "0.02", "time": 1600}
        fixture.info.user_fills_by_time.return_value = [fixture.fill, external]
        fixture.adapter.snapshot.return_value = snapshot("0.08")
        detail = fixture.info.query_order_by_cloid.return_value["order"]
        detail["status"] = "open"
        observation = fixture.adapter.observe("BTC", fixture.state)
        self.assertTrue(observation["evidence"]["known"])
        self.assertFalse(observation["evidence"]["consistent"])
        self.assertEqual(observation["externalReductions"], [])
        exchange = Mock()
        fixture.adapter.exchange = Mock(return_value=exchange)
        result = fixture.adapter.cancel_child(fixture.child, fixture.state, {"id": "one", "deadlineMs": 10000}, Admission(lambda: 2000))
        self.assertEqual(result["classification"], "cancel_submitted")
        exchange.cancel.assert_called_once_with("BTC", 11)
        detail["status"] = "canceled"
        result = fixture.adapter.observe("BTC", fixture.state)
        self.assertTrue(result["evidence"]["complete"])
        self.assertEqual(result["evidence"]["filled"], "0.1")
        self.assertEqual(result["externalDelta"], "-0.02")
        fixture.info.query_order_by_cloid.return_value = {"status": "unknownOid"}
        with self.assertRaises(Stop): fixture.adapter.observe("BTC", fixture.state)
