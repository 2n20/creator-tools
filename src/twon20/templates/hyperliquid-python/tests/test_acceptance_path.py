import copy
from decimal import Decimal
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hyperliquid.utils.types import Cloid

from test_runtime import bar, config
from executor import LimitChaser
from feed import HyperliquidCandleFeed
from models import Bar
from runtime import Runtime
from runtime_adapter import RuntimeAdapter
from runtime_io import SerialIO
from runtime_journal import RuntimeJournal
from strategy import BandBreakout


class FictionalVenue:
    def __init__(self, clock, checkpoint):
        self.clock, self.checkpoint = clock, checkpoint
        self.candles = [bar(index).public() for index in range(5)]
        self.order = None
        self.fills = []
        self.canceled = False
        self.lose_cancel_ack = False
        self.remaining_visible = True
        self.submissions, self.cancellations = [], []
        self.callback = None
        methods = ["meta", "subscribe", "disconnect_websocket", "candles_snapshot", "meta_and_asset_ctxs",
                   "l2_snapshot", "post", "user_state", "open_orders", "frontend_open_orders", "perp_dexs",
                   "spot_user_state", "user_fees", "query_order_by_cloid", "user_fills_by_time"]
        self.info = Mock(spec=methods)
        self.info.meta.return_value = {"universe": [{"name": "BTC", "szDecimals": 5}]}
        self.info.post.side_effect = lambda path, payload: {"user": payload["user"], "coin": payload["coin"],
            "leverage": {"type": "cross", "value": 1}, "maxTradeSzs": ["10000", "10000"],
            "availableToTrade": ["10000", "10000"]}
        self.info.subscribe.side_effect = self.subscribe
        self.info.candles_snapshot.side_effect = self.candles_snapshot
        self.info.meta_and_asset_ctxs.return_value = [self.info.meta.return_value,
                                                    [{"markPx": "110", "dayNtlVlm": "1000000"}]]
        self.info.l2_snapshot.side_effect = lambda market: {
            "coin": market, "time": self.clock(),
            "levels": [[{"px": "109.9", "sz": "100"}], [{"px": "110.1", "sz": "100"}]]}
        self.info.user_state.side_effect = self.user_state
        self.info.open_orders.side_effect = self.open_orders
        self.info.frontend_open_orders.side_effect = self.open_orders
        self.info.perp_dexs.return_value = [None]
        self.info.spot_user_state.return_value = {"balances": []}
        self.info.user_fees.return_value = {"userCrossRate": "0.00045"}
        self.info.query_order_by_cloid.side_effect = self.query_order
        self.info.user_fills_by_time.side_effect = lambda account, start, end: copy.deepcopy(
            [fill for fill in self.fills if start <= fill["time"] <= end])
        self.exchange = Mock(spec=["order", "cancel"])
        self.exchange.order.side_effect = self.submit
        self.exchange.cancel.side_effect = self.cancel

    def subscribe(self, subscription, callback):
        self.callback = callback
        return 1

    def candles_snapshot(self, market, interval, start, end):
        return copy.deepcopy([candle for candle in self.candles if start <= candle["t"] <= end])

    def user_state(self, account):
        size = sum((Decimal(fill["sz"]) for fill in self.fills), Decimal(0))
        return {"time": self.clock(), "withdrawable": "10000",
                "marginSummary": {"accountValue": "10000", "totalMarginUsed": "0"},
                "assetPositions": [{"position": {"coin": "BTC", "szi": str(size)}}] if size else []}

    def open_orders(self, account):
        return [copy.deepcopy(self.order)] if self.order and not self.canceled else []

    def query_order(self, account, cloid):
        if self.order is None or self.order["cloid"] != cloid.to_raw():
            return {"status": "unknownOid"}
        order = copy.deepcopy(self.order)
        if not self.remaining_visible:
            order.pop("sz")
        return {"status": "order", "order": {"order": order, "status": "canceled" if self.canceled else "open"}}

    def submit(self, market, buy, size, price, order_type, reduce_only, cloid):
        self.submissions.append({"market": market, "buy": buy, "size": size, "price": price,
                                 "orderType": order_type, "reduceOnly": reduce_only, "cloid": cloid.to_raw(),
                                 "checkpoint": self.checkpoint()})
        self.order = {"coin": market, "side": "B" if buy else "A", "origSz": str(size), "sz": str(size),
                      "limitPx": str(price), "reduceOnly": reduce_only, "cloid": cloid.to_raw(),
                      "oid": 731, "timestamp": self.clock()}
        return {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 731}}]}}}

    def partial_fill(self):
        self.fills.append({"oid": self.order["oid"], "tid": 19, "time": self.clock(), "coin": "BTC", "side": "B",
                           "sz": "0.1", "px": "109.8", "fee": "0.002", "feeToken": "USDC", "crossed": False})
        self.order["sz"] = str(Decimal(self.order["origSz"]) - Decimal("0.1"))

    def cancel(self, market, oid):
        self.cancellations.append((market, oid))
        if self.order is None or (market, oid) != (self.order["coin"], self.order["oid"]):
            raise AssertionError("Cancellation must identify the exact fictional owned order")
        self.canceled = True
        if self.lose_cancel_ack:
            raise TimeoutError("Fictional cancellation response lost")
        return {"status": "ok"}


