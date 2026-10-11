"""Small replaceable interfaces. Strategies return exposure, never venue orders."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence
import re

from risk import Stop, number

INTERVALS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
             "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}


@dataclass(frozen=True)
class Bar:
    market: str
    interval: str
    open_ms: int
    close_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def public(self):
        return {"s": self.market, "i": self.interval, "t": self.open_ms, "T": self.close_ms,
                "o": str(self.open), "h": str(self.high), "l": str(self.low), "c": str(self.close), "v": str(self.volume)}

    @classmethod
    def parse(cls, value, market, interval):
        try:
            duration = INTERVALS[interval]
            if not isinstance(value, dict) or value.get("s") != market or value.get("i") != interval:
                raise ValueError()
            start, end = value["t"], value["T"]
            if type(start) is not int or type(end) is not int or start < 0 or start % duration or end != start + duration - 1:
                raise ValueError()
            o, h, low, c, v = [number(value[key]) for key in ("o", "h", "l", "c", "v")]
            if not 0 < low <= min(o, c) <= max(o, c) <= h:
                raise ValueError()
            return cls(market, interval, start, end, o, h, low, c, v)
        except (KeyError, TypeError, ValueError):
            raise Stop("CANDLE_INVALID", "Candle identity, timestamps or OHLCV are inconsistent.") from None


@dataclass(frozen=True)
class Target:
    fraction: Decimal
    reason: str

    def __post_init__(self):
        if not -1 <= number(self.fraction, signed=True) <= 1 or not re.fullmatch(r"[a-z0-9_]{1,64}", self.reason):
            raise Stop("TARGET_INVALID", "Strategy targets need a normalized fraction and a short public reason code.")


@dataclass(frozen=True)
class BarBatch:
    bars: tuple[Bar, ...]
    trade_last: bool = False


class Feed(Protocol):
    healthy: bool
    done: bool
    def poll(self, now_ms: int) -> BarBatch | None: ...
    def close(self) -> None: ...


class Strategy(Protocol):
    warmup_bars: int
    def evaluate(self, bars: Sequence[Bar], checkpoint: dict) -> tuple[Target | None, dict]: ...


class Executor(Protocol):
    def recover(self) -> None: ...
    def set_target(self, intent: dict) -> None: ...
    def step(self, now_ms: int) -> None: ...
    def shutdown(self) -> None: ...


class Reporter(Protocol):
    def emit(self, report: dict) -> None: ...
