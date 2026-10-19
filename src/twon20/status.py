"""Public account access checks. This does not exercise a strategy or submit orders."""

import time
from decimal import Decimal, InvalidOperation

from .discovery import RELEASE_ROUND_SECONDS, ZERO, Discovery, Key, Book, Release, Strikes, address, block_identity, read_account, verify_current, word256
from .withdrawals import pending_withdrawals_at, unavailable_pending_withdrawals
from .errors import SetupError
from .rpc import INFO_URLS, Rpc, request_json

CASH_REPORT_TTL_SECONDS = 45
KEY_EXPIRY_WARNING_SECONDS = 14 * 24 * 60 * 60
FRESH_KEY = "Approve a fresh trading key. A key that was already used cannot be approved again."
WAIT_FOR_REVOCATION = "A revocation is pending. Wait for it to finish, then approve a fresh trading key. A key that was already used cannot be approved again."
IMPORT_CONSENT = "Import public consent into Step 2 and approve it with the creator wallet."
NO_ACTION = "No action needed."
WHILE_UNPRICED = "Holders can still cancel the unsettled part of a request and claim settled withdrawals, and holders other than the owner can request withdrawals. Deposits, share transfers, settlement and the owner's withdrawal requests wait until the account is priced again. The key stays approved."
WHILE_UNPRICED_WOUND_DOWN = "Holders can still cancel the unsettled part of a request and claim settled withdrawals, and holders other than the owner can request withdrawals. Share transfers, settlement and the owner's withdrawal requests wait until the account is priced again."
STRANDED = "The trading key was removed before wind-down. Neither the vault nor the service can undo this state. While it lasts, no request settles, including requests the vault's cash covers, and wind-down cannot complete."
# Reason names and actions match the website's unpriced copy. `waits` clears when data returns.
# `repair` is undone by the venue during a release. `strategy` needs the strategy to undo it.
UNPRICED_REASONS = (
    (1, "PRICE_DATA", "waits", "Hyperliquid has no usable price for collateral the account holds."),
    (128, "READ_INCOMPLETE", "waits", "Part of the account read did not finish."),
    (2, "ABSTRACTION", "repair", "The trading account is not in standard account mode."),
    (4, "VAULT_EQUITY", "repair", "The account holds equity in a Hyperliquid vault."),
    (8, "STAKING", "strategy", "The account holds staked HYPE."),
    (16, "SUB_ACCOUNT", "strategy", "The account has sub-accounts."),
    (32, "BORROW_LEND", "strategy", "The account borrows, lends or owes spot debt."),
    (64, "BUILDER_DEX", "strategy", "The account holds a position, order or collateral on a builder-deployed market without a USDC price."),
    (256, "OTHER", "strategy", "The account value is negative or cannot be computed."),
)


def pricing_public(book: Book, wound_down: bool = False) -> dict:
    reasons = [{"name": name, "action": action, "detail": detail} for bit, name, action, detail in UNPRICED_REASONS if book.unpriced & bit]
    repairs = []
    if book.unpriced & 2:
        repairs.append("restores standard account mode")
    if book.unpriced & 4:
        repairs.append("withdraws the Hyperliquid vault equity once its lockup ends")
    if book.priced:
        summary, nxt = "priced", None
    elif not reasons:
        summary, nxt = "unreported", "The first valuation report has not landed yet."
    elif any(item["action"] == "strategy" for item in reasons):
        summary, nxt = "strategy", STRANDED if wound_down else "The strategy must undo this before the vault can price the account. The vault cannot undo it."
    elif repairs:
        summary, nxt = "repair", f"During a withdrawal release, or while no trading key is active, the vault {' and '.join(repairs)}."
    else:
        summary, nxt = "waits", "The vault prices the account again once the data returns."
    result = {"priced": book.priced, "unpriced": book.unpriced, "reasons": reasons, "action": summary}
    if nxt:
        result["next"] = nxt
    if reasons:
        result["whileUnpriced"] = WHILE_UNPRICED_WOUND_DOWN if wound_down else WHILE_UNPRICED
    return result


def lifecycle_public(book: Book, key: Key, release: Release, strikes: Strikes, cuts_allowed_at: int, uncovered: int, now: int, wound_down: bool = False) -> dict:
    expired = key.valid_until != 0 and key.valid_until <= now
    key_gone = key.confirmed == ZERO and key.requested == ZERO and key.nonce > 0 and not key.revocation_pending
    if key.revocation_pending:
        action = WAIT_FOR_REVOCATION
    elif expired or key_gone:
        action = FRESH_KEY
    elif key.requested == ZERO and key.nonce == 0:
        action = IMPORT_CONSENT
    else:
        action = NO_ACTION
    return {"release": {"active": book.restricted, "noticeAt": release.notice_at, "roundSeconds": RELEASE_ROUND_SECONDS,
                        "openedThisRound": release.opened, "misses": strikes.misses, "lastMissAt": strikes.last_miss_at,
                        "cutsAllowedAt": cuts_allowed_at},
            "pricing": pricing_public(book, wound_down),
            "restricted": book.restricted, "keyValidUntil": key.valid_until,
            "keyExpiryWarning": key.valid_until > now and key.valid_until - now <= KEY_EXPIRY_WARNING_SECONDS,
            "creatorAction": action, "uncoveredAssets": str(uncovered)}


