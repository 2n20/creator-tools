"""Public account access checks. This does not exercise a strategy or submit orders."""

import time
from decimal import Decimal, InvalidOperation

from .discovery import Discovery, ZERO, address, decoded_address, verify_current
from .errors import SetupError
from .rpc import INFO_URLS, Rpc, request_json


def decimal(value) -> Decimal:
    if not isinstance(value, str) or len(value) > 100:
        raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid returned invalid public funding evidence.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid returned invalid public funding evidence.") from None
    if not result.is_finite() or result < 0:
        raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid returned invalid public funding evidence.")
    return result


def status(found: Discovery, rpc: Rpc, now: int | None = None, info=None) -> dict:
    now = int(time.time()) if now is None else now
    info = info if info is not None else lambda payload: request_json(INFO_URLS[found.network], payload)
    result = found.public() | {"tradingAccessReady": False, "strategyConnectionTested": False,
                              "checkedAt": now, "evidence": "unavailable", "reasons": []}
    reasons = result["reasons"]
    if found.trading_account == ZERO:
        reasons.append("The trading account has not been created yet. Wait for vault funding on Step 2.")
        return result
    if found.requested_key == ZERO:
        reasons.append("Import public consent into Step 2 and approve it with the creator wallet.")
        return result
    if found.lifecycle != 0 or found.book[1] != 0 or found.pending_requests or found.revocation_pending:
        reasons.append("The vault has a pending operation, withdrawal, revocation or closed lifecycle.")
    if decoded_address(found.key_status[0], True) != found.requested_key or decoded_address(found.key_status[2], True) != found.owner or found.key_status[1] <= now:
        reasons.append("The approval contract has not confirmed current trading access.")
    try:
        role = info({"type": "userRole", "user": found.requested_key})
        agents = info({"type": "extraAgents", "user": found.trading_account})
        mode = info({"type": "userAbstraction", "user": found.trading_account})
        spot = info({"type": "spotClearinghouseState", "user": found.trading_account})
        perp = info({"type": "clearinghouseState", "user": found.trading_account, "dex": ""})
        if not isinstance(role, dict) or role.get("role") != "agent" or not isinstance(role.get("data"), dict) or address(role["data"].get("user")) != found.trading_account:
            reasons.append("Hyperliquid has not confirmed this key's trading-account binding.")
        if not isinstance(agents, list) or len(agents) > 100:
            raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid agent evidence is unavailable.")
        seen = set()
        matched = False
        for agent in agents:
            if not isinstance(agent, dict):
                raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid agent evidence is unavailable.")
            account = address(agent.get("address"))
            expiry = agent.get("validUntil")
            if account in seen or type(expiry) is not int or not isinstance(agent.get("name"), str):
                raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid agent evidence is inconsistent.")
            seen.add(account)
            if account == found.requested_key and expiry > now * 1000:
                matched = True
        if not matched:
            reasons.append("Hyperliquid has not confirmed an unexpired approval for this trading key.")
        if mode != "disabled":
            reasons.append("The trading account is not in the supported standard account mode.")
        if not isinstance(spot, dict) or not isinstance(spot.get("balances"), list) or not isinstance(perp, dict) or not isinstance(perp.get("marginSummary"), dict) or type(perp.get("time")) is not int or not now * 1000 - 15_000 <= perp["time"] <= now * 1000 + 5000:
            raise SetupError("INVALID_CORE_EVIDENCE", "Fresh Hyperliquid balance evidence is unavailable.")
        usdc = Decimal(0)
        seen_tokens = set()
        for balance in spot["balances"]:
            if not isinstance(balance, dict) or type(balance.get("token")) is not int or balance["token"] in seen_tokens:
                raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid spot evidence is inconsistent.")
            seen_tokens.add(balance["token"])
            total, hold = decimal(balance.get("total")), decimal(balance.get("hold"))
            if hold > total:
                raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid spot evidence is inconsistent.")
            if balance["token"] == 0:
                if balance.get("coin") != "USDC":
                    raise SetupError("INVALID_CORE_EVIDENCE", "Hyperliquid USDC evidence is inconsistent.")
                usdc += total - hold
        usdc += decimal(perp["marginSummary"].get("accountValue"))
        if usdc <= 0:
            reasons.append("No available USDC backing was verified in the supported trading account.")
        if role != info({"type": "userRole", "user": found.requested_key}) or agents != info({"type": "extraAgents", "user": found.trading_account}) or mode != info({"type": "userAbstraction", "user": found.trading_account}):
            raise SetupError("CORE_STATE_CHANGED", "Hyperliquid key evidence changed during verification. Check again.")
        verify_current(found, rpc)
        if int(time.time()) - now > 45:
            raise SetupError("STALE_CORE_EVIDENCE", "Connection evidence expired during verification. Check again.")
        result["evidence"] = "verified-public-reads"
        result["availableUsdc"] = str(usdc)
        result["tradingAccessReady"] = not reasons
    except (SetupError, KeyError, TypeError, ValueError):
        reasons.append("Public Hyperliquid evidence is unavailable or inconsistent. Readiness is unverified; check again later.")
    return result
