"""Disposable fixtures and mocked execution only. No real order or transfer."""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapter import Access, Adapter, classify_submission, fresh, public_candles
from execution import COLLATERAL, SMOKE, collateral, collateral_plan, review_collateral, smoke
from journal import Journal, account_lock
from risk import Bounds, Stop, close_plan, entry_plan, number, price_limit
from runner import main, parser, run
from strategy import crossover, paper

VAULT = "0x" + "a" * 40
ACCOUNT = "0x" + "b" * 40
KEY = "0x" + "c" * 40
OWNER = "0x" + "d" * 40
APPROVAL = "0x" + "e" * 40
LINK = "https://2n20.org/vaults/" + VAULT + "?network=mainnet"
SCOPE = {"vault": VAULT, "network": "mainnet", "chainId": 999, "approvalContract": APPROVAL,
         "tradingAccount": ACCOUNT, "owner": OWNER, "tradingKey": KEY}


def snapshot(**change):
    return {"market": "BTC", "decimals": 3, "mark": Decimal("100"), "bid": Decimal("99.9"),
            "ask": Decimal("100.1"), "positions": {}, "orders": [],
            "perp_available": Decimal("20"), "spot_available": Decimal("20"),
            "taker_fee": Decimal("0.00045"), **change}


class Clock:
    def __init__(self): self.value = 1000
    def time(self): return self.value
    def sleep(self, delay): self.value += delay


class FakeAdapter:
    def __init__(self, journal, entry_fill="0.11", close_fill=None, ambiguous=False, rejection=False):
        self.journal, self.calls = journal, []
        self.entry_fill = Decimal(entry_fill)
        self.close_fill = self.entry_fill if close_fill is None else Decimal(close_fill)
        self.ambiguous, self.rejection, self.interrupt, self.revoked = ambiguous, rejection, False, False
        self.conflict, self.transfer, self.foreign_activity = False, False, False

    def snapshot(self, market):
        if self.revoked: raise Stop("ACCESS_UNVERIFIED", "Revoked fixture")
        position = Decimal(0)
        if self.calls and isinstance(self.calls[0][0], dict) and not self.ambiguous and not self.rejection:
            direction = 1 if self.calls[0][0]["buy"] else -1
            position = self.entry_fill * direction
            if len(self.calls) > 1: position -= self.close_fill * direction
        result = snapshot(positions={"BTC": position} if position else {})
        if self.conflict: result["positions"]["ETH"] = Decimal(1)
        if self.transfer: result.update(spot_available=Decimal(15), perp_available=Decimal(25))
        return result

    def submit_order(self, plan, cloid, expires_ms):
        value = self.journal.read(SMOKE)
        assert value["phase"] == ("close_pending" if plan["reduceOnly"] else "entry_pending")
        assert value["closeCloid" if plan["reduceOnly"] else "entryCloid"] == cloid
        self.calls.append((plan, cloid, expires_ms))
        if self.interrupt: raise KeyboardInterrupt()
        return {"classification": "rejected", "code": "ORDER_REJECTED"} if self.rejection else {"classification": "ambiguous" if self.ambiguous else "accepted_unverified"}

    def order_evidence(self, cloid, plan, started):
        if self.ambiguous or self.rejection: return {"known": False, "terminal": False, "filled": "0"}
        return {"known": True, "terminal": True, "filled": str(self.close_fill if plan["reduceOnly"] else self.entry_fill), "oid": 1 if plan["reduceOnly"] else 0}

    def usdc_token(self): return "USDC:0x" + "f" * 32
    def submit_collateral(self, amount, nonce, token, market="BTC"):
        assert self.journal.read(COLLATERAL)["phase"] == "pending"
        self.calls.append((str(amount), nonce, token))
        self.transfer = not self.ambiguous
        return "ambiguous" if self.ambiguous else "submitted_unverified"

    def verify_activity(self, cloids, started_ms):
        if self.foreign_activity: raise Stop("ACCOUNT_ACTIVITY_CONFLICT", "Foreign fill fixture")


