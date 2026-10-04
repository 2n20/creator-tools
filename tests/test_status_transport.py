import unittest
import io
import json
import urllib.error
from unittest.mock import patch

from twon20.discovery import discover
from twon20.errors import SetupError
from twon20.onboarding import HANDOFF_ERRORS, HANDOFF_TIMEOUT_SECONDS
from twon20.rpc import Rpc, request_json
from twon20.status import status

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
        self.rpc.key_status = [int(KEY, 16), self.rpc.now + 3600, int(OWNER, 16), 0]
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)

    def info(self, payload):
        return {"userRole": {"role": "agent", "data": {"user": CORE}},
                "extraAgents": [{"address": KEY, "name": "2n20", "validUntil": (self.rpc.now + 3600) * 1000}],
                "userAbstraction": "disabled", "spotClearinghouseState": {"balances": [{"token": 0, "coin": "USDC", "total": "3", "hold": "0"}]},
                "clearinghouseState": {"time": self.rpc.now * 1000, "marginSummary": {"accountValue": "0"}}}[payload["type"]]

    def check(self, info=None):
        return status(self.found, self.rpc, self.rpc.now, info or self.info)

    def test_read_only_ready_with_current_approval_and_funding(self):
        value = self.check()
        self.assertTrue(value["tradingAccessReady"])
        self.assertFalse(value["strategyConnectionTested"])
        self.assertEqual(value["availableUsdc"], "3")

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
            self.assertFalse(self.check()["tradingAccessReady"])

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