def poll_lifecycle(scope: dict, rpc: Rpc, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    number, block_hash, timestamp = block_identity(rpc.call("eth_getBlockByNumber", ["latest", False]), now)
    block = {"blockHash": block_hash, "requireCanonical": True}
    book, key, release, strikes, cuts_allowed_at, uncovered = read_account(rpc, scope["approvalContract"], scope["vault"], scope["tradingAccount"], block)
    if block_identity(rpc.call("eth_getBlockByNumber", [hex(number), False]))[:2] != (number, block_hash):
        raise SetupError("CHAIN_CHANGED", "The chain changed during verification. Try again.")
    return lifecycle_public(book, key, release, strikes, cuts_allowed_at, uncovered, now) | {"blockTimestamp": timestamp}


def unavailable_cash_target() -> dict:
    return {"evidence": "unavailable", "bps": None, "targetAssets": None,
                   "actualUnreservedAssets": None, "allocatableAssets": None,
                   "totalAssets": None, "reservedAssets": None}


def cash_target_public(found: Discovery, rpc: Rpc, now: int) -> dict:
    unavailable = unavailable_cash_target()
    try:
        if found.timestamp > now + 5 or now - found.timestamp > 300:
            return unavailable
        if found.book.phase != 0 or found.trading_account != ZERO and (not found.book.priced or not now - CASH_REPORT_TTL_SECONDS <= found.book.observed_at <= now + 5):
            return unavailable
        read = lambda target, signature, args=(): word256(rpc.words(target, signature, list(args), found.fixed, 1)[0])
        bps = read(found.vault, "liquidityBufferBps()")
        if bps > 5000:
            return unavailable
        total = read(found.vault, "totalAssets()")
        reserved = read(found.vault, "reservedAssets()")
        cash = read(address(found.anchor["contracts"]["MockUSDC"]), "balanceOf(address)", [found.vault])
        if cash < reserved:
            return unavailable
        allocatable = read(found.vault, "allocatableAssets()")
        canonical = rpc.call("eth_getBlockByNumber", [hex(found.block_number), False])
        completed_at = max(now, int(time.time()))
        if found.trading_account != ZERO and completed_at - found.book.observed_at > CASH_REPORT_TTL_SECONDS:
            return unavailable
        if block_identity(canonical, completed_at) != (found.block_number, found.block_hash, found.timestamp):
            return unavailable
        return {"evidence": "verified-public-reads", "bps": bps,
                "targetAssets": str((total * bps + 9999) // 10000),
                "actualUnreservedAssets": str(cash - reserved),
                "allocatableAssets": str(allocatable), "totalAssets": str(total),
                "reservedAssets": str(reserved), "blockNumber": str(found.block_number),
                "blockHash": found.block_hash, "blockTimestamp": found.timestamp}
    except (SetupError, KeyError, IndexError, TypeError, ValueError):
        return unavailable


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
    view = lifecycle_public(found.book, found.key, found.release, found.strikes, found.cuts_allowed_at, found.uncovered, now, found.lifecycle != 0)
    result = found.public() | {"tradingAccessReady": False, "strategyConnectionTested": False,
                              "checkedAt": now, "evidence": "unavailable", "reasons": [],
                              "cashTarget": cash_target_public(found, rpc, now),
                              "pendingWithdrawals": pending_withdrawals_at(rpc, found.vault, found.trading_account, found.book,
                                  found.block_number, found.block_hash, found.timestamp, now)} | view
    def finish() -> dict:
        completed_at = max(now, int(time.time()))
        if completed_at - found.timestamp > 300 or (found.trading_account != ZERO and completed_at - found.book.observed_at > CASH_REPORT_TTL_SECONDS):
            result["cashTarget"] = unavailable_cash_target()
        if completed_at - found.timestamp > 45:
            result["pendingWithdrawals"] = unavailable_pending_withdrawals()
        elif found.trading_account != ZERO and completed_at - found.book.observed_at > 45:
            result["pendingWithdrawals"]["owedAssets"] = None
            result["pendingWithdrawals"]["uncoveredAssets"] = None
        return result

    reasons = result["reasons"]
    if found.trading_account == ZERO:
        reasons.append("The trading account has not been created yet. Wait for vault funding on Step 2.")
        return finish()
    if found.requested_key == ZERO:
        reasons.append(view["creatorAction"])
        return finish()
    if found.lifecycle != 0 or found.book.phase != 0 or found.revocation_pending:
        reasons.append("The vault has a pending operation, revocation or closed lifecycle.")
    if found.key.confirmed != found.requested_key or found.key.nominator != found.owner or found.key.valid_until <= now:
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
        result["pendingWithdrawals"] = unavailable_pending_withdrawals()
        reasons.append("Public Hyperliquid evidence is unavailable or inconsistent. Readiness is unverified; check again later.")
    return finish()