class StarterAcceptancePathTests(unittest.TestCase):
    def setUp(self):
        self.now = 5 * 60000 + 5000
        self.settings = config()
        self.settings["strategy"] = {"window": 5, "multiplier": "1"}
        self.settings["execution"].update(targetNotional="110", chaseSeconds=600, requoteSeconds=60)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / "state"
        self.scope = {"tradingAccount": "disposable-acceptance-account"}
        self.journal = RuntimeJournal(self.path, self.scope)
        self.addCleanup(lambda: self.journal.close())
        self.venue = FictionalVenue(lambda: self.now, lambda: self.journal.read_runtime())
        self.access = SimpleNamespace(scope=self.scope, verify=Mock(),
                                      signer=Mock(side_effect=AssertionError("No signer may be loaded")))
        self.access.pending_withdrawals = lambda: {"evidence": "verified-public-reads", "blockTimestamp": self.now // 1000,
            "pendingRequestCount": "0", "escrowedShares": "0", "totalAssets": "10000000000", "remainingAssets": "10000000000",
            "restricted": False, "operationInProgress": False}
        self.adapter = RuntimeAdapter(self.access, self.venue.info)
        self.adapter.exchange = Mock(side_effect=self.exchange_boundary)
        self.workers = []
        self.addCleanup(self.close_workers)
        self.delivered = []
        self.delivery_checkpoints = []
        self.reporter = Mock()
        self.reporter.emit.side_effect = self.receive_report
        self.sdk_info = Mock(return_value=self.venue.info)
        self.sdk_exchange = Mock(side_effect=AssertionError("No real exchange may be constructed"))
        for patcher in (
            patch("adapter.sdk_types", return_value=(self.sdk_info, self.sdk_exchange, Cloid)),
            patch("runtime_adapter.sdk_types", return_value=(self.sdk_info, self.sdk_exchange, Cloid)),
            patch("adapter.time.time", side_effect=lambda: self.now / 1000),
            patch("socket.socket.connect", side_effect=AssertionError("No live network in acceptance tests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runtime = self.new_runtime()

    def exchange_boundary(self, expires_ms=None, before_sign=None):
        if before_sign:
            before_sign()
        return self.venue.exchange

    def receive_report(self, report):
        self.delivered.append(copy.deepcopy(report))
        self.delivery_checkpoints.append(self.journal.read_runtime()["reports"])

    def close_workers(self):
        for worker in self.workers:
            worker.close()
            worker.thread.join(timeout=1)

    def new_runtime(self, history=()):
        worker = SerialIO()
        self.workers.append(worker)
        feed = HyperliquidCandleFeed(worker, history=history, clock=lambda: self.now)
        return Runtime(self.settings, self.journal, mode="live", worker=worker, adapter=self.adapter,
                       reporter=self.reporter, feed=feed, session_seconds=600, clock=lambda: self.now)

    def tick_until(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.runtime.tick()
            if predicate():
                return
            self.now += 10
            time.sleep(0.001)
        self.fail("Acceptance trace stalled: " + repr(self.runtime.result()))

    def test_closed_candle_partial_fill_restart_and_verified_cancellation(self):
        self.run_trace()

    def test_lost_cancellation_response_reconciles_without_duplicate_order(self):
        self.venue.lose_cancel_ack = True
        self.run_trace()

    def run_trace(self):
        self.assertIsInstance(self.runtime.strategy, BandBreakout)
        self.assertIsInstance(self.runtime.executor, LimitChaser)
        self.tick_until(lambda: self.runtime.state["cursor"] == bar(4).open_ms and self.runtime.worker.idle)
        self.assertEqual(self.runtime.bars_processed, 5)
        self.assertEqual(self.runtime.state["decisionSequence"], 0)
        self.assertIsNone(self.runtime.state["execution"]["active"])
        self.assertEqual(self.venue.submissions, [])
        self.venue.info.subscribe.assert_called_once_with(
            {"type": "candle", "coin": "BTC", "interval": "1m"}, self.runtime.feed.callback)

        breakout = bar(5, "110").public()
        self.venue.candles.append(breakout)
        self.now = 6 * 60000 + 1000
        self.venue.callback({"channel": "candle", "data": breakout})
        for _ in range(5):
            self.runtime.tick()
        self.assertEqual(self.runtime.state["cursor"], bar(4).open_ms)
        self.assertEqual(self.venue.submissions, [])

        self.now = 6 * 60000 + 2000
        self.tick_until(lambda: self.runtime.state["execution"]["phase"] == "child_open"
                        and self.runtime.worker.idle)
        self.assertEqual(len(self.venue.submissions), 1)
        submission = self.venue.submissions[0]
        retained = self.journal.read_runtime()
        child = retained["execution"]["child"]
        target = retained["execution"]["active"]
        self.assertEqual(submission["orderType"], {"limit": {"tif": "Alo"}})
        self.assertTrue(submission["buy"])
        self.assertFalse(submission["reduceOnly"])
        self.assertEqual(Decimal(str(submission["size"])), Decimal("0.22727"))
        self.assertEqual(submission["price"], 109.9)
        self.assertEqual(target["reason"], "upper_breakout")
        self.assertEqual(Decimal(target["quantity"]), Decimal(1))
        before_effect = submission["checkpoint"]
        self.assertEqual(before_effect["cursor"], bar(5).open_ms)
        self.assertEqual(before_effect["execution"]["child"]["phase"], "submission_uncertain")
        self.assertEqual(before_effect["execution"]["child"]["cloid"], submission["cloid"])

        for _ in range(2):
            self.venue.callback({"channel": "candle", "data": breakout})
            self.runtime.tick()
        self.assertEqual(self.runtime.state["decisionSequence"], 1)
        self.assertEqual(len(self.venue.submissions), 1)

        self.venue.partial_fill()
        self.now += 1000
        self.tick_until(lambda: self.runtime.state["execution"]["child"].get("filled") == "0.1"
                        and self.runtime.worker.idle)
        progress = self.runtime.result()
        self.assertTrue(progress["unresolvedChild"])
        self.assertIsNone(progress["position"])
        self.assertEqual(Decimal(progress["knownPosition"]), Decimal("0.1"))
        self.assertEqual(Decimal(progress["filledSize"]), Decimal("0.1"))
        self.assertEqual(Decimal(progress["vwap"]), Decimal("109.8"))
        self.assertEqual(Decimal(progress["feesUsdc"]), Decimal("0.002"))
        self.assertEqual((progress["makerFills"], progress["takerFills"]), (1, 0))

        old_deadline = retained["sessionDeadlineMs"]
        self.runtime.feed.close()
        self.runtime.worker.close(finalizer=self.runtime.feed.dispose)
        self.runtime.worker.thread.join(timeout=1)
        self.assertFalse(self.runtime.worker.thread.is_alive())
        self.journal.close()
        self.journal = RuntimeJournal(self.path, self.scope)
        saved = self.journal.read_runtime()
        history = [Bar.parse(value, "BTC", "1m") for value in saved["history"]]
        self.runtime = self.new_runtime(history)
        self.tick_until(lambda: self.runtime.feed.healthy and self.runtime.worker.idle)
        self.assertEqual(self.runtime.state["sessionDeadlineMs"], old_deadline)
        self.assertEqual(self.runtime.state["decisionSequence"], 1)
        self.assertEqual(self.runtime.bars_processed, 0)
        self.assertEqual(self.runtime.state["execution"]["child"]["cloid"], child["cloid"])
        self.assertEqual(self.runtime.state["execution"]["active"], target)
        self.assertGreaterEqual(self.venue.info.candles_snapshot.call_count, 3)
        self.assertEqual(len(self.venue.submissions), 1)

        self.venue.remaining_visible = False
        self.runtime.stop()
        self.runtime.feed.close()
        self.tick_until(lambda: self.runtime.state["execution"]["error"] == "CHILD_FILL_COVERAGE_PENDING")
        waiting = self.journal.read_runtime()
        self.assertEqual(self.venue.cancellations, [("BTC", 731)])
        self.assertEqual(waiting["execution"]["child"]["cloid"], child["cloid"])
        self.assertEqual(waiting["execution"]["ownedOids"], [])
        blocked = waiting["reports"][-1]
        self.assertEqual(blocked["status"], "blocked")
        self.assertTrue(blocked["unresolvedChild"])
        self.assertIsNone(blocked["position"])
        self.assertEqual(Decimal(blocked["knownPosition"]), Decimal("0.1"))
        self.assertIsNone(blocked["residual"])
        self.assertEqual(Decimal(blocked["filledSize"]), Decimal("0.1"))
        self.assertEqual(Decimal(blocked["vwap"]), Decimal("109.8"))
        self.assertEqual(Decimal(blocked["feesUsdc"]), Decimal("0.002"))
        self.assertFalse(any(report["status"] == "child_complete" for report in waiting["reports"]))

        self.venue.remaining_visible = True
        self.now += 1000
        self.tick_until(lambda: self.runtime.state["execution"]["child"] is None)
        final = self.journal.read_runtime()
        completed = final["reports"][-1]
        self.assertEqual(completed["status"], "child_complete")
        self.assertEqual(completed["childId"], child["cloid"])
        self.assertEqual(completed["oid"], 731)
        self.assertFalse(completed["unresolvedChild"])
        self.assertEqual(Decimal(completed["position"]), Decimal("0.1"))
        self.assertEqual(Decimal(completed["residual"]), Decimal("0.9"))
        for values in (completed, self.runtime.result()):
            self.assertEqual(Decimal(values["filledSize"]), Decimal("0.1"))
            self.assertEqual(Decimal(values["vwap"]), Decimal("109.8"))
            self.assertEqual(Decimal(values["feesUsdc"]), Decimal("0.002"))
            self.assertEqual((values["makerFills"], values["takerFills"]), (1, 0))
        self.assertEqual(final["execution"]["ownedOids"], [731])
        self.assertEqual(Decimal(final["execution"]["position"]), Decimal("0.1"))
        self.assertEqual(len(self.venue.submissions), 1)
        self.assertEqual(self.venue.cancellations, [("BTC", 731)])
        event_ids = [report["eventId"] for report in self.delivered]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertEqual(self.delivered, final["reports"])
        for report, checkpoint_reports in zip(self.delivered, self.delivery_checkpoints):
            self.assertIn(report, checkpoint_reports)
        self.access.signer.assert_not_called()
        self.sdk_exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()
