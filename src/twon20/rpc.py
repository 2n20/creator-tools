"""Bounded, read-only JSON transport with fixed service endpoints."""

import json
import re
import urllib.error
import urllib.request

from eth_utils import keccak

from .errors import SetupError
from . import __version__

RPC_URLS = {"mainnet": "https://rpc.hyperliquid.xyz/evm"}
INFO_URLS = {"mainnet": "https://api.hyperliquid.xyz/info"}
MAX_BYTES = 1_048_576


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
                    raise SetupError(value["code"], error_messages[value["code"]])
            except (ValueError, OSError):
                pass
        raise SetupError("PUBLIC_READ_UNAVAILABLE", "Public network evidence is unavailable. Try again later; no transaction was submitted.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SetupError("PUBLIC_READ_UNAVAILABLE", "Public network evidence is unavailable. Try again later; no transaction was submitted.") from None


class Rpc:
    METHODS = frozenset({"eth_chainId", "eth_getBlockByNumber", "eth_getTransactionReceipt", "eth_getCode", "eth_call"})

    def __init__(self, network: str):
        self.url = RPC_URLS[network]
        self._id = 0

    def call(self, method: str, params: list):
        if method not in self.METHODS:
            raise SetupError("READ_ONLY", "Only public read operations are supported.")
        self._id += 1
        response = request_json(self.url, {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if not isinstance(response, dict) or type(response.get("id")) is not int or response.get("id") != self._id or response.get("jsonrpc") != "2.0" or "error" in response or "result" not in response:
            raise SetupError("RPC_UNAVAILABLE", "A required public contract read failed. Try again later; no transaction was submitted.")
        return response["result"]

    def words(self, to: str, signature: str, arguments: list, block: dict, count: int) -> list[int]:
        data = "0x" + keccak(text=signature)[:4].hex() + "".join(word(argument) for argument in arguments)
        result = self.call("eth_call", [{"to": to, "data": data}, block])
        if not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{" + str(count * 64) + r"}", result):
            raise SetupError("CAPABILITY_UNAVAILABLE", "The deployment did not return the supported contract capabilities.")
        try:
            return [int(result[offset:offset + 64], 16) for offset in range(2, len(result), 64)]
        except ValueError:
            raise SetupError("INVALID_CONTRACT_READ", "The public contract returned invalid evidence.") from None


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
