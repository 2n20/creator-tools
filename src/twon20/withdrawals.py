"""Public withdrawal demand for cooperative strategies, without signing access."""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .discovery import BOOK_WORDS, TRADING_KEY_VERSION, Book, ZERO, block_identity, parse_book, word256
from .errors import SetupError
from .rpc import Rpc

REPORT_TTL_SECONDS = 45


def unavailable_pending_withdrawals() -> dict:
    return dict.fromkeys(("pendingRequestCount", "escrowedShares", "owedAssets", "reservedAssets",
                          "uncoveredAssets", "totalAssets", "remainingAssets", "restricted", "operationInProgress", "operationPhase",
                          "blockNumber", "blockHash", "blockTimestamp")) | {"evidence": "unavailable"}


def pending_withdrawals_at(rpc: Rpc, vault: str, core: str, book: Book, number: int,
                           block_hash: str, timestamp: int, now: int) -> dict:
    """Read bounded aggregate demand at the caller's verified canonical block."""
    try:
        fixed = {"blockHash": block_hash, "requireCanonical": True}
        read = lambda signature, args=(): word256(rpc.words(vault, signature, list(args), fixed, 1)[0])
        count = read("pendingRequestCount()")
        shares = read("balanceOf(address)", [vault])
        reserved = read("reservedAssets()")
        supply = read("totalSupply()")
        valued = book.phase == 0 and (core == ZERO or book.priced and now - REPORT_TTL_SECONDS <= book.observed_at <= now + 5)
        total = read("totalAssets()") if valued else 0
        uncovered = read("uncoveredAssets()") if valued else 0
        if shares > supply:
            return unavailable_pending_withdrawals()
        canonical_block = rpc.call("eth_getBlockByNumber", [hex(number), False])
        completed = max(now, int(time.time()))
        canonical = block_identity(canonical_block, completed)
        if canonical != (number, block_hash, timestamp) or completed - timestamp > REPORT_TTL_SECONDS:
            return unavailable_pending_withdrawals()
        if valued and core != ZERO and completed - book.observed_at > REPORT_TTL_SECONDS:
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
                "blockTimestamp": timestamp}
    except (SetupError, KeyError, IndexError, TypeError, ValueError):
        return unavailable_pending_withdrawals()


def poll_pending_withdrawals(scope: dict, rpc: Rpc, now: int | None = None) -> dict:
    """Read public demand using an already verified vault/account scope. No key is read."""
    now = int(time.time()) if now is None else now
    try:
        number, block_hash, timestamp = block_identity(rpc.call("eth_getBlockByNumber", ["latest", False]), now)
        fixed = {"blockHash": block_hash, "requireCanonical": True}
        venue, vault, core = scope["approvalContract"], scope["vault"], scope["tradingAccount"]
        if rpc.words(venue, "tradingKeyVersion()", [], fixed, 1) != [TRADING_KEY_VERSION]:
            return unavailable_pending_withdrawals()
        book = parse_book(tuple(rpc.words(venue, "books(address)", [vault], fixed, BOOK_WORDS)), core)
        return pending_withdrawals_at(rpc, scope["vault"], scope["tradingAccount"], book,
                                      number, block_hash, timestamp, now)
    except (SetupError, KeyError, IndexError, TypeError, ValueError):
        return unavailable_pending_withdrawals()


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
        stamp = snapshot["blockTimestamp"]
        if (snapshot["evidence"] != "verified-public-reads" or type(stamp) is not int
                or not 0 <= now - stamp < min(20, max_age)):
            raise ValueError()
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
    return withdrawal_policy(poll_pending_withdrawals(scope, rpc, now), now)
