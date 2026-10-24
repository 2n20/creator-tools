"""On-demand signed valuation, applied only inside read-only EVM calls."""

import re
import time
from dataclasses import dataclass
from threading import Lock, Thread

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import keccak

from .discovery import address, trust_anchor
from .errors import SetupError
from .rpc import request_json

REPORT_TYPES = ["address", "uint64", "uint64", "uint64", "bool", "uint16", "uint256",
                "int256", "uint32", "uint64", "uint64", "address", "uint64", "uint8",
                "uint16", "uint64", "bool", "bytes32"]
ENDPOINT = "https://2n20.org/api/sdk/valuation"
_reports = {}
_refreshing = set()
_retry_at = {}
_report_lock = Lock()


@dataclass(frozen=True)
class ValuationReport:
    encoded: bytes
    signature: bytes
    observed_at: int


def fetch_valuation(scope: dict) -> ValuationReport:
    """No authentication credential or strategy signer is needed for public data.

    The endpoint supplies evidence, not trusted balances. withReport verifies
    the operator signature, vault, key/state nonces and expiry on the EVM.
    """
    anchor = trust_anchor(scope.get("network", "mainnet"))
    if address(scope["approvalContract"]) != address(anchor["contracts"]["LocalVenue"]):
        raise ValueError("Unverified valuation scope")
    vault = address(scope["vault"])
    generation = anchor["generation"]
    key = (generation, vault)
    now = int(time.time())
    with _report_lock:
        saved = _reports.get(key)
        usable = saved is not None and 0 <= now - saved.observed_at < 40
        refresh = (key not in _refreshing and len(_refreshing) < 4
                   and now >= _retry_at.get(key, 0))
        if usable and now - saved.observed_at < 5:
            refresh = False
        if not usable and not refresh:
            raise SetupError("VALUATION_UNAVAILABLE", "Fresh capital valuation is being checked. New risk must wait.")
        if refresh:
            _refreshing.add(key)
    if usable:
        if refresh:
            Thread(target=_background_refresh, args=(key,), name="2n20-valuation", daemon=True).start()
        return saved  # Verify the signature and current state in every EVM read.
    return _refresh(key)


def _background_refresh(key):
    try:
        _refresh(key)
    except Exception:
        pass  # Retain usable evidence until expiry; never expose a transport body.


def _refresh(key):
    try:
        report = _request_valuation(*key)
        with _report_lock:
            if len(_reports) >= 128 and key not in _reports:
                _reports.pop(next(iter(_reports)))
            _reports[key] = report
            _retry_at.pop(key, None)
        return report
    except Exception:
        with _report_lock:
            if len(_retry_at) >= 128 and key not in _retry_at:
                _retry_at.pop(next(iter(_retry_at)))
            _retry_at[key] = int(time.time()) + 5
        raise
    finally:
        with _report_lock:
            _refreshing.discard(key)


def _request_valuation(generation, vault):
    result = request_json(ENDPOINT, {"generation": generation, "vault": vault}, timeout=65)
    if (not isinstance(result, dict) or result.get("status") != "ready"
            or result.get("generation") != generation or address(result.get("vault")) != vault
            or not isinstance(result.get("report"), str)
            or not re.fullmatch(r"0x[0-9a-fA-F]{1152}", result["report"])
            or not isinstance(result.get("signature"), str)
            or not re.fullmatch(r"0x[0-9a-fA-F]{130}", result["signature"])):
        raise ValueError("Valuation unavailable")
    raw = bytes.fromhex(result["report"][2:])
    try:
        values = decode(REPORT_TYPES, raw)
    except DecodingError:
        raise ValueError("Invalid valuation encoding") from None
    now = int(time.time())
    if (encode(REPORT_TYPES, values) != raw or address(values[0]) != vault
            or values[4] is not True or values[5] != 0 or values[13] != 0
            or not any(values[17]) or type(result.get("observedAt")) is not int
            or result["observedAt"] != values[3] or not now - 45 < values[3] <= now):
        raise ValueError("Valuation is not fresh and priced")
    return ValuationReport(raw, bytes.fromhex(result["signature"][2:]), values[3])


def valuation_word(rpc, vault, report, signature, arguments, fixed):
    action = keccak(text=signature)[:4] + encode(["address"] * len(arguments), arguments)
    body = encode(["bytes", "bytes", "bytes", "uint256", "uint256"],
                  [report.encoded, report.signature, action, 0, 2 ** 256 - 1])
    data = "0x" + (keccak(text="withReport(bytes,bytes,bytes,uint256,uint256)")[:4] + body).hex()
    result = rpc.call("eth_call", [{"to": vault, "data": data}, fixed])
    if not isinstance(result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{192}", result):
        raise SetupError("VALUATION_UNAVAILABLE", "Fresh capital valuation is unavailable. No transaction was submitted.")
    raw = bytes.fromhex(result[2:])
    try:
        value, = decode(["bytes"], raw)
    except DecodingError:
        raise ValueError("Invalid valuation encoding") from None
    if len(value) != 32 or encode(["bytes"], [value]) != raw:
        raise ValueError("Invalid valuation result")
    return int.from_bytes(value, "big")
