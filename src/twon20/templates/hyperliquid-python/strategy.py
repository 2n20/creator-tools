"""Educational moving-average crossover. No wallets, APIs or order submission."""
from decimal import Decimal


def crossover(closes, fast=3, slow=5):
    if not 1 < fast < slow <= 100 or len(closes) < slow + 1:
        return "hold"
    values = [Decimal(str(value)) for value in closes]
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("INVALID_CANDLES")
    before_fast = sum(values[-fast-1:-1]) / fast
    before_slow = sum(values[-slow-1:-1]) / slow
    current_fast = sum(values[-fast:]) / fast
    current_slow = sum(values[-slow:]) / slow
    if before_fast <= before_slow and current_fast > current_slow:
        return "buy"
    if before_fast >= before_slow and current_fast < current_slow:
        return "sell"
    return "hold"


def paper(closes, budget="12", fast=3, slow=5):
    """Finite simulation; fills are fictional and omit fees/funding/latency."""
    if not isinstance(closes, list) or not 1 <= len(closes) <= 10000:
        raise ValueError("INVALID_CANDLES")
    budget = Decimal(str(budget))
    if not budget.is_finite() or not 0 < budget <= 100:
        raise ValueError("INVALID_PAPER_BUDGET")
    quantity, cash, decisions = Decimal(0), budget, []
    for index, raw in enumerate(closes):
        price = Decimal(str(raw))
        if not price.is_finite() or price <= 0:
            raise ValueError("INVALID_CANDLES")
        signal = crossover(closes[:index+1], fast, slow)
        if signal == "buy" and quantity == 0:
            quantity, cash = cash / price, Decimal(0)
            decisions.append({"index": index, "signal": signal, "price": str(price)})
        elif signal == "sell" and quantity > 0:
            cash, quantity = quantity * price, Decimal(0)
            decisions.append({"index": index, "signal": signal, "price": str(price)})
    return {"mode": "paper", "simulated": True, "ordersSubmitted": 0,
            "signerLoaded": False, "decisions": decisions,
            "simulatedEquity": str(cash + quantity * Decimal(str(closes[-1]))),
            "limitations": "Fictional fills; no fees, funding, latency or profit forecast."}
