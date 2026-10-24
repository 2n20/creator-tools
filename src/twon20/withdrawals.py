"""Public withdrawal demand for cooperative strategies, without signing access."""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .discovery import BOOK_WORDS, TRADING_KEY_VERSION, Book, ZERO, block_identity, parse_book, word256
from .errors import SetupError
from .rpc import Rpc
from .valuation import ValuationReport, fetch_valuation, valuation_word

REPORT_TTL_SECONDS = 45


def unavailable_pending_withdrawals() -> dict:
    return dict.fromkeys(("pendingRequestCount", "escrowedShares", "owedAssets", "reservedAssets",
                          "uncoveredAssets", "totalAssets", "remainingAssets", "restricted", "operationInProgress", "operationPhase",
                          "blockNumber", "blockHash", "blockTimestamp")) | {"evidence": "unavailable"}


def pending_withdrawals_at(rpc: Rpc, vault: str, core: str, book: Book, number: int,
                           block_hash: str, timestamp: int, now: int,
                           report: ValuationReport | None = None) -> dict:
    """Read bounded aggregate demand at the caller's verified canonical block."""
    try:
        fixed = {"blockHash": block_hash, "requireCanonical": True}
        read = lambda signature, args=(): word256(rpc.words(vault, signature, list(args), fixed, 1)[0])
        count = read("pendingRequestCount()")
        shares = read("balanceOf(address)", [vault])
        reserved = read("reservedAssets()")
        supply = read("totalSupply()")
        observed = report.observed_at if report else book.observed_at
        valued = book.phase == 0 and (core == ZERO or (report is not None or book.priced)
                  and now - REPORT_TTL_SECONDS <= observed <= min(timestamp, now + 5))
        total = uncovered = 0
        if valued:
            try:
                financial = (lambda name: valuation_word(rpc, vault, report, name, [], fixed)) if report else read
                total, uncovered = financial("totalAssets()"), financial("uncoveredAssets()")
            except (SetupError, ValueError, TypeError):
                valued = False  # Preserve observed demand when fresh pricing cannot finish.
        if shares > supply:
            return unavailable_pending_withdrawals()
        canonical_block = rpc.call("eth_getBlockByNumber", [hex(number), False])
        completed = max(now, int(time.time()))
        canonical = block_identity(canonical_block, completed)
        if canonical != (number, block_hash, timestamp) or completed - timestamp > REPORT_TTL_SECONDS:
            return unavailable_pending_withdrawals()
        if valued and core != ZERO and completed - observed > REPORT_TTL_SECONDS:
            valued = False
        # A pending operation can change valuation; preserve demand but do not price it.
        owed = (shares * total + supply - 1) // supply if shares else 0
        return {"evidence": "verified-public-reads", "pendingRequestCount": str(count),
                "escrowedShares": str(shares), "owedAssets": str(owed) if valued else None,
                "totalAssets": str(total) if valued else None,
                "remainingAssets": str(max(0, total - owed)) if valued else None,
                "reservedAssets": str(reserved), "uncoveredAssets": str(uncovered) if valued else None,
                "restricted": book.restricted, "operationInProgress": book.phase != 0,
                "operationPhase": book.phase, "blockNumber": str(number), "blockHash": block_hash,
                "blockTimestamp": timestamp, "valuationObservedAt": observed if core != ZERO else timestamp}
    except (SetupError, KeyError, IndexError, TypeError, ValueError):
        return unavailable_pending_withdrawals()


def poll_pending_withdrawals(scope: dict, rpc: Rpc, now: int | None = None) -> dict:
    """Read public demand using an already verified vault/account scope. No key is read."""
    now = int(time.time()) if now is None else now
    try:
        report = None
        if scope["tradingAccount"] != ZERO:
            try:
                report = fetch_valuation(scope)
            except (SetupError, KeyError, IndexError, TypeError, ValueError):
                pass
        # Choose the block after obtaining the report, so it cannot predate it.
        now = max(now, int(time.time()))
        number, block_hash, timestamp = block_identity(rpc.call("eth_getBlockByNumber", ["latest", False]), now)
        fixed = {"blockHash": block_hash, "requireCanonical": True}
        venue, vault, core = scope["approvalContract"], scope["vault"], scope["tradingAccount"]
        if rpc.words(venue, "tradingKeyVersion()", [], fixed, 1) != [TRADING_KEY_VERSION]:
            return unavailable_pending_withdrawals()
        book = parse_book(tuple(rpc.words(venue, "books(address)", [vault], fixed, BOOK_WORDS)), core)
        return pending_withdrawals_at(rpc, scope["vault"], scope["tradingAccount"], book,
                                      number, block_hash, timestamp, now, report)
    except (SetupError, KeyError, IndexError, TypeError, ValueError):
        return unavailable_pending_withdrawals()


