"""Small explicit limits for one native-USDC perpetual entry and reduction."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
import re


class Stop(Exception):
    def __init__(self, code, action, recovery=None):
        self.code, self.action = code, action
        self.recovery = recovery
        super().__init__(code)  # Never retain arbitrary provider exception text.


def number(value, signed=False):
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool) or len(str(value)) > 100:
        raise Stop("PUBLIC_DATA_INVALID", "Retry public reads; no order is authorized by unavailable evidence.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise Stop("PUBLIC_DATA_INVALID", "Retry public reads.") from None
    if not result.is_finite() or not signed and result < 0:
        raise Stop("PUBLIC_DATA_INVALID", "Retry public reads.")
    return result


@dataclass(frozen=True)
class Bounds:
    market: str
    side: str
    max_notional: Decimal
    slippage_bps: int
    duration_seconds: int

    def __post_init__(self):
        if not isinstance(self.market, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,19}", self.market):
            raise Stop("UNSUPPORTED_MARKET", "Choose a listed native USDC perpetual; spot and HIP-3 markets are unsupported.")
        if self.side not in {"buy", "sell"} or not number(self.max_notional) or not 10 <= self.max_notional <= 100:
            raise Stop("INVALID_BUDGET", "Choose an explicit side and maximum notional between 10 and 100 USDC, plus fee reserves.")
        if type(self.slippage_bps) is not int or not 1 <= self.slippage_bps <= 50:
            raise Stop("INVALID_SLIPPAGE", "Choose explicit slippage from 1 to 50 basis points.")
        if type(self.duration_seconds) is not int or not 5 <= self.duration_seconds <= 120:
            raise Stop("INVALID_DURATION", "Choose a bounded duration from 5 to 120 seconds.")

    def public(self):
        return {"market": self.market, "side": self.side, "maxNotional": str(self.max_notional),
                "slippageBps": self.slippage_bps, "durationSeconds": self.duration_seconds, "maxOrders": 2}


def price_limit(reference, buy, bps, decimals):
    reference = number(reference)
    if reference <= 0 or type(decimals) is not int or not 0 <= decimals <= 6:
        raise Stop("PRECISION_UNAVAILABLE", "Market price or size precision is unsupported.")
    bound = reference * (1 + Decimal(bps) / 10000 if buy else 1 - Decimal(bps) / 10000)
    # Integers are always valid. Fractional prices permit five significant
    # figures and at most 6 - szDecimals decimals. Round inside the user's bound.
    places = max(0, min(6 - decimals, 4 - bound.adjusted()))
    return bound.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN if buy else ROUND_UP)


def entry_plan(bounds, snapshot):
    if snapshot["positions"] or snapshot["orders"]:
        raise Stop("ACCOUNT_CONFLICT", "Existing positions or orders need your review. This runner will not touch them.")
    mark = number(snapshot["mark"])
    decimals = snapshot["decimals"]
    price = price_limit(snapshot["ask"] if bounds.side == "buy" else snapshot["bid"], bounds.side == "buy", bounds.slippage_bps, decimals)
    size = (bounds.max_notional / max(mark, price)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
    notional = size * max(mark, price)
    if size <= 0 or size * min(mark, price) < 10 or notional > bounds.max_notional:
        raise Stop("MINIMUM_NOTIONAL", "This budget cannot form a valid 10 USDC order at market precision. Choose a different budget yourself; no automatic increase occurs.")
    # Conservative unlevered funding plus both taker fees and a fixed reserve.
    fee = number(snapshot["taker_fee"])
    if fee > Decimal("0.001"):
        raise Stop("FEE_UNVERIFIED", "Current fees exceed this example's reviewed reserve bound.")
    required = notional * (1 + 2 * fee) + Decimal("0.05")
    if number(snapshot["perp_available"]) < required:
        raise Stop("PERP_COLLATERAL_REQUIRED", "Usable perpetual collateral is insufficient. Spot USDC is separate: preview an explicit bounded collateral transfer, or keep paper mode. No deposit or leverage change will be made.")
    return {"market": bounds.market, "buy": bounds.side == "buy", "size": str(size), "price": str(price), "reduceOnly": False,
            "maxNotional": str(bounds.max_notional), "slippageBps": bounds.slippage_bps, "requiredPerpCollateral": str(required)}


def close_plan(bounds, snapshot, filled):
    expected = number(filled) * (1 if bounds.side == "buy" else -1)
    actual = snapshot["positions"].get(bounds.market, Decimal(0))
    if snapshot["orders"] or set(snapshot["positions"]) - {bounds.market} or actual != expected or expected == 0:
        raise Stop("EXPOSURE_CONFLICT", "Current exposure is not exactly the runner's confirmed fill. No unrelated position or order will be closed.")
    size = abs(expected)
    if size.quantize(Decimal(1).scaleb(-snapshot["decimals"])) != size:
        raise Stop("PRECISION_UNAVAILABLE", "Confirmed exposure cannot be represented at current market precision; review it yourself.")
    buy = expected < 0
    price = price_limit(snapshot["ask"] if buy else snapshot["bid"], buy, bounds.slippage_bps, snapshot["decimals"])
    if size * max(number(snapshot["mark"]), price) > bounds.max_notional * (1 + Decimal(bounds.slippage_bps) / 10000):
        raise Stop("EXPOSURE_BOUND_CHANGED", "The confirmed position moved beyond the closing exposure bound. Review recovery explicitly.")
    return {"market": bounds.market, "buy": buy, "size": str(size), "price": str(price), "reduceOnly": True,
            "maxNotional": str(bounds.max_notional), "slippageBps": bounds.slippage_bps}