class StrategyTests(unittest.TestCase):
    def test_default_paper_never_imports_signer_or_sdk(self):
        with patch("adapter.sdk_types", side_effect=AssertionError("SDK loaded")), patch("twon20.files.load_key", side_effect=AssertionError("key loaded")):
            result, code = run(parser().parse_args([]))
        self.assertEqual(code, 0)
        self.assertEqual(result["ordersSubmitted"], 0)
        self.assertFalse(result["signerLoaded"])
        self.assertTrue(result["decisions"])

    def test_pure_crossover_and_empty_invalid(self):
        self.assertEqual(crossover([100, 99, 98, 97, 96, 97, 100]), "buy")
        for values in ([], [0], ["NaN"]):
            with self.assertRaises(ValueError): paper(values)

    def test_paper_reproducible_and_finite(self):
        values = [100, 99, 98, 97, 96, 97, 100, 105, 101, 95, 90]
        self.assertEqual(paper(values), paper(values))
        self.assertEqual(paper(values)["ordersSubmitted"], 0)

    def test_public_data_never_signs(self):
        now = 1790000000000
        info = Mock()
        info.meta.return_value = {"universe": [{"name": "BTC"}]}
        info.candles_snapshot.return_value = [{"t": now - (8-i)*60000, "T": now - (7-i)*60000, "c": "100"} for i in range(7)]
        with patch("adapter.time.time", return_value=now/1000), patch("adapter.public_info", return_value=info), patch("twon20.files.load_key", side_effect=AssertionError()):
            self.assertEqual(len(public_candles("BTC")), 7)

    def test_forming_candle_is_excluded_from_prices_and_freshness(self):
        now = 1_800_030_000
        boundary = 1_800_000_000
        completed = [{"t": boundary - (6-i)*60000, "T": boundary - (5-i)*60000 - 1,
                      "c": str(100+i)} for i in range(6)]
        forming = {"t": boundary, "T": boundary + 59999, "c": "999"}
        info = Mock()
        info.meta.return_value = {"universe": [{"name": "BTC"}]}
        info.candles_snapshot.return_value = list(reversed(completed + [forming]))
        with patch("adapter.time.time", return_value=now/1000), patch("adapter.public_info", return_value=info):
            self.assertEqual(public_candles("BTC"), [Decimal(100+i) for i in range(6)])

    def test_forming_candle_cannot_hide_stale_completed_prices(self):
        now = 1_800_030_000
        boundary = 1_800_000_000
        completed = [{"t": boundary - (9-i)*60000, "T": boundary - (8-i)*60000 - 1,
                      "c": "100"} for i in range(6)]
        info = Mock()
        info.meta.return_value = {"universe": [{"name": "BTC"}]}
        info.candles_snapshot.return_value = completed + [{"t": boundary, "T": boundary + 59999, "c": "999"}]
        with patch("adapter.time.time", return_value=now/1000), patch("adapter.public_info", return_value=info):
            with self.assertRaises(Stop) as stopped:
                public_candles("BTC")
            self.assertEqual(stopped.exception.code, "PUBLIC_API_UNAVAILABLE")