def _fresh_snapshot(snapshot: dict, now: int, max_age: int) -> bool:
    stamp = snapshot["blockTimestamp"]
    return (snapshot["evidence"] == "verified-public-reads" and type(stamp) is int
            and 0 <= now - stamp < min(20, max_age)
            and 0 <= now - snapshot.get("valuationObservedAt", stamp) < REPORT_TTL_SECONDS)


def pending_withdrawal_usdc(snapshot: dict, now: int | None = None,
                            max_age: int = 20) -> Decimal | None:
    """Estimated unsettled withdrawals across the vault, excluding settled reserves.

    Return None for stale, unpriced, in-flight or invalid evidence. This amount
    is not an account-specific margin deduction or permission to submit orders.
    """
    now = int(time.time()) if now is None else now
    try:
        if (not _fresh_snapshot(snapshot, now, max_age)
                or snapshot["operationInProgress"] is not False
                or type(snapshot["operationPhase"]) is not int or snapshot["operationPhase"] != 0):
            return None
        amounts = [snapshot[name] for name in ("owedAssets", "totalAssets", "remainingAssets",
                                              "pendingRequestCount", "escrowedShares",
                                              "reservedAssets", "uncoveredAssets")]
        if any(not isinstance(value, str) or not value.isascii() or not value.isdecimal() for value in amounts):
            return None
        owed, total, remaining, count, shares, _reserved, _uncovered = map(int, amounts)
        if (not 0 <= owed <= total or remaining != total - owed
                or (count == 0) != (shares == 0) or shares == 0 and owed != 0
                or shares > 0 and total > 0 and owed == 0
                or type(snapshot["restricted"]) is not bool):
            return None
        return Decimal(f"{owed // 1_000_000}.{owed % 1_000_000:06d}")
    except (KeyError, TypeError, ValueError):
        return None


def poll_pending_withdrawal_usdc(scope: dict, rpc: Rpc, now: int | None = None) -> Decimal | None:
    """Read the withdrawal estimate in USDC using a previously verified scope."""
    snapshot = poll_pending_withdrawals(scope, rpc, now)
    completed = int(time.time())
    return pending_withdrawal_usdc(snapshot, completed if now is None else max(now, completed))


@dataclass(frozen=True)
class TradingCapital:
    total_usdc: Decimal | None
    remaining_usdc: Decimal | None


@dataclass(frozen=True)
class WithdrawalPolicy:
    state: Literal["ready", "yielding", "unavailable"]
    capital: TradingCapital
    ready_to_increase: bool
    reason: str
    observed_at: int | None


def withdrawal_policy(snapshot: dict, now: int | None = None, max_age: int = 20) -> WithdrawalPolicy:
    """Cooperative policy. totalAssets already excludes settled reserved claims."""
    now = int(time.time()) if now is None else now
    empty = TradingCapital(None, None)
    try:
        if not _fresh_snapshot(snapshot, now, max_age):
            raise ValueError()
        stamp = snapshot["blockTimestamp"]
        demand = int(snapshot["pendingRequestCount"]) > 0 or int(snapshot["escrowedShares"]) > 0
        if type(snapshot["restricted"]) is not bool or type(snapshot["operationInProgress"]) is not bool:
            raise ValueError()
        capital = empty
        if snapshot["totalAssets"] is not None and snapshot["remainingAssets"] is not None:
            total, remaining = int(snapshot["totalAssets"]), int(snapshot["remainingAssets"])
            if not 0 <= remaining <= total:
                raise ValueError()
            capital = TradingCapital(Decimal(total) / 1_000_000, Decimal(remaining) / 1_000_000)
        if demand or snapshot["restricted"] or snapshot["operationInProgress"]:
            return WithdrawalPolicy("yielding", capital, False, "withdrawal_pending", stamp)
        if capital.remaining_usdc is None:
            return WithdrawalPolicy("unavailable", capital, False, "capital_unpriced", stamp)
        return WithdrawalPolicy("ready", capital, True, "ready", stamp)
    except (KeyError, TypeError, ValueError):
        return WithdrawalPolicy("unavailable", empty, False, "withdrawal_evidence_unavailable", None)


def poll_withdrawal_policy(scope: dict, rpc: Rpc, now: int | None = None) -> WithdrawalPolicy:
    snapshot = poll_pending_withdrawals(scope, rpc, now)
    return withdrawal_policy(snapshot, max(now or 0, int(time.time())))
