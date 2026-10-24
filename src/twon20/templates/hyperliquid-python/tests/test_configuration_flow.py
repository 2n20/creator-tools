"""One public configuration across replaceable components; no live effects."""
import copy
import io
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from configuration import load_config
from executor import execution_state, plan_child
from feed import FixtureFeed
from models import Bar, BarBatch, Target
from risk import Stop
from runner import parser, run
from runtime import Runtime
from test_runner import LINK, SCOPE, snapshot
from test_runtime import ImmediateIO, MemoryJournal

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "strategy.public.json"

    def configure(self, market="ETH"):
        config = load_config(ROOT / "config.public.example.json")
        config.update(market=market)
        self.path.write_text(json.dumps(config))
        return config

    def check(self, observed, mode="check"):
        access = Mock(scope=SCOPE, directory=self.root, vault=LINK)
        access.pending_withdrawals.return_value = {}
        sdk = Mock()
        sdk.snapshot.return_value = observed
        args = [mode, "--vault", LINK, "--directory", str(self.root), "--config", str(self.path)]
        if mode == "run":
            args += ["--mode", "live", "--feed", "public", "--max-seconds", "30"]
        with patch("adapter.Access", return_value=access), patch("adapter.Adapter", return_value=sdk), \
                patch("runtime_adapter.RuntimeAdapter", return_value=sdk), \
                patch("twon20.files.load_key", side_effect=AssertionError("signer read")), \
                patch("runtime.RuntimeJournal", side_effect=AssertionError("journal creation")):
            result, code = run(parser().parse_args(args))
        self.assertEqual(code, 0)
        access.signer.assert_not_called()
        sdk.submit_order.assert_not_called()
        sdk.submit_collateral.assert_not_called()
        sdk.snapshot.assert_called_once_with(observed["market"])
        return result

    def test_market_switch_changes_check_feed_target_and_child_without_strategy_edits(self):
        for market, decimals in (("BTC", 5), ("ETH", 4), ("SOL", 2)):
            with self.subTest(market=market):
                config = self.configure(market)
                result = self.check(snapshot(market=market, decimals=decimals))
                self.assertEqual((result["market"], result["sizeDecimals"]), (market, decimals))
                self.assertNotIn("paper", result["nextAction"])
                self.assertIn("2n20 complete-setup", result["nextAction"])
                self.assertNotIn("live-smoke", result["nextAction"])
                preview = self.check(snapshot(market=market, decimals=decimals), "run")
                self.assertEqual(preview["config"], config)
                self.assertEqual(preview["sizeDecimals"], decimals)
                self.assertFalse(preview["capital"]["readyToIncrease"])
                self.assertIn("repeat this preview", preview["requiredHumanAction"])

                strategy = Mock()
                strategy.evaluate.return_value = (Target(Decimal(1), "replacement_strategy"), {"observed": True})
                executor = Mock()
                runtime = Runtime(config, MemoryJournal(), worker=ImmediateIO(), strategy=strategy,
                                  executor_factory=lambda *args: executor, reporter=Mock(), clock=lambda: 1000)
                runtime.tick()
                bars, checkpoint = strategy.evaluate.call_args.args
                self.assertEqual({bar.market for bar in bars}, {market})
                self.assertEqual(checkpoint, {})
                target = executor.set_target.call_args.args[0]
                self.assertEqual(target["market"], market)
                self.assertNotIn("sizeDecimals", target)
                self.assertNotIn("sizeDecimals", config)
                child = plan_child(target, execution_state(1000),
                                   snapshot(market=market, decimals=decimals), config["execution"], 1000)
                self.assertEqual((child["market"], child["decimals"]), (market, decimals))
                runtime.stop()
                executor.shutdown.assert_called_once()

    def test_precision_is_discovered_and_orders_are_rounded_without_config_edits(self):
        config = self.configure()
        original = self.path.read_bytes()
        target = {"market": "ETH", "quantity": "0.23456789", "arrivalPrice": "100", "createdMs": 1000}
        for decimals in range(7):
            with self.subTest(decimals=decimals):
                observed = snapshot(market="ETH", decimals=decimals)
                observed.update(mark=Decimal(100), bid=Decimal("99.9999"), ask=Decimal("100.0001"))
                self.assertEqual(self.check(observed)["sizeDecimals"], decimals)
                child = plan_child(target, execution_state(1000), observed, config["execution"], 1000)
                if decimals == 0:
                    self.assertIsNone(child)
                    continue
                size, price = Decimal(child["size"]), Decimal(child["price"])
                self.assertLessEqual(size, Decimal(target["quantity"]))
                self.assertEqual(size, size.quantize(Decimal(1).scaleb(-decimals)))
                self.assertEqual(price, price.quantize(Decimal(1).scaleb(-(6 - decimals))))
                self.assertLessEqual(len(price.normalize().as_tuple().digits), 5)
                self.assertLessEqual(size * max(price, observed["mark"]), Decimal(config["execution"]["childNotional"]))
        self.assertEqual(self.path.read_bytes(), original)

    def test_user_precision_is_rejected_instead_of_overriding_exchange_metadata(self):
        config = self.configure()
        config["sizeDecimals"] = 5
        self.path.write_text(json.dumps(config))
        with self.assertRaises(Stop) as caught:
            load_config(self.path)
        self.assertEqual(caught.exception.code, "PUBLIC_CONFIG_INVALID")

    def test_public_feed_constructor_gets_the_same_configured_market_and_interval(self):
        config = self.configure()
        args = parser().parse_args(["run", "--feed", "public", "--config", str(self.path),
                                    "--state-directory", str(self.root / "paper"), "--max-bars", "1"])
        observed = []
        def drive(runtime, *bounds):
            observed.extend([runtime.config["market"], runtime.config["interval"], runtime.feed])
            return {"unresolvedChild": False, "reason": None}
        with patch("runtime.HyperliquidCandleFeed") as feed, patch("runtime.drive", side_effect=drive):
            run(args)
        self.assertEqual(feed.call_args.args[1:3], (config["market"], config["interval"]))
        self.assertEqual(observed, [config["market"], config["interval"], feed.return_value])

    def test_wrong_market_or_precision_blocks_check_preview_and_child(self):
        config = self.configure()
        target = {"market": "ETH"}
        for observed, code in ((snapshot(market="BTC", decimals=4), "MARKET_MISMATCH"),
                               (snapshot(market="ETH", decimals=7), "PRECISION_UNAVAILABLE"),
                               (snapshot(market="ETH", decimals=None), "PRECISION_UNAVAILABLE"),
                               (snapshot(market="ETH", decimals=True), "PRECISION_UNAVAILABLE")):
            with self.subTest(code=code, observed=observed["decimals"]):
                for mode in ("check", "run"):
                    with self.assertRaises(Stop) as caught:
                        self.check(observed, mode)
                    self.assertEqual(caught.exception.code, code)
                with self.assertRaises(Stop) as caught:
                    plan_child(target, execution_state(1000), observed, config["execution"], 1000)
                self.assertEqual(caught.exception.code, code)

    def test_replacement_feed_rejected_before_strategy_or_executor_receives_wrong_bars(self):
        config = self.configure()
        for market, interval in (("BTC", "1m"), ("ETH", "5m")):
            with self.subTest(market=market, interval=interval):
                bad = Bar(market, interval, 0, 59999, *([Decimal(100)] * 4), Decimal(1))
                feed = Mock(healthy=True, done=False, spec=FixtureFeed)
                feed.poll.return_value = BarBatch((bad,), True)
                strategy, executor = Mock(), Mock()
                runtime = Runtime(config, MemoryJournal(), worker=ImmediateIO(), reporter=Mock(),
                                  feed=feed, strategy=strategy, executor_factory=lambda *args: executor)
                with self.assertRaises(Stop) as caught:
                    runtime.tick()
                self.assertEqual(caught.exception.code, "CANDLE_INVALID")
                strategy.evaluate.assert_not_called()
                executor.set_target.assert_not_called()
                executor.step.assert_not_called()

    def test_missing_or_invalid_configuration_does_not_fall_back_to_btc_or_read_account(self):
        args = parser().parse_args(["check", "--vault", LINK, "--directory", str(self.root), "--config", str(self.path)])
        for contents in (None, "{}", '{"market":"BTC"}'):
            if contents is not None:
                self.path.write_text(contents)
            with patch("adapter.Access", side_effect=AssertionError("account read")), self.assertRaises(Stop) as caught:
                run(args)
            self.assertEqual(caught.exception.code, "PUBLIC_CONFIG_INVALID")
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(["check", "--vault", LINK, "--directory", str(self.root), "--market", "BTC"])

    def test_changed_market_cannot_reuse_another_markets_retained_runtime(self):
        config = self.configure("BTC")
        journal = MemoryJournal()
        Runtime(config, journal, worker=ImmediateIO(), reporter=Mock()).commit()
        original = copy.deepcopy(journal.value)
        config = self.configure("ETH")
        with self.assertRaises(Stop) as caught:
            Runtime(config, journal, worker=ImmediateIO(), reporter=Mock())
        self.assertEqual(caught.exception.code, "RUNTIME_POLICY_CHANGED")
        self.assertEqual(journal.value, original)
