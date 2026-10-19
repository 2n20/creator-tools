import json
import unittest
from unittest.mock import patch

from support import CORE, LINK, VAULT, FakeRpc
from twon20.discovery import discover
from twon20.status import status


class PendingWithdrawalTests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeRpc()
        self.values = {"pendingRequestCount()": 1, "balanceOf(address)": 3,
                       "reservedAssets()": 7, "totalAssets()": 101, "totalSupply()": 10,
                       "uncoveredAssets()": 0}
        original = self.rpc.words

        def words(target, signature, args, block, count):
            if target == VAULT and signature in self.values:
                self.reads.append((signature, block))
                return [self.values[signature]]
            return original(target, signature, args, block, count)

        self.reads = []
        self.rpc.words = words
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)

    def signal(self):
        return status(self.found, self.rpc, self.rpc.now)["pendingWithdrawals"]

    def test_cash_covered_pending_request_is_visible_before_restriction(self):
        signal = self.signal()
        self.assertEqual(signal["evidence"], "verified-public-reads")
        self.assertEqual(signal["pendingRequestCount"], "1")
        self.assertEqual(signal["escrowedShares"], "3")
        self.assertEqual(signal["owedAssets"], "31")
        self.assertEqual(signal["uncoveredAssets"], "0")
        self.assertFalse(signal["restricted"])
        self.assertTrue(all(block == self.found.fixed for _, block in self.reads))

    def test_reserved_claims_remain_distinct_after_pending_shares_clear(self):
        self.values.update({"pendingRequestCount()": 0, "balanceOf(address)": 0})
        signal = self.signal()
        self.assertEqual(signal["pendingRequestCount"], "0")
        self.assertEqual(signal["owedAssets"], "0")
        self.assertEqual(signal["reservedAssets"], "7")

    def test_stale_or_racing_reads_are_unavailable_not_zero(self):
        for race in ("age", "reorg"):
            with self.subTest(race=race):
                self.rpc.reorg = race == "reorg"
                with patch("twon20.withdrawals.time.time", return_value=self.rpc.now + (46 if race == "age" else 0)):
                    signal = self.signal()
                self.assertEqual(signal["evidence"], "unavailable")
                self.assertIsNone(signal["pendingRequestCount"])
                self.assertIsNone(signal["uncoveredAssets"])

    def test_missing_read_sanitizes_provider_errors(self):
        from twon20.errors import SetupError
        with patch.object(self.rpc, "words", side_effect=SetupError("RPC_UNAVAILABLE", "sensitive-disposable-marker")):
            signal = self.signal()
        self.assertEqual(signal["evidence"], "unavailable")
        self.assertNotIn("sensitive-disposable-marker", json.dumps(signal))
        self.assertNotIn("key", json.dumps(signal).lower())

    def test_public_poll_exposes_operation_without_valuing_in_flight_assets(self):
        from twon20.withdrawals import poll_pending_withdrawals
        self.rpc.phase = 2
        self.reads.clear()
        signal = poll_pending_withdrawals({"vault": VAULT, "tradingAccount": CORE,
                                          "approvalContract": self.found.approval_contract}, self.rpc, self.rpc.now)
        self.assertTrue(signal["operationInProgress"])
        self.assertEqual(signal["operationPhase"], 2)
        self.assertIsNone(signal["owedAssets"])
        self.assertEqual(signal["pendingRequestCount"], "1")
        self.assertNotIn("uncoveredAssets()", [signature for signature, _ in self.reads])

    def test_final_canonical_read_time_is_included_in_freshness(self):
        original = self.rpc.call
        with patch("twon20.withdrawals.time.time", return_value=self.rpc.now) as clock:
            def delayed(method, params):
                result = original(method, params)
                clock.return_value = self.rpc.now + 46
                return result
            self.rpc.call = delayed
            self.assertEqual(self.signal()["evidence"], "unavailable")

    def test_queued_operation_with_cleared_observation_still_exposes_demand(self):
        from dataclasses import replace
        from twon20.withdrawals import pending_withdrawals_at
        book = replace(self.found.book, phase=2, observed_at=0, priced=False)
        block = self.rpc.call("eth_getBlockByNumber", ["latest", False])
        signal = pending_withdrawals_at(self.rpc, VAULT, CORE, book,
                                       int(block["number"], 16), block["hash"], int(block["timestamp"], 16), self.rpc.now)
        self.assertEqual(signal["evidence"], "verified-public-reads")
        self.assertEqual(signal["pendingRequestCount"], "1")
        self.assertTrue(signal["operationInProgress"])
        self.assertIsNone(signal["owedAssets"])
        self.assertIsNone(signal["uncoveredAssets"])
        self.assertNotIn("totalAssets()", [signature for signature, _ in self.reads])

        self.found = replace(self.found, book=book)
        self.assertEqual(self.signal()["evidence"], "verified-public-reads")
        self.assertTrue(self.signal()["operationInProgress"])

    def test_unpriced_demand_does_not_claim_a_known_owed_amount(self):
        from dataclasses import replace
        self.found = replace(self.found, book=replace(self.found.book, priced=False, unpriced=1))
        signal = self.signal()
        self.assertEqual(signal["evidence"], "verified-public-reads")
        self.assertEqual(signal["pendingRequestCount"], "1")
        self.assertEqual(signal["reservedAssets"], "7")
        self.assertIsNone(signal["owedAssets"])
        self.assertIsNone(signal["uncoveredAssets"])
