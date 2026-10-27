"""Put signal logic and its parameter validation here. Return targets, not orders."""
from decimal import Decimal


def build_strategy(parameters):
    """Select your strategy here; check and run use this same factory.

    The returned object implements models.Strategy. Construction and evaluate
    must avoid network calls and signing. Keep state in the public checkpoint.
    Replace this factory and the strategy below to use your own parameters.
    """
    from risk import Stop
    if not isinstance(parameters, dict) or set(parameters) != {"window", "multiplier"}:
        raise Stop("STRATEGY_CONFIG_INVALID", "The example strategy requires window and multiplier.")
    return BandBreakout(**parameters)


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


class BandBreakout:
    """Educational rolling-band component, with a durable current direction.

    Above the upper sample-standard-deviation band: long. Below the lower
    band: short. A crossing of the rolling mean: flat. This is a small
    replaceable component, not the original multi-signal strategy or an alpha
    claim. It uses no research, optimization or backtesting dependencies.
    """
    def __init__(self, window=20, multiplier="2"):
        from risk import Stop, number
        self.window, self.multiplier = window, number(multiplier)
        if type(window) is not int or not 3 <= window <= 128 or not 0 < self.multiplier <= 10:
            raise Stop("STRATEGY_CONFIG_INVALID", "Choose a band window from 3 to 128 and a positive multiplier up to 10.")
        self.warmup_bars = window

    def evaluate(self, bars, checkpoint):
        from models import Target
        from risk import number
        previous = number(checkpoint.get("fraction", "0"), signed=True)
        if len(bars) < self.window:
            return None, {"fraction": str(previous), "distance": None}
        values = [bar.close for bar in bars[-self.window:]]
        mean = sum(values) / self.window
        deviation = (sum((value - mean) ** 2 for value in values) / (self.window - 1)).sqrt()
        distance = values[-1] - mean
        fraction, reason = previous, "band_hold"
        if distance > self.multiplier * deviation:
            fraction, reason = Decimal(1), "upper_breakout"
        elif distance < -self.multiplier * deviation:
            fraction, reason = Decimal(-1), "lower_breakout"
        old_distance = checkpoint.get("distance")
        if old_distance is not None and number(old_distance, signed=True) * distance < 0:
            fraction, reason = Decimal(0), "mean_crossing"
        return Target(fraction, reason), {"fraction": str(fraction), "distance": str(distance)}