class RiskTests(unittest.TestCase):
    def setUp(self): self.bounds = Bounds("BTC", "buy", Decimal(12), 20, 30)

    def test_explicit_limits(self):
        for args in (("@1", "buy", Decimal(12), 20, 30), ("BTC", "sell", Decimal(9), 20, 30), ("BTC", "buy", Decimal(101), 20, 30), ("BTC", "buy", Decimal(12), 0, 30), ("BTC", "buy", Decimal(12), 20, 121)):
            with self.assertRaises(Stop): Bounds(*args)
        self.assertEqual(self.bounds.public()["maxOrders"], 2)

    def test_precision_and_minimum_budget(self):
        plan = entry_plan(self.bounds, snapshot())
        self.assertLessEqual(number(plan["size"]) * number(plan["price"]), 12)
        self.assertGreaterEqual(number(plan["size"]) * 100, 10)
        self.assertLessEqual(price_limit("100.123", True, 20, 3), Decimal("100.123") * Decimal("1.002"))
        self.assertGreaterEqual(price_limit("100.123", False, 20, 3), Decimal("100.123") * Decimal("0.998"))
        with self.assertRaises(Stop): entry_plan(Bounds("BTC", "buy", Decimal(10), 20, 30), snapshot(decimals=1))

    def test_no_spot_as_perp_or_leverage_increase(self):
        with self.assertRaises(Stop) as failure: entry_plan(self.bounds, snapshot(perp_available=Decimal(3), spot_available=Decimal(1000)))
        self.assertEqual(failure.exception.code, "PERP_COLLATERAL_REQUIRED")
        with self.assertRaises(Stop): entry_plan(self.bounds, snapshot(taker_fee=Decimal("0.01")))

    def test_existing_conflict_and_exact_reduce_only(self):
        for change in ({"positions": {"ETH": Decimal(1)}}, {"orders": [{"oid": 100}]}):
            with self.assertRaises(Stop): entry_plan(self.bounds, snapshot(**change))
        plan = close_plan(self.bounds, snapshot(positions={"BTC": Decimal("0.03")}), "0.03")
        self.assertTrue(plan["reduceOnly"])
        self.assertEqual(plan["size"], "0.03")
        for positions in ({"BTC": Decimal("0.04")}, {"BTC": Decimal("0.03"), "ETH": Decimal(1)}):
            with self.assertRaises(Stop): close_plan(self.bounds, snapshot(positions=positions), "0.03")

    def test_collateral_explicit_not_entire_balance(self):
        self.assertEqual(collateral_plan(snapshot(), "5")["amount"], "5")
        for amount in ("0", "20", "101", "0.0000001", "NaN"):
            with self.assertRaises(Stop): collateral_plan(snapshot(), amount)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.temp.name).resolve()/"state", SCOPE)
        self.clock = Clock()
        self.bounds = Bounds("BTC", "buy", Decimal(12), 20, 30)
    def tearDown(self): self.journal.close(); self.temp.cleanup()
    def execute(self, adapter, resume=False):
        return smoke(adapter, self.bounds, self.journal, resume, self.clock.time, self.clock.sleep)

    def test_first_roundtrip_and_repeated_execution(self):
        adapter = FakeAdapter(self.journal)
        result = self.execute(adapter)
        self.assertEqual(result["stage"], "complete")
        self.assertEqual(len(adapter.calls), 2)
        self.assertFalse(adapter.calls[0][0]["reduceOnly"])
        self.assertTrue(adapter.calls[1][0]["reduceOnly"])
        self.assertEqual(self.execute(adapter)["ordersSubmitted"], 0)
        self.assertEqual(len(adapter.calls), 2)

    def test_sell_entry_closes_with_a_buy_reduction(self):
        self.bounds = Bounds("BTC", "sell", Decimal(12), 20, 30)
        adapter = FakeAdapter(self.journal)
        self.assertEqual(self.execute(adapter)["stage"], "complete")
        self.assertFalse(adapter.calls[0][0]["buy"])
        self.assertTrue(adapter.calls[1][0]["buy"])

    def test_complete_history_does_not_claim_current_flat_completion(self):
        adapter = FakeAdapter(self.journal)
        self.assertEqual(self.execute(adapter)["stage"], "complete")
        adapter.conflict = True
        result = self.execute(adapter)
        self.assertEqual(result["stage"], "historical_completion")
        self.assertFalse(result["currentCompletionVerified"])
        self.assertEqual(len(adapter.calls), 2)

    def test_resume_close_revalidates_original_fills_and_foreign_activity(self):
        adapter = FakeAdapter(self.journal, ambiguous=True)
        self.execute(adapter)
        adapter.ambiguous = False
        self.assertEqual(self.execute(adapter)["stage"], "recovery_required")
        intent = self.journal.read(SMOKE)
        intent["filled"] = "0.03"
        self.journal.save(SMOKE, intent)
        with patch.object(adapter, "snapshot", return_value=snapshot(positions={"BTC": Decimal("0.03")})):
            with self.assertRaises(Stop) as failure: self.execute(adapter, resume=True)
        self.assertEqual(failure.exception.code, "ENTRY_RECONCILIATION_REQUIRED")
        intent["filled"] = "0.11"
        self.journal.save(SMOKE, intent)
        adapter.foreign_activity = True
        with self.assertRaises(Stop) as failure: self.execute(adapter, resume=True)
        self.assertEqual(failure.exception.code, "ACCOUNT_ACTIVITY_CONFLICT")
        self.assertEqual(len(adapter.calls), 1)

    def test_two_accounts_cannot_overwrite_same_state_directory(self):
        self.journal.save(SMOKE, {"phase": "fixture"})
        other = {**SCOPE, "tradingAccount": "0x"+"f"*40}
        with self.assertRaises(Stop) as failure: Journal(self.journal.output.path, other)
        self.assertEqual(failure.exception.code, "RUNNER_BUSY")
        self.assertEqual(self.journal.read(SMOKE)["phase"], "fixture")

    def test_unresolved_registry_cannot_be_bypassed_with_another_state_directory(self):
        locks = Path(self.temp.name).resolve()/"locks"
        with account_lock(ACCOUNT, locks) as guard:
            guard.reserve(self.journal, SCOPE)
        other = Journal(Path(self.temp.name).resolve()/"another-state", SCOPE)
        try:
            with account_lock(ACCOUNT, locks) as guard:
                with self.assertRaises(Stop) as failure: guard.reserve(other, SCOPE)
                self.assertEqual(failure.exception.code, "ACTIVE_INTENT")
                self.assertEqual(failure.exception.recovery["stateDirectory"], str(self.journal.output.path))
                guard.reserve(self.journal, SCOPE)
                guard.clear()  # Simulated verified completion, never reset on failure.
                guard.reserve(other, SCOPE)
        finally: other.close()

    def test_partial_entry_reduces_only_confirmed_fill(self):
        adapter = FakeAdapter(self.journal, entry_fill="0.03")
        self.assertEqual(self.execute(adapter)["stage"], "complete")
        self.assertEqual(adapter.calls[1][0]["size"], "0.03")

    def test_partial_close_never_retries_or_increases_size(self):
        adapter = FakeAdapter(self.journal, close_fill="0.02")
        result = self.execute(adapter)
        self.assertEqual(result["stage"], "unresolved_exposure")
        self.assertEqual(number(result["remainingExposure"]), Decimal("0.09"))
        self.execute(adapter, resume=True)
        self.assertEqual(len(adapter.calls), 2)

    def test_ambiguous_response_restart_never_reenters(self):
        adapter = FakeAdapter(self.journal, ambiguous=True)
        result = self.execute(adapter)
        self.assertEqual(result["stage"], "unresolved_evidence")
        self.assertEqual(len(adapter.calls), 1)
        self.execute(adapter)
        self.assertEqual(len(adapter.calls), 1)
        adapter.ambiguous = False
        self.assertEqual(self.execute(adapter)["stage"], "recovery_required")
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.execute(adapter, resume=True)["stage"], "complete")
        self.assertEqual(len(adapter.calls), 2)

    def test_interruption_intent_durable_before_submission(self):
        adapter = FakeAdapter(self.journal)
        adapter.interrupt = True
        with self.assertRaises(KeyboardInterrupt): self.execute(adapter)
        self.assertEqual(self.journal.read(SMOKE)["phase"], "entry_pending")
        adapter.interrupt = False
        self.assertEqual(self.execute(adapter)["stage"], "recovery_required")
        self.assertEqual(len(adapter.calls), 1)

    def test_revocation_after_entry_stops_no_blind_close(self):
        adapter = FakeAdapter(self.journal, ambiguous=True)
        self.execute(adapter)
        adapter.revoked = True
        with self.assertRaises(Stop): self.execute(adapter, resume=True)
        self.assertEqual(len(adapter.calls), 1)

    def test_wrong_scope_bound_and_conflicting_positions(self):
        adapter = FakeAdapter(self.journal, ambiguous=True)
        self.execute(adapter)
        with self.assertRaises(Stop): smoke(adapter, Bounds("BTC", "buy", Decimal(13), 20, 30), self.journal)
        adapter.ambiguous, adapter.conflict = False, True
        with self.assertRaises(Stop): self.execute(adapter, resume=True)
        self.assertEqual(len(adapter.calls), 1)

    def test_rejection_error_surfaces_without_raw_provider_text(self):
        adapter = FakeAdapter(self.journal, rejection=True)
        result = self.execute(adapter)
        self.assertEqual(result["stage"], "submission_rejected")
        self.assertEqual(result["submissionCode"], "ORDER_REJECTED")
        self.execute(adapter)
        self.assertEqual(len(adapter.calls), 1)
        classified = classify_submission({"status": "ok", "response": {"data": {"statuses": [{"error": "CONFIDENTIAL_PROVIDER_TEXT"}]}}})
        self.assertNotIn("CONFIDENTIAL", json.dumps(classified))

    def test_collateral_ambiguity_and_balance_change_are_not_identity_proof(self):
        adapter = FakeAdapter(self.journal, ambiguous=True)
        result = collateral(adapter, self.journal, "5", "BTC", self.clock.time, self.clock.sleep)
        self.assertEqual(result["stage"], "unresolved_evidence")
        adapter.transfer = True
        result = collateral(adapter, self.journal, "5", "BTC", self.clock.time, self.clock.sleep)
        self.assertEqual(result["stage"], "balance_change_observed")
        self.assertFalse(result["transferIdentityVerified"])
        collateral(adapter, self.journal, "5", "BTC", self.clock.time, self.clock.sleep)
        self.assertEqual(len(adapter.calls), 1)
        with self.assertRaises(Stop): collateral(adapter, self.journal, "6", "BTC", self.clock.time, self.clock.sleep)

    def test_collateral_recovery_review_and_exact_human_acknowledgement_never_submit(self):
        adapter = FakeAdapter(self.journal)
        self.assertEqual(collateral(adapter, self.journal, "5", "BTC", self.clock.time, self.clock.sleep)["stage"], "balance_change_observed")
        before = self.journal.read(COLLATERAL)
        with patch.object(adapter, "submit_order", side_effect=AssertionError("order submitted")), patch.object(adapter, "submit_collateral", side_effect=AssertionError("transfer submitted")):
            result = review_collateral(adapter, self.journal, "BTC")
            self.assertEqual(result["stage"], "recovery_review")
            self.assertFalse(result["signerLoaded"])
            self.assertEqual(self.journal.read(COLLATERAL), before)
            with self.assertRaises(Stop): review_collateral(adapter, self.journal, "BTC", True, before["nonce"] + 1)
            self.assertEqual(self.journal.read(COLLATERAL), before)
            result = review_collateral(adapter, self.journal, "BTC", True, before["nonce"])
        self.assertEqual(result["stage"], "recovery_acknowledged")
        self.assertFalse(result["transferIdentityVerified"])
        self.assertEqual(self.journal.read(COLLATERAL)["phase"], "human_acknowledged")
        self.assertEqual(len(adapter.calls), 1)

    def test_collateral_ack_refuses_pending_conflicts_and_unresolved_orders(self):
        adapter = FakeAdapter(self.journal, ambiguous=True)
        collateral(adapter, self.journal, "5", "BTC", self.clock.time, self.clock.sleep)
        intent = self.journal.read(COLLATERAL)
        with self.assertRaises(Stop): review_collateral(adapter, self.journal, "BTC", True, intent["nonce"])
        intent["phase"] = "balance_observed"; self.journal.save(COLLATERAL, intent)
        adapter.conflict = True
        with self.assertRaises(Stop): review_collateral(adapter, self.journal, "BTC", True, intent["nonce"])
        adapter.conflict = False
        self.journal.save(SMOKE, {"phase": "entry_pending"})
        with self.assertRaises(Stop): review_collateral(adapter, self.journal, "BTC", True, intent["nonce"])
        self.assertEqual(self.journal.read(COLLATERAL)["phase"], "balance_observed")

    def test_runner_registry_retained_for_observed_transfer_and_later_completed_smoke(self):
        adapter = FakeAdapter(self.journal)
        collateral(adapter, self.journal, "5", "BTC", self.clock.time, self.clock.sleep)
        access = Mock(); access.scope = SCOPE; access.directory = Path("/placeholder/setup"); access.vault = LINK
        guard = Mock()
        @contextlib.contextmanager
        def lock(account): yield guard
        argv = ["--vault", LINK, "--directory", "/placeholder/setup", "--state-directory", str(self.journal.output.path)]
        with patch("adapter.Access", return_value=access), patch("adapter.Adapter", return_value=adapter), patch("journal.account_lock", side_effect=lock), patch("journal.Journal", return_value=self.journal), patch.object(self.journal, "close"):
            result, code = run(parser().parse_args(["collateral", *argv, "--amount", "5", "--execute", "TRANSFER_TO_PERPS"]))
        self.assertEqual(result["stage"], "balance_change_observed")
        self.assertEqual(code, 2)
        guard.clear.assert_not_called()
        live_adapter = FakeAdapter(self.journal)
        live_adapter.transfer = True
        with patch("adapter.Access", return_value=access), patch("adapter.Adapter", return_value=live_adapter), patch("journal.account_lock", side_effect=lock), patch("journal.Journal", return_value=self.journal), patch.object(self.journal, "close"):
            result, code = run(parser().parse_args(["live-smoke", *argv, "--market", "BTC", "--side", "buy", "--max-notional", "12", "--slippage-bps", "20", "--duration-seconds", "30", "--execute", "LIVE_SMOKE"]))
        self.assertEqual(result["stage"], "complete")
        self.assertEqual(code, 0)
        guard.clear.assert_not_called()

    def test_unfilled_entry_returns_nonzero_without_resubmitting_on_resume(self):
        adapter = FakeAdapter(self.journal, entry_fill="0")
        access = Mock(); access.scope = SCOPE; access.directory = Path("/placeholder/setup"); access.vault = LINK
        guard = Mock()
        @contextlib.contextmanager
        def lock(account): yield guard
        argv = ["live-smoke", "--vault", LINK, "--directory", "/placeholder/setup",
                "--state-directory", str(self.journal.output.path), "--market", "BTC", "--side", "buy",
                "--max-notional", "12", "--slippage-bps", "20", "--duration-seconds", "30", "--execute", "LIVE_SMOKE"]
        with patch("adapter.Access", return_value=access), patch("adapter.Adapter", return_value=adapter), patch("journal.account_lock", side_effect=lock), patch("journal.Journal", return_value=self.journal), patch.object(self.journal, "close"):
            for _ in range(2):
                result, code = run(parser().parse_args(argv))
                self.assertEqual(result["stage"], "entry_rejected")
                self.assertEqual(code, 2, "No fill is not a successful trading test")
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.journal.read(SMOKE)["phase"], "entry_rejected")
        self.assertEqual(guard.clear.call_count, 2)

    def test_journal_permissions_atomic_updates_and_symlink_refusal(self):
        self.journal.save(SMOKE, {"phase": "fixture"})
        path = Path(self.journal.public_path(SMOKE))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertIn("*", (path.parent/".gitignore").read_text())
        path.unlink(); path.symlink_to(Path(self.temp.name).resolve()/"outside")
        with self.assertRaises(Exception): self.journal.save(SMOKE, {"phase": "other"})

    def test_account_lock_is_cross_project_and_exclusive(self):
        root = Path(self.temp.name).resolve()/"locks"
        with account_lock(ACCOUNT, root):
            with self.assertRaises(Stop) as failure:
                with account_lock(ACCOUNT, root): pass
        self.assertEqual(failure.exception.code, "RUNNER_BUSY")


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name).resolve()/"setup"
        self.path.mkdir(mode=0o700)
        # A redacted fixture intentionally cannot be a signing key. The check
        # only stats this file; tests fail if private loading occurs.
        self.write("strategy.key", "DISPOSABLE_REDACTED_PLACEHOLDER")
        self.write("setup.public.json", json.dumps({"schema": "2n20-local-setup-v1", "vaultLink": LINK, "tradingKey": KEY}))
        self.found = SimpleNamespace(network="mainnet", anchor={"chainId": 999}, vault=VAULT, trading_account=ACCOUNT, approval_contract=APPROVAL, owner=OWNER, requested_key=KEY)
        self.patches = [patch("twon20.discovery.discover", return_value=self.found), patch("twon20.status.status", return_value={"tradingAccessReady": True, "evidence": "verified-public-reads"}), patch("twon20.files.load_key", side_effect=AssertionError("private loading forbidden in check"))]
        for mocking in self.patches: mocking.start()
    def tearDown(self):
        for mocking in reversed(self.patches): mocking.stop()
        self.temp.cleanup()
    def write(self, name, text):
        path = self.path/name
        path.write_text(text); path.chmod(0o600)

    def test_published_directory_read_only_and_owner_checkpoint(self):
        access = Access(LINK, self.path)
        self.assertEqual(access.scope["tradingAccount"], ACCOUNT)
        access.close()
        self.write("onboarding.public.json", "{}")
        with patch("twon20.onboarding.load_state", side_effect=Stop("OWNER_CHANGED", "Changed fixture owner")):
            with self.assertRaises(Stop): Access(LINK, self.path)

    def test_wrong_network_key_legacy_account_and_permissions(self):
        cases = [("network", "testnet"), ("requested_key", "0x"+"f"*40), ("trading_account", VAULT)]
        for field, value in cases:
            old = getattr(self.found, field); setattr(self.found, field, value)
            with self.assertRaises(Stop): Access(LINK, self.path)
            setattr(self.found, field, old)
        (self.path/"strategy.key").chmod(0o644)
        with self.assertRaises(Stop): Access(LINK, self.path)

    def test_public_evidence_unavailable_never_ready(self):
        with patch("twon20.status.status", return_value={"tradingAccessReady": False, "evidence": "unavailable"}):
            with self.assertRaises(Stop) as failure: Access(LINK, self.path)
        self.assertEqual(failure.exception.code, "ACCESS_UNVERIFIED")


