import io
import unittest
import urllib.error
from unittest.mock import patch

from twon20.errors import SetupError
from twon20.rpc import Rpc


class ReadRecoveryTests(unittest.TestCase):
    def test_internal_error_retries_identical_canonical_read(self):
        fixed = {"blockHash": "0x" + "a" * 64, "requireCanonical": True}
        params = [{"to": "0x" + "1" * 40, "data": "0x12345678"}, fixed]
        replies = [{"jsonrpc": "2.0", "id": 1, "error": {"code": -32603}},
                   {"jsonrpc": "2.0", "id": 1, "result": "0x1234"}]
        with patch("twon20.rpc.request_json", side_effect=replies) as send, patch("time.sleep"):
            self.assertEqual(Rpc("mainnet").call("eth_call", params), "0x1234")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args, send.call_args_list[1].args)
        self.assertEqual(send.call_args.args[1]["params"][1], fixed)

    def test_persistent_failure_is_bounded_and_never_accepts_evidence(self):
        reply = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603}}
        with patch("twon20.rpc.request_json", return_value=reply) as send, patch("time.sleep"), self.assertRaises(SetupError):
            Rpc("mainnet").call("eth_chainId", [])
        self.assertEqual(send.call_count, 3)

    def test_invalid_response_or_revert_is_not_retried(self):
        for reply in [{"jsonrpc": "2.0", "id": 2, "error": {"code": -32603}},
                      {"jsonrpc": "2.0", "id": 1, "error": {"code": 3}},
                      {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602}}]:
            with patch("twon20.rpc.request_json", return_value=reply) as send, self.assertRaises(SetupError):
                Rpc("mainnet").call("eth_chainId", [])
            self.assertEqual(send.call_count, 1)

    def test_http_failure_retries_but_long_retry_after_does_not(self):
        for headers, expected in [({}, 3), ({"Retry-After": "600"}, 1)]:
            error = urllib.error.HTTPError("https://rpc.hyperliquid.xyz/evm", 429, "rate limited", headers, io.BytesIO(b""))
            with patch("twon20.rpc.urllib.request.OpenerDirector.open", side_effect=error) as opened, patch("time.sleep"), self.assertRaises(SetupError):
                Rpc("mainnet").call("eth_chainId", [])
            self.assertEqual(opened.call_count, expected)

    def test_malformed_http_json_is_not_retried(self):
        with patch("twon20.rpc.urllib.request.OpenerDirector.open") as opened, self.assertRaises(SetupError):
            opened.return_value.__enter__.return_value.read.return_value = b'{broken'
            Rpc("mainnet").call("eth_chainId", [])
        self.assertEqual(opened.call_count, 1)

    def test_recovery_budget_is_shared_across_reads(self):
        rpc = Rpc("mainnet")
        clock = [0.0]
        calls = []
        def failing(url, payload, **kwargs):
            calls.append(kwargs["timeout"])
            if len(calls) > 1:
                clock[0] += kwargs["timeout"]
            return {"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32603}}
        with patch("twon20.rpc.request_json", side_effect=failing), patch("twon20.rpc.time.monotonic", side_effect=lambda: clock[0]), patch("time.sleep"):
            with self.assertRaises(SetupError):
                rpc.call("eth_chainId", [])
            before = len(calls)
            with self.assertRaises(SetupError):
                rpc.call("eth_chainId", [])
        self.assertEqual(len(calls) - before, 1)
        self.assertLessEqual(sum(calls[1:before]) + 2, 20)
