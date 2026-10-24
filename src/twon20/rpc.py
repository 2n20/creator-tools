"""Bounded, read-only JSON transport with fixed service endpoints."""

import json
import re
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

from eth_utils import keccak

from .errors import SetupError
from . import __version__

RPC_URLS = {"mainnet": "https://rpc.hyperliquid.xyz/evm"}
INFO_URLS = {"mainnet": "https://api.hyperliquid.xyz/info"}
MAX_BYTES = 1_048_576


class TransientReadError(SetupError):
    """Retry metadata only; never retain provider bodies or URLs."""

    def __init__(self, code="PUBLIC_READ_UNAVAILABLE", message="Public network evidence is temporarily unavailable. Resume the same setup.", retry_after=0):
        super().__init__(code, message)
        self.retry_after = retry_after


def retry_after_seconds(value):
    if not value:
        return 0
    try:
        return max(0, int(value) if value.isdigit() else parsedate_to_datetime(value).timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 60  # An unreadable provider backoff is not permission to retry immediately.


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SetupError("REDIRECT_REJECTED", "A public service redirected the request. No setup data was accepted.")


def request_json(url: str, payload: dict, error_messages=None, *, timeout=15):
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "User-Agent": "2n20-creator-cli/" + __version__}, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise SetupError("RESPONSE_TOO_LARGE", "The public service returned too much data. Try again later.")
            return parse_json(raw)
    except SetupError:
        raise
    except urllib.error.HTTPError as error:
        if error_messages is not None and error.code in (400, 403, 409, 410, 429, 503):
            try:
                raw = error.read(4097)
                value = parse_json(raw) if len(raw) <= 4096 else None
                if isinstance(value, dict) and set(value) in ({"code", "message"}, {"code", "message", "error"}) and isinstance(value.get("code"), str) and value["code"] in error_messages:
                    if error.code == 503 and value["code"] in ("HANDOFF_UNAVAILABLE", "EVIDENCE_UNAVAILABLE"):
                        raise TransientReadError(value["code"], error_messages[value["code"]], retry_after_seconds(error.headers.get("Retry-After"))) from None
                    raise SetupError(value["code"], error_messages[value["code"]])
            except (ValueError, OSError):
                pass
        if error.code in (408, 429, 500, 502, 503, 504):
            raise TransientReadError(retry_after=retry_after_seconds(error.headers.get("Retry-After"))) from None
        raise SetupError("PUBLIC_READ_UNAVAILABLE", "Public network evidence is unavailable. Try again later; no transaction was submitted.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TransientReadError() from None
    except ValueError:
        raise SetupError("PUBLIC_READ_UNAVAILABLE", "Public network evidence is unavailable. Try again later; no transaction was submitted.") from None


class Rpc:
    METHODS = frozenset({"eth_chainId", "eth_getBlockByNumber", "eth_getTransactionReceipt", "eth_getCode", "eth_call"})

    def __init__(self, network: str):
        self.url = RPC_URLS[network]
        self._id = 0
        self._recovery_seconds = 20.0
        self._prefetched = {}

    def call(self, method: str, params: list):
        if method not in self.METHODS:
            raise SetupError("READ_ONLY", "Only public read operations are supported.")
        key = json.dumps([method, params], sort_keys=True)
        if key in self._prefetched:
            return self._prefetched[key]
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        return self._request(payload, {self._id})[self._id]

    def prefetch(self, calls):
        """Batch only canonical fixed-block reads within this Rpc instance."""
        if not isinstance(calls, list) or not 1 <= len(calls) <= 48:
            raise SetupError("READ_BOUND", "A public read batch exceeds its bound.")
        pending = {}
        for call in calls:
            if not isinstance(call, (list, tuple)) or len(call) != 2:
                raise SetupError("READ_ONLY", "Use explicit fixed-block public read pairs.")
            method, params = call
            if (method not in {"eth_call", "eth_getCode"} or not isinstance(params, list) or len(params) != 2
                    or not isinstance(params[1], dict) or set(params[1]) != {"blockHash", "requireCanonical"}
                    or params[1]["requireCanonical"] is not True or not isinstance(params[1]["blockHash"], str)
                    or not re.fullmatch(r"0x[0-9a-fA-F]{64}", params[1]["blockHash"])):
                raise SetupError("READ_ONLY", "Only canonical fixed-block calls and code reads may be prefetched.")
            key = json.dumps([method, params], sort_keys=True)
            if key not in self._prefetched:
                pending[key] = (method, params)
        if not pending:
            return
        if len(self._prefetched) + len(pending) > 128:
            raise SetupError("READ_BOUND", "The fixed-block public read cache exceeds its bound.")
        payload, keys = [], {}
        for key, (method, params) in pending.items():
            self._id += 1
            keys[self._id] = key
            payload.append({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        results = self._request(payload, set(keys))
        self._prefetched.update({key: results[identity] for identity, key in keys.items()})

    def prefetch_words(self, reads, block):
        self.prefetch([("eth_call", [{"to": target, "data": call_data(signature, args)}, block])
                       for target, signature, args in reads])

    def _request(self, payload, identities):
        failure = "A required public contract read failed. Resume the same setup; no transaction was submitted."
        for attempt in range(3):
            started = time.monotonic()
            try:
                response = request_json(self.url, payload, timeout=15 if attempt == 0 else min(15, self._recovery_seconds))
                rows = response if isinstance(payload, list) else [response]
                if (not isinstance(rows, list) or len(rows) != len(identities)
                        or any(not isinstance(row, dict) or type(row.get("id")) is not int or row.get("jsonrpc") != "2.0" for row in rows)
                        or len({row["id"] for row in rows}) != len(rows) or {row["id"] for row in rows} != identities):
                    raise SetupError("RPC_UNAVAILABLE", failure)
                results = {}
                for row in rows:
                    results[row["id"]] = rpc_result(row, failure)
                return results
            except TransientReadError as error:
                delay = max((0.5, 1.5, 0)[attempt], error.retry_after)
                if attempt == 2 or self._recovery_seconds - (time.monotonic() - started if attempt else 0) <= delay:
                    raise
            finally:
                if attempt:
                    self._recovery_seconds = max(0, self._recovery_seconds - (time.monotonic() - started))
            self._recovery_seconds -= delay
            time.sleep(delay)

    def words(self, to: str, signature: str, arguments: list, block: dict, count: int) -> list[int]:
        data = call_data(signature, arguments)
        result = self.call("eth_call", [{"to": to, "data": data}, block])
        if not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{" + str(count * 64) + r"}", result):
            raise SetupError("CAPABILITY_UNAVAILABLE", "The deployment did not return the supported contract capabilities.")
        try:
            return [int(result[offset:offset + 64], 16) for offset in range(2, len(result), 64)]
        except ValueError:
            raise SetupError("INVALID_CONTRACT_READ", "The public contract returned invalid evidence.") from None


def rpc_result(response, failure):
    if "error" in response:
        error = response["error"]
        code = error.get("code") if isinstance(error, dict) else None
        message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
        transient = type(code) is int and (code in (-32603, -32005) or code == -32000 and any(phrase in message for phrase in ("header not found", "unknown block", "state not found", "state is not available", "missing trie node", "rate limit")))
        if transient and "result" not in response:
            raise TransientReadError("RPC_UNAVAILABLE", failure)
        raise SetupError("RPC_UNAVAILABLE", failure)
    if "result" not in response:
        raise SetupError("RPC_UNAVAILABLE", failure)
    return response["result"]


def call_data(signature, arguments):
    return "0x" + keccak(text=signature)[:4].hex() + "".join(word(argument) for argument in arguments)


def word(value) -> str:
    number = int(value, 16) if isinstance(value, str) else value
    if isinstance(number, bool) or not isinstance(number, int) or number < 0 or number >= 2 ** 256:
        raise SetupError("INVALID_INPUT", "Invalid public contract argument.")
    return f"{number:064x}"


def integer(value) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,64}", value):
        raise SetupError("INVALID_RPC_EVIDENCE", "The RPC returned invalid chain evidence.")
    try:
        result = int(value, 16)
    except ValueError:
        raise SetupError("INVALID_RPC_EVIDENCE", "The RPC returned invalid chain evidence.") from None
    if result < 0:
        raise SetupError("INVALID_RPC_EVIDENCE", "The RPC returned invalid chain evidence.")
    return result
