"""Public withdrawal demand for cooperative strategies, without signing access."""

import time

from .discovery import Book, ZERO, block_identity, read_account, word256
from .errors import SetupError
from .rpc import Rpc

REPORT_TTL_SECONDS = 45


def unavailable_pending_withdrawals() -> dict:
    return dict.fromkeys(("pendingRequestCount", "escrowedShares", "owedAssets", "reservedAssets",
                          "uncoveredAssets", "restricted", "operationInProgress", "operationPhase",
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
        valued = book.phase == 0
        total = read("totalAssets()") if valued else 0
        uncovered = read("uncoveredAssets()") if valued else 0
        if shares > supply:
            return unavailable_pending_withdrawals()
        canonical_block = rpc.call("eth_getBlockByNumber", [hex(number), False])
        completed = max(now, int(time.time()))
        canonical = block_identity(canonical_block, completed)
        if canonical != (number, block_hash, timestamp) or completed - timestamp > REPORT_TTL_SECONDS:
            return unavailable_pending_withdrawals()
        if valued and core != ZERO and (not book.priced or not completed - REPORT_TTL_SECONDS <= book.observed_at <= completed + 5):
            return unavailable_pending_withdrawals()
        # A pending operation can change valuation; preserve demand but do not price it.
        owed = (shares * total + supply - 1) // supply if shares else 0
        return {"evidence": "verified-public-reads", "pendingRequestCount": str(count),
                "escrowedShares": str(shares), "owedAssets": str(owed) if valued else None,
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
        book, *_ = read_account(rpc, scope["approvalContract"], scope["vault"], scope["tradingAccount"], fixed)
        return pending_withdrawals_at(rpc, scope["vault"], scope["tradingAccount"], book,
                                      number, block_hash, timestamp, now)
    except (SetupError, KeyError, IndexError, TypeError, ValueError):
        return unavailable_pending_withdrawals()