class AdapterTests(unittest.TestCase):
    def public_fixture(self):
        access = Mock(); access.scope = SCOPE
        info = Mock()
        now = 1790000000000
        info.meta_and_asset_ctxs.return_value = [{"universe": [{"name": "BTC", "szDecimals": 3}]}, [{"markPx": "100"}]]
        info.l2_snapshot.return_value = {"coin": "BTC", "time": now, "levels": [[{"px": "99.9", "sz": "1"}], [{"px": "100.1", "sz": "1"}]]}
        info.user_state.return_value = {"time": now, "marginSummary": {"accountValue": "20", "totalMarginUsed": "0"}, "withdrawable": "20", "assetPositions": []}
        info.open_orders.return_value = []
        info.frontend_open_orders.return_value = []
        info.perp_dexs.return_value = [None]
        info.spot_user_state.return_value = {"balances": [{"coin": "USDC", "token": 0, "total": "30", "hold": "10"}]}
        info.user_fees.return_value = {"userCrossRate": "0.00045"}
        return Adapter(access, info=info), info, now

    def test_complete_snapshot_binds_account_and_separate_collateral(self):
        adapter, info, now = self.public_fixture()
        with patch("adapter.time.time", return_value=now/1000):
            result = adapter.snapshot("BTC")
        self.assertEqual(result["perp_available"], Decimal(20))
        self.assertEqual(result["spot_available"], Decimal(20))
        info.user_state.assert_called_with(ACCOUNT)
        info.open_orders.assert_called_with(ACCOUNT)
        info.spot_user_state.assert_called_with(ACCOUNT)
        adapter.access.signer.assert_not_called()

    def test_snapshot_rejects_stale_market_balance_and_unsupported_precision(self):
        for field in ("book", "balance", "precision", "market", "spot", "duplicate_positions", "boolean_token"):
            adapter, info, now = self.public_fixture()
            if field == "book": info.l2_snapshot.return_value["time"] = now - 6000
            if field == "balance": info.user_state.return_value["time"] = now - 16000
            if field == "precision": info.meta_and_asset_ctxs.return_value[0]["universe"][0]["szDecimals"] = 7
            if field == "market": info.meta_and_asset_ctxs.return_value[0]["universe"][0]["isDelisted"] = True
            if field == "spot": info.spot_user_state.return_value["balances"][0]["coin"] = "OTHER"
            if field == "duplicate_positions": info.user_state.return_value["assetPositions"] = [{"position": {"coin": "BTC", "szi": "0"}}, {"position": {"coin": "BTC", "szi": "0.01"}}]
            if field == "boolean_token": info.spot_user_state.return_value["balances"][0]["token"] = False
            with patch("adapter.time.time", return_value=now/1000):
                with self.assertRaises(Stop): adapter.snapshot("BTC")
            adapter.access.signer.assert_not_called()

    def test_builder_dex_conflict_or_unavailable_evidence_refuses(self):
        adapter, info, now = self.public_fixture()
        info.perp_dexs.return_value = [None, {"name": "builder"}]
        builder_info = Mock()
        builder_info.user_state.return_value = {"time": now, "assetPositions": [{"position": {"szi": "1"}}]}
        builder_info.open_orders.return_value = []
        with patch("adapter.sdk_types", return_value=(Mock(return_value=builder_info), None, None)), patch("adapter.time.time", return_value=now/1000):
            with self.assertRaises(Stop) as failure: adapter.snapshot("BTC")
        self.assertEqual(failure.exception.code, "ACCOUNT_CONFLICT")
        builder_info.user_state.side_effect = RuntimeError("CONFIDENTIAL_REMOTE_DATA")
        with patch("adapter.sdk_types", return_value=(Mock(return_value=builder_info), None, None)), patch("adapter.time.time", return_value=now/1000):
            with self.assertRaises(Stop) as failure: adapter.snapshot("BTC")
        self.assertNotIn("CONFIDENTIAL", str(failure.exception))

    def test_read_only_check_and_both_previews_never_load_signer_or_journal(self):
        argv_base = ["--vault", LINK, "--directory", "/placeholder/setup", "--market", "BTC"]
        for name, extra in (("check", []), ("live-smoke", ["--side", "buy", "--max-notional", "12", "--slippage-bps", "20", "--duration-seconds", "30"]), ("collateral", ["--amount", "5"])):
            access = Mock(); access.scope = SCOPE; access.directory = Path("/placeholder/setup"); access.vault = LINK
            adapter = Mock(); adapter.snapshot.return_value = snapshot()
            with patch("adapter.Access", return_value=access), patch("adapter.Adapter", return_value=adapter), patch("journal.Journal", side_effect=AssertionError("journal creation")):
                result, code = run(parser().parse_args([name, *argv_base, *extra]))
            self.assertEqual(code, 0)
            self.assertFalse(result["signerLoaded"])
            access.signer.assert_not_called()
            adapter.submit_order.assert_not_called()
            adapter.submit_collateral.assert_not_called()

    def test_actual_dispatch_rechecks_changed_slippage_margin_state_and_deadline(self):
        plan = entry_plan(Bounds("BTC", "buy", Decimal(12), 20, 30), snapshot())
        adapter = Adapter(Mock(), info=Mock())
        for current, expires in ((snapshot(ask=Decimal(80)), 1790000030000), (snapshot(perp_available=Decimal(1)), 1790000030000), (snapshot(positions={"ETH": Decimal(1)}), 1790000030000), (snapshot(), 1789999999000)):
            exchange = Mock()
            with patch.object(adapter, "exchange", return_value=exchange), patch.object(adapter, "snapshot", return_value=current), patch("adapter.time.time", return_value=1790000000):
                with self.assertRaises(Stop): adapter.submit_order(plan, "0x"+"1"*32, expires)
            exchange.order.assert_not_called()

    def test_expired_deadline_refuses_before_sdk_or_signer_initialization(self):
        adapter = Adapter(Mock(), info=Mock())
        with patch("adapter.sdk_types", side_effect=AssertionError("SDK initialized")), patch("adapter.time.time", return_value=1000):
            with self.assertRaises(Stop) as failure: adapter.submit_order({}, "0x"+"1"*32, 999000)
        self.assertEqual(failure.exception.code, "DEADLINE_EXPIRED")
        adapter.access.signer.assert_not_called()

    def test_collateral_last_moment_checks_precede_signer_and_boolean_token_refused(self):
        adapter = Adapter(Mock(), info=Mock())
        for current in (snapshot(spot_available=Decimal(5)), snapshot(positions={"BTC": Decimal(1)})):
            with patch.object(adapter, "snapshot", return_value=current), patch.object(adapter, "exchange", side_effect=AssertionError("signer initialized")):
                with self.assertRaises(Stop): adapter.submit_collateral(Decimal(5), 1000000, "USDC:0x"+"f"*32)
        adapter.info.spot_meta.return_value = {"tokens": [{"index": False, "name": "USDC", "tokenId": "0x"+"f"*32}]}
        with self.assertRaises(Stop): adapter.usdc_token()

    def test_implicit_resume_cannot_echo_invalid_vault_credentials(self):
        sentinel = "DO_NOT_ECHO_INVALID_CREDENTIALS"
        argv = ["live-smoke", "--vault", "https://user:"+sentinel+"@2n20.org/vaults/"+VAULT,
                "--directory", "/placeholder/setup", "--market", "BTC", "--side", "buy", "--max-notional", "12",
                "--slippage-bps", "20", "--duration-seconds", "30", "--execute", "LIVE_SMOKE"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output): code = main(argv)
        self.assertEqual(code, 3)
        self.assertNotIn(sentinel, output.getvalue())
        self.assertNotIn("resumeCommand", json.loads(output.getvalue()))

    def test_foreign_post_intent_fills_are_refused_even_if_net_position_matches(self):
        access = Mock(); access.scope = SCOPE
        info = Mock(); adapter = Adapter(access, info=info)
        cloid = "0x"+"1"*32
        info.query_order_by_cloid.return_value = {"status": "order", "order": {"order": {"cloid": cloid, "oid": 1}}}
        info.user_fills_by_time.return_value = [{"oid": 1, "tid": 1, "time": 1000}, {"oid": 2, "tid": 2, "time": 1001}]
        with self.assertRaises(Stop) as failure: adapter.verify_activity([cloid], 1000)
        self.assertEqual(failure.exception.code, "ACCOUNT_ACTIVITY_CONFLICT")

    def test_sdk_debug_payload_logging_is_disabled_before_signer(self):
        access = Mock(); access.scope = SCOPE
        def signer():
            self.assertEqual(logging.root.manager.disable, logging.CRITICAL)
            return Mock()
        access.signer.side_effect = signer
        adapter = Adapter(access, info=Mock())
        previous = logging.root.manager.disable
        try:
            with patch("adapter.sdk_types", return_value=(None, Mock(), None)):
                adapter.exchange()
        finally:
            logging.disable(previous)

    def test_stale_public_data_and_sanitized_failure(self):
        with self.assertRaises(Stop): fresh(1)
        adapter = Adapter(Mock(), info=Mock())
        with self.assertRaises(Stop) as failure: adapter.read(Mock(side_effect=RuntimeError("CONFIDENTIAL")))
        self.assertNotIn("CONFIDENTIAL", str(failure.exception))

    def test_real_reviewed_sdk_ioc_and_agent_account_binding(self):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        access = Mock()
        access.scope = SCOPE
        access.signer.return_value = Account.create()  # Disposable memory only.
        real_exchange = Exchange(access.signer(), "https://api.hyperliquid.xyz", account_address=ACCOUNT,
                                 vault_address=None, timeout=5,
                                 meta={"universe": [{"name": "BTC", "szDecimals": 3}]},
                                 spot_meta={"tokens": [], "universe": []})
        real_exchange.set_expires_after(1790000030000)
        captured = []
        def transport(url, body):
            captured.append((url, body))
            return {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 1}}]}}}
        adapter = Adapter(access, info=Mock())
        with patch.object(adapter, "exchange", return_value=real_exchange), patch.object(adapter, "snapshot", return_value=snapshot()), patch.object(adapter, "usdc_token", return_value="USDC:0x"+"f"*32), patch("adapter.time.time", return_value=1790000000), patch.object(real_exchange, "post", side_effect=transport):
            result = adapter.submit_order(entry_plan(Bounds("BTC", "buy", Decimal(12), 20, 30), snapshot()), "0x"+"1"*32, 1790000030000)
            adapter.submit_collateral(Decimal(5), 1790000000000, "USDC:0x"+"f"*32)
        self.assertEqual(result["classification"], "accepted_unverified")
        order = captured[0][1]["action"]["orders"][0]
        self.assertEqual(order["t"], {"limit": {"tif": "Ioc"}})
        self.assertFalse(order["r"])
        self.assertIsNone(captured[0][1]["vaultAddress"])
        transfer = captured[1][1]["action"]
        self.assertEqual(transfer["type"], "agentSendAsset")
        self.assertEqual(transfer["destination"], ACCOUNT)
        self.assertEqual(transfer["sourceDex"], "spot")
        self.assertEqual(transfer["destinationDex"], "")

    def test_order_evidence_requires_matching_order_fills_and_terminal_state(self):
        access = Mock(); access.scope = SCOPE
        info = Mock(); adapter = Adapter(access, info=info)
        plan = entry_plan(Bounds("BTC", "buy", Decimal(12), 20, 30), snapshot())
        cloid = "0x"+"1"*32
        info.query_order_by_cloid.return_value = {"status": "unknownOid"}
        self.assertFalse(adapter.order_evidence(cloid, plan, 0)["terminal"])
        order = {"cloid": cloid, "coin": "BTC", "side": "B", "origSz": plan["size"], "limitPx": plan["price"], "timestamp": 1, "reduceOnly": False, "oid": 5}
        info.query_order_by_cloid.return_value = {"status": "order", "order": {"status": "canceled", "order": order}}
        info.user_fills_by_time.return_value = [{"oid": 5, "coin": "BTC", "side": "B", "tid": 10, "time": 1, "sz": "0.03", "px": "100"}]
        self.assertEqual(adapter.order_evidence(cloid, plan, 0)["filled"], "0.03")
        info.user_fills_by_time.return_value *= 2
        with self.assertRaises(Stop): adapter.order_evidence(cloid, plan, 0)
        order["coin"] = "ETH"
        with self.assertRaises(Stop): adapter.order_evidence(cloid, plan, 0)

    def test_wrong_scope_and_sdk_response_never_readiness(self):
        for response in ({"status": "ok"}, {"status": "err", "response": "SECRET_PROVIDER_TEXT"}, {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 100}}]}}}):
            result = classify_submission(response)
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertNotIn("ready", result)

    def test_safe_json_error_output_with_resume_path(self):
        argv = ["live-smoke", "--vault", LINK, "--directory", "/fixture/setup", "--market", "BTC", "--side", "buy", "--max-notional", "12", "--slippage-bps", "20", "--duration-seconds", "30", "--execute", "LIVE_SMOKE"]
        output = io.StringIO()
        with patch("runner.run", side_effect=RuntimeError("CONFIDENTIAL_PROVIDER_TEXT")), contextlib.redirect_stdout(output):
            self.assertEqual(main(argv), 3)
        value = json.loads(output.getvalue())
        self.assertNotIn("CONFIDENTIAL", output.getvalue())
        self.assertNotIn("resumeCommand", value)
        self.assertEqual(value["errorCode"], "CHECK_UNAVAILABLE")


if __name__ == "__main__": unittest.main()
