"""Pure sizing example for a custom executor. No signing or network access.

The caller must establish that this equity pool funds the full pending amount.
A target is not an order or admission decision. See WITHDRAWALS.md.
"""

import json
import sys
from decimal import Decimal, InvalidOperation

from twon20.withdrawals import pending_withdrawal_usdc


def desired_position(snapshot: dict, equity: Decimal, price: Decimal,
                     multiplier: Decimal, now: int | None = None) -> Decimal | None:
    """Desired total long quantity, not additional quantity to buy.

    Keep your own signals and executor. Unknown evidence leaves sizing unknown;
    it must not become a zero withdrawal deduction or a flattening instruction.
    """
    if any(not isinstance(value, Decimal) or not value.is_finite()
           for value in (equity, price, multiplier)) or price <= 0 or multiplier < 0:
        raise ValueError("Use finite Decimal equity, positive price and nonnegative multiplier.")
    pending = pending_withdrawal_usdc(snapshot, now)
    if pending is None:
        return None
    sizing_equity = max(Decimal(0), equity - pending)
    return sizing_equity * multiplier / price


def main() -> int:
    """Read externally supplied public snapshot and sizing inputs from stdin."""
    try:
        data = json.load(sys.stdin)
        target = desired_position(data["snapshot"], Decimal(data["equity"]),
                                  Decimal(data["price"]), Decimal(data["multiplier"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        print(json.dumps({"error": "Invalid public sizing input."}))
        return 1
    print(json.dumps({"desiredTotalQuantity": str(target) if target is not None else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
