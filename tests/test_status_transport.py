from dataclasses import replace

import unittest
import io
import json
import urllib.error
from unittest.mock import patch

from twon20.discovery import discover
from twon20.errors import SetupError
from twon20.onboarding import HANDOFF_ERRORS, HANDOFF_TIMEOUT_SECONDS
from twon20.rpc import Rpc, request_json
from twon20.status import cash_target_public, poll_lifecycle, status

from support import CORE, KEY, LINK, OWNER, FakeRpc


class StatusTests(unittest.TestCase):
    def test_approval_timeout_allows_server_verification_without_slowing_rpc_timeouts(self):
        with patch("twon20.rpc.urllib.request.OpenerDirector.open") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b'{}'
            request_json("https://2n20.org/api/sdk/approval-requests", {}, timeout=HANDOFF_TIMEOUT_SECONDS)
            self.assertEqual(opened.call_args.kwargs["timeout"], 65)
            request_json("https://rpc.hyperliquid.xyz/evm", {})
            self.assertEqual(opened.call_args.kwargs["timeout"], 15)

    def test_unavailable_contract_evidence_keeps_its_safe_handoff_reason(self):
        body = json.dumps({"code": "EVIDENCE_UNAVAILABLE", "message": "sensitive-disposable-marker", "error": "provider details"}).encode()
        error = urllib.error.HTTPError("https://2n20.org/api/sdk/approval-requests", 503, "unavailable", {}, io.BytesIO(body))
        with patch("twon20.rpc.urllib.request.OpenerDirector.open", side_effect=error), self.assertRaises(SetupError) as caught:
            request_json(error.url, {}, error_messages=HANDOFF_ERRORS)
        self.assertEqual(caught.exception.code, "EVIDENCE_UNAVAILABLE")
        self.assertNotIn("sensitive-disposable-marker", caught.exception.message)
        self.assertIn("retained", caught.exception.message)

    def setUp(self):
        self.rpc = FakeRpc()
        self.rpc.key = [int(KEY, 16), 0, 1]
        self.rpc.key_status = [int(KEY, 16), self.rpc.now + 20 * 86400, int(OWNER, 16), 0]
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)

    def info(self, payload):
        return {"userRole": {"role": "agent", "data": {"user": CORE}},
                "extraAgents": [{"address": KEY, "name": "2n20", "validUntil": (self.rpc.now + 3600) * 1000}],
                "userAbstraction": "disabled", "spotClearinghouseState": {"balances": [{"token": 0, "coin": "USDC", "total": "3", "hold": "0"}]},
                "clearinghouseState": {"time": self.rpc.now * 1000, "marginSummary": {"accountValue": "0"}}}[payload["type"]]

    def release(self, active=False, opened=False):
        return {"active": active, "noticeAt": self.rpc.notice_at, "roundSeconds": 600, "openedThisRound": opened,
                "misses": self.rpc.misses, "lastMissAt": self.rpc.last_miss_at, "cutsAllowedAt": self.rpc.served_cuts()}

    def check(self, info=None):
        return status(self.found, self.rpc, self.rpc.now, info or self.info)

    def test_cash_target_rounds_up_and_pins_all_reads_to_one_block(self):
        values = {"liquidityBufferBps()": 1000, "totalAssets()": 101,
                  "reservedAssets()": 20, "balanceOf(address)": 26, "allocatableAssets()": 0}
        original = self.rpc.words
        reads = []

        def words(target, signature, args, block, count):
            if signature in values:
                reads.append((target, signature, args, block))
                return [values[signature]]
            return original(target, signature, args, block, count)

        with patch.object(self.rpc, "words", side_effect=words):
            target = cash_target_public(self.found, self.rpc, self.rpc.now)
        self.assertEqual(target["evidence"], "verified-public-reads")
        self.assertEqual(target["bps"], 1000)
        self.assertEqual(target["targetAssets"], "11")
        self.assertEqual(target["actualUnreservedAssets"], "6")
        self.assertEqual(target["allocatableAssets"], "0")
        self.assertEqual(target["blockHash"], self.found.block_hash)
        self.assertEqual(len(reads), 5)
        self.assertTrue(all(read[3] == self.found.fixed for read in reads))
        balance = next(read for read in reads if read[1] == "balanceOf(address)")
        self.assertEqual(balance[0], self.found.anchor["contracts"]["MockUSDC"].lower())
        self.assertEqual(balance[2], [self.found.vault])

    def test_cash_target_missing_evidence_is_null_without_blocking_setup(self):
        for signature in ("liquidityBufferBps()", "totalAssets()", "reservedAssets()", "balanceOf(address)", "allocatableAssets()"):
            with self.subTest(signature=signature):
                self.rpc.fail = signature
                value = self.check()
                self.assertTrue(value["tradingAccessReady"])
                self.assertEqual(value["cashTarget"]["evidence"], "unavailable")
                self.assertIsNone(value["cashTarget"]["targetAssets"])
                self.assertIsNone(value["cashTarget"]["actualUnreservedAssets"])

    def test_cash_target_rejects_stale_unpriced_pending_or_reorganized_evidence(self):
        for found in (replace(self.found, timestamp=self.rpc.now - 301),
                      replace(self.found, book=replace(self.found.book, observed_at=self.rpc.now - 46)),
                      replace(self.found, book=replace(self.found.book, priced=False)),
                      replace(self.found, book=replace(self.found.book, phase=1))):
            self.assertEqual(cash_target_public(found, self.rpc, self.rpc.now)["evidence"], "unavailable")
        self.rpc.reorg = True
        self.assertEqual(cash_target_public(self.found, self.rpc, self.rpc.now)["evidence"], "unavailable")

    def test_cash_target_expiring_during_reads_is_unavailable(self):
        with patch("twon20.status.time.time", return_value=self.rpc.now + 46):
            self.assertEqual(cash_target_public(self.found, self.rpc, self.rpc.now)["evidence"], "unavailable")

    def test_cash_target_expires_during_later_hyperliquid_reads(self):
        self.found = replace(self.found, book=replace(self.found.book, observed_at=self.rpc.now - 44))
        with patch("twon20.status.time.time", return_value=self.rpc.now) as clock:
            def slow_info(payload):
                clock.return_value = self.rpc.now + 2
                return self.info(payload)

            value = self.check(slow_info)
        self.assertEqual(value["cashTarget"]["evidence"], "unavailable")
        self.assertIsNone(value["cashTarget"]["targetAssets"])
        self.assertIsNone(value["cashTarget"]["actualUnreservedAssets"])
        for signature in ("liquidityBufferBps()", "totalAssets()", "reservedAssets()", "balanceOf(address)", "allocatableAssets()"):
            self.assertEqual(sum(call[0] == signature for call in self.rpc.calls),
                             2 if signature in {"totalAssets()", "reservedAssets()", "balanceOf(address)"} else 1)

    def test_cash_target_inconsistent_reserve_is_unavailable(self):
        original = self.rpc.words
        values = {"reservedAssets()": 21, "balanceOf(address)": 20}
        with patch.object(self.rpc, "words", side_effect=lambda target, sig, args, block, count: [values[sig]] if sig in values else original(target, sig, args, block, count)):
            value = self.check()
        self.assertTrue(value["tradingAccessReady"])
        self.assertEqual(value["cashTarget"]["evidence"], "unavailable")
        self.assertIsNone(value["cashTarget"]["actualUnreservedAssets"])

    def test_cash_target_before_account_creation_needs_no_core_report(self):
        found = replace(self.found, trading_account="0x" + "0" * 40,
                        book=replace(self.found.book, priced=False, observed_at=0, report_nonce=0))
        self.assertEqual(cash_target_public(found, self.rpc, self.rpc.now)["evidence"], "verified-public-reads")

    def test_cash_target_zero_maximum_and_fully_reserved_cash(self):
        original = self.rpc.words
        for bps, expected in ((0, "0"), (5000, "51"), (5001, None)):
            values = {"liquidityBufferBps()": bps, "totalAssets()": 101,
                      "reservedAssets()": 20, "balanceOf(address)": 20}
            with patch.object(self.rpc, "words", side_effect=lambda target, sig, args, block, count: [values[sig]] if sig in values else original(target, sig, args, block, count)):
                target = cash_target_public(self.found, self.rpc, self.rpc.now)
            self.assertEqual(target["targetAssets"], expected)
            self.assertEqual(target["actualUnreservedAssets"], "0" if bps <= 5000 else None)

    def test_read_only_ready_with_current_approval_and_funding(self):
        value = self.check()
        self.assertTrue(value["tradingAccessReady"])
        self.assertFalse(value["strategyConnectionTested"])
        self.assertEqual(value["availableUsdc"], "3")
        self.assertEqual(value["release"], self.release())
        self.assertEqual(value["pricing"], {"priced": True, "unpriced": 0, "reasons": [], "action": "priced"})
        self.assertFalse(value["restricted"])
        self.assertNotIn("suspended", value)
        self.assertNotIn("missLimit", value["release"])
        self.assertNotIn("missedRounds", value["release"])
        self.assertEqual(value["keyValidUntil"], self.rpc.now + 20 * 86400)
        self.assertFalse(value["keyExpiryWarning"])
        self.assertEqual(value["creatorAction"], "No action needed.")
        self.assertEqual(value["uncoveredAssets"], "0")

    def test_unavailable_evidence_never_counts_as_ready(self):
        def unavailable(payload):
            raise SetupError("PUBLIC_READ_UNAVAILABLE", "Disposable failure")
        value = self.check(unavailable)
        self.assertFalse(value["tradingAccessReady"])
        self.assertEqual(value["evidence"], "unavailable")

    def test_wrong_agent_expired_agent_mode_balance_and_stale_evidence(self):
        def altered(field, replacement):
            return lambda payload: replacement if payload["type"] == field else self.info(payload)
        for field, value in [("userRole", {"role": "agent", "data": {"user": OWNER}}), ("extraAgents", [{"address": KEY, "name": "2n20", "validUntil": self.rpc.now * 1000}]), ("userAbstraction", "unifiedAccount"), ("spotClearinghouseState", {"balances": []}), ("clearinghouseState", {"time": (self.rpc.now - 60) * 1000, "marginSummary": {"accountValue": "3"}}), ("spotClearinghouseState", {"balances": [{"token": 0, "coin": "USDC", "total": "NaN", "hold": "0"}]})]:
            with self.subTest(field=field):
                self.assertFalse(self.check(altered(field, value))["tradingAccessReady"])

    def test_pending_revoke_and_unconfirmed_contract_fail_closed(self):
        self.rpc.key[1] = 1
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        self.assertFalse(self.check()["tradingAccessReady"])
        self.rpc.key[1] = 0
        self.rpc.key_status[0] = 0
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        self.assertFalse(self.check()["tradingAccessReady"])

    def test_no_key_does_not_require_hypercore_api(self):
        self.rpc.key = [0, 0, 0]
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        with patch("twon20.status.request_json", side_effect=AssertionError("Should not request account evidence")):
            value = self.check()
        self.assertFalse(value["tradingAccessReady"])
        self.assertEqual(value["creatorAction"], "Import public consent into Step 2 and approve it with the creator wallet.")
        self.assertEqual(value["reasons"], [value["creatorAction"]])

    def test_restricted_live_key_needs_no_creator_action(self):
        self.rpc.restricted = 1
        self.rpc.restricted_at = self.rpc.now - 300
        self.rpc.notice_at = self.rpc.restricted_at + 120
        self.rpc.misses = 2
        self.rpc.opened = 1
        self.rpc.uncovered = 2_500_000
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["tradingAccessReady"], True)
        self.assertEqual(value["release"], self.release(True, True))
        self.assertEqual(value["release"]["noticeAt"], self.rpc.now - 180)
        self.assertEqual(value["release"]["cutsAllowedAt"], 1200)
        self.assertEqual(value["restricted"], True)
        self.assertNotIn("suspended", value)
        self.assertNotIn("missLimit", value["release"])
        self.assertEqual(value["uncoveredAssets"], "2500000")
        self.assertEqual(value["creatorAction"], "No action needed.")

    def test_poll_lifecycle_reads_the_same_release(self):
        self.rpc.restricted = 1
        self.rpc.misses = 2
        scope = {"approvalContract": self.found.approval_contract, "vault": self.found.vault, "tradingAccount": self.found.trading_account}
        value = poll_lifecycle(scope, self.rpc, self.rpc.now)
        self.assertEqual(value["release"], self.release(True))
        self.assertEqual(value["creatorAction"], "No action needed.")
        self.assertEqual(value["blockTimestamp"], self.rpc.now)

    def test_release_progress_does_not_invalidate_the_status_read(self):
        self.rpc.restricted = 1
        self.rpc.misses = 1
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        self.rpc.misses = 255
        value = self.check()
        self.assertEqual(value["tradingAccessReady"], True)
        self.assertEqual(value["creatorAction"], "No action needed.")
        self.assertEqual(value["release"]["active"], True)
        self.assertEqual(value["release"]["misses"], 1)

    def test_many_missed_rounds_leave_the_trading_key_in_place(self):
        self.rpc.restricted = 1
        self.rpc.misses = 255
        self.rpc.cuts_allowed_at = self.rpc.now + 86_400
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["tradingAccessReady"], True)
        self.assertEqual(value["release"], self.release(True))
        self.assertEqual(value["release"]["cutsAllowedAt"], self.rpc.now + 86_400)
        self.assertNotIn("suspended", value)
        self.assertNotIn("missLimit", value["release"])
        self.assertEqual(value["creatorAction"], "No action needed.")

    def test_unpriced_price_data_waits_and_keeps_the_key(self):
        self.rpc.priced = 0
        self.rpc.unpriced = 1
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["tradingAccessReady"], True)
        self.assertEqual(value["creatorAction"], "No action needed.")
        self.assertEqual(value["pricing"]["action"], "waits")
        self.assertEqual(value["pricing"]["reasons"], [{
            "name": "PRICE_DATA", "action": "waits",
            "detail": "Hyperliquid has no usable price for collateral the account holds.",
        }])
        self.assertEqual(value["pricing"]["next"], "The vault prices the account again once the data returns.")
        self.assertIn("The key stays approved.", value["pricing"]["whileUnpriced"])
        self.assertIn("holders other than the owner can request withdrawals", value["pricing"]["whileUnpriced"])

    def test_unpriced_abstraction_is_repaired_during_a_release(self):
        self.rpc.priced = 0
        self.rpc.unpriced = 2
        self.rpc.restricted = 1
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["tradingAccessReady"], True)
        self.assertEqual(value["creatorAction"], "No action needed.")
        self.assertEqual(value["pricing"]["action"], "repair")
        self.assertEqual(value["pricing"]["reasons"], [{
            "name": "ABSTRACTION", "action": "repair",
            "detail": "The trading account is not in standard account mode.",
        }])
        self.assertEqual(value["pricing"]["next"], "During a withdrawal release, or while no trading key is active, the vault restores standard account mode.")

    def test_unpriced_staking_needs_the_strategy_and_keeps_the_key(self):
        self.rpc.priced = 0
        self.rpc.unpriced = 8
        self.rpc.restricted = 1
        self.rpc.misses = 255
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["tradingAccessReady"], True)
        self.assertEqual(value["creatorAction"], "No action needed.")
        self.assertEqual(value["release"]["misses"], 255)
        self.assertEqual(value["pricing"]["action"], "strategy")
        self.assertEqual(value["pricing"]["reasons"], [{
            "name": "STAKING", "action": "strategy",
            "detail": "The account holds staked HYPE.",
        }])
        self.assertEqual(value["pricing"]["next"], "The strategy must undo this before the vault can price the account. The vault cannot undo it.")
        self.assertNotIn("fresh trading key", value["creatorAction"])

    def test_unpriced_staking_after_wind_down_has_no_one_to_undo_it(self):
        self.rpc.priced = 0
        self.rpc.unpriced = 8
        self.rpc.restricted = 1
        self.rpc.lifecycle = 1
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["pricing"]["action"], "strategy")
        self.assertEqual(value["pricing"]["next"], "The trading key was removed before wind-down. Neither the vault nor the service can undo this state. While it lasts, no request settles, including requests the vault's cash covers, and wind-down cannot complete.")
        self.assertEqual(value["pricing"]["whileUnpriced"], "Holders can still cancel the unsettled part of a request and claim settled withdrawals, and holders other than the owner can request withdrawals. Share transfers, settlement and the owner's withdrawal requests wait until the account is priced again.")

    def test_a_vault_before_its_first_report_names_no_reason(self):
        self.rpc.core, self.rpc.priced, self.rpc.report_nonce = "0x" + "0" * 40, 0, 0
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["reasons"], ["The trading account has not been created yet. Wait for vault funding on Step 2."])
        self.assertEqual(value["pricing"], {"priced": False, "unpriced": 0, "reasons": [], "action": "unreported", "next": "The first valuation report has not landed yet."})

    def test_pending_revocation_says_wait(self):
        self.rpc.key[1] = 1
        self.rpc.restricted = 1
        self.rpc.misses = 255
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertNotIn("suspended", value)
        self.assertEqual(value["creatorAction"], "A revocation is pending. Wait for it to finish, then approve a fresh trading key. A key that was already used cannot be approved again.")

    def test_expired_key_during_a_release_says_approve_a_fresh_key(self):
        self.rpc.restricted = 1
        self.rpc.misses = 1
        self.rpc.key_status[1] = self.rpc.now
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        value = self.check()
        self.assertEqual(value["release"], self.release(True))
        self.assertNotIn("suspended", value)
        self.assertEqual(value["creatorAction"], "Approve a fresh trading key. A key that was already used cannot be approved again.")

    def test_discovery_rejects_unreviewed_release_rules(self):
        for name, value in (("release_notice", 60), ("release_round", 300)):
            rpc = FakeRpc()
            setattr(rpc, name, value)
            with self.subTest(name=name), self.assertRaises(SetupError) as caught:
                discover(LINK, rpc, rpc.now, rpc.anchor)
            self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
            self.assertEqual(caught.exception.message, "This approval contract's withdrawal release rules are not the reviewed ones. Setup stops.")

    def test_expired_key_and_fourteen_day_warning(self):
        self.rpc.key_status[1] = self.rpc.now + 10 * 86400
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        warning = self.check()
        self.assertTrue(warning["tradingAccessReady"])
        self.assertTrue(warning["keyExpiryWarning"])
        self.assertEqual(warning["keyValidUntil"], self.rpc.now + 10 * 86400)
        self.assertEqual(warning["creatorAction"], "No action needed.")
        self.rpc.key_status[1] = self.rpc.now
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        expired = self.check()
        self.assertFalse(expired["tradingAccessReady"])
        self.assertEqual(expired["creatorAction"], "Approve a fresh trading key. A key that was already used cannot be approved again.")

    def test_rpc_error_messages_do_not_include_response_payloads(self):
        dangerous_body = "sensitive-disposable-marker"
        with patch("twon20.rpc.request_json", return_value={"jsonrpc": "2.0", "id": 1, "error": {"message": dangerous_body}}):
            with self.assertRaises(SetupError) as caught:
                Rpc("mainnet").call("eth_chainId", [])
        self.assertNotIn(dangerous_body, caught.exception.message)

    def test_malformed_rpc_result_and_mismatched_response_id_rejected(self):
        for response in [None, {"result": "0x3e7", "id": 2, "jsonrpc": "2.0"}, {"id": 1, "jsonrpc": "2.0"}, {"id": True, "jsonrpc": "2.0", "result": "0x3e7"}]:
            with patch("twon20.rpc.request_json", return_value=response), self.assertRaises(SetupError):
                Rpc("mainnet").call("eth_chainId", [])

    def test_duplicate_json_fields_and_non_hex_abi_words_are_rejected(self):
        from twon20.rpc import parse_json
        with self.assertRaises(ValueError):
            parse_json('{"result":"0x1","result":"0x2"}')
        for result in ["zz" + "0" * 64, "0x" + "+" + "0" * 63, "0x" + "0" * 63 + "_"]:
            rpc = Rpc("mainnet")
            with patch.object(rpc, "call", return_value=result), self.assertRaises(SetupError):
                rpc.words(CORE, "owner()", [], {"blockHash": "0x" + "1" * 64, "requireCanonical": True}, 1)

    def test_handoff_http_error_accepts_only_allowlisted_code_and_never_remote_message(self):
        body = json.dumps({"code": "HANDOFF_EXPIRED", "message": "sensitive-disposable-marker"}).encode()
        error = urllib.error.HTTPError("https://2n20.org/api/sdk/approval-requests", 410, "expired", {}, io.BytesIO(body))
        with patch("twon20.rpc.urllib.request.OpenerDirector.open", side_effect=error), self.assertRaises(SetupError) as caught:
            request_json(error.url, {}, error_messages={"HANDOFF_EXPIRED": "Local safe expiry instructions"})
        self.assertEqual(caught.exception.code, "HANDOFF_EXPIRED")
        self.assertNotIn("sensitive-disposable-marker", caught.exception.message)
        error = urllib.error.HTTPError(error.url, 410, "expired", {}, io.BytesIO(body))
        with patch("twon20.rpc.urllib.request.OpenerDirector.open", side_effect=error), self.assertRaises(SetupError) as caught:
            request_json(error.url, {})
        self.assertEqual(caught.exception.code, "PUBLIC_READ_UNAVAILABLE")
