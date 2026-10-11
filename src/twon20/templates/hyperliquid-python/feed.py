"""Official SDK candle subscriptions with REST-confirmed elapsed bars and repair."""
from collections import OrderedDict
from decimal import Decimal
from threading import Event, Lock
import time

from models import Bar, BarBatch, INTERVALS
from risk import Stop


class FixtureFeed:
    """Finite deterministic educational observations, explicitly fictional."""
    def __init__(self, market="BTC", interval="1m", closes=None, cursor=None):
        self.market, self.interval = market, interval
        self.closes = closes or ([100] * 22 + [104, 108, 111, 105, 99, 93, 89, 95, 101, 106])
        self.index = 0
        self.cursor = cursor
        self.healthy, self.done = True, False

    def poll(self, now_ms):
        duration = INTERVALS[self.interval]
        if self.index >= len(self.closes):
            self.done = True
            return None
        start = (1_700_000_000_000 // duration + self.index) * duration
        price = Decimal(str(self.closes[self.index]))
        self.index += 1
        if self.cursor is not None and start <= self.cursor:
            return None
        return BarBatch((Bar(self.market, self.interval, start, start + duration - 1,
                             price, price, price, price, Decimal(1)),), True)

    def close(self):
        self.healthy = False


def sdk_stream(market, callback, interval):
    from adapter import API, sdk_types
    Info, _, _ = sdk_types()
    # Fetch metadata before starting the socket so a failed constructor does
    # not leak a half-created SDK websocket manager.
    rest = Info(API, skip_ws=True, timeout=5, meta={"universe": []}, spot_meta={"universe": [], "tokens": []})
    meta = rest.meta()
    if not isinstance(meta, dict) or not isinstance(meta.get("universe"), list) or len(meta["universe"]) > 2000:
        raise Stop("MARKET_METADATA_INVALID", "Public native-perp metadata is unavailable.")
    matches = [asset for asset in meta["universe"] if asset.get("name") == market and not asset.get("isDelisted")]
    if len(matches) != 1:
        raise Stop("UNSUPPORTED_MARKET", "Choose a listed native perpetual market.")
    info = Info(API, skip_ws=False, timeout=5, meta=meta, spot_meta={"universe": [], "tokens": []})
    try:
        info.subscribe({"type": "candle", "coin": market, "interval": interval}, callback)
        return info
    except BaseException:
        info.disconnect_websocket()
        raise


class HyperliquidCandleFeed:
    """poll never performs network I/O; one shared worker owns all SDK reads.

    SDK updates are triggers, not a close flag. After close+grace, REST must
    confirm a contiguous bar. Replay/warmup never dispatches historical trades.
    Overflow is a sticky out-of-band latch. Only a successful repair clears it.
    """
    CAPACITY = 128
    MAX_BACKFILL = 512
    GRACE_MS = 1500

    def __init__(self, worker, market="BTC", interval="1m", history=(), factory=sdk_stream, clock=None):
        self.worker, self.market, self.interval, self.factory = worker, market, interval, factory
        self.duration = INTERVALS[interval]
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.committed = OrderedDict((bar.open_ms, bar) for bar in history)
        self.cursor = next(reversed(self.committed), None)
        self.buffer = OrderedDict()
        self.lock, self.repair, self.closed = Lock(), Event(), Event()
        self.repair.set()
        self.healthy, self.done, self.error = False, False, None
        self.info, self.job = None, None
        self.resource = None
        self.next_poll, self.last_message, self.failures = 0, self.clock(), 0
        self.repair_generation = 0
        self.was_ready = False
        self.socket_generation = 0
        self.messages_received = 0

    def admission_ready(self):
        now = self.clock()
        silent = now - self.last_message > max(30_000, self.duration * 2)
        stale = self.cursor is not None and now > self.cursor + 2 * self.duration + self.GRACE_MS + 5000
        dead = self.info is not None and hasattr(self.info, "ws_manager") and not self.info.ws_manager.is_alive()
        if silent or stale or dead:
            self._damage()
        return self.healthy and not self.repair.is_set() and not self.closed.is_set()

    def _damage(self):
        with self.lock:
            self.repair_generation += 1
            self.repair.set()
        self.healthy = False

    def callback(self, message):
        if self.closed.is_set():
            return
        try:
            if not isinstance(message, dict) or message.get("channel") != "candle":
                raise ValueError()
            bar = Bar.parse(message.get("data"), self.market, self.interval)
            now = self.clock()
            if bar.open_ms > now + 5000:
                raise ValueError()
            with self.lock:
                if bar.open_ms not in self.buffer and len(self.buffer) >= self.CAPACITY:
                    self.repair_generation += 1
                    self.repair.set()
                    return
                self.buffer[bar.open_ms] = bar
                self.last_message = now
                self.messages_received += 1
        except Exception:
            self._damage()

    def _connect(self):
        old = self.info
        if old is not None:
            old.disconnect_websocket()
        info = self.factory(self.market, self.callback, self.interval)
        self.resource = info
        return info

    def _merge(self, values, now_ms, recovering, generation):
        if not isinstance(values, list) or len(values) > self.MAX_BACKFILL + 2:
            raise Stop("CANDLE_HISTORY_BOUND", "REST history exceeds the bounded recovery window.")
        candidates = {}
        for value in values:
            bar = Bar.parse(value, self.market, self.interval)
            if bar.close_ms + self.GRACE_MS >= now_ms:
                continue
            if bar.open_ms in candidates and candidates[bar.open_ms] != bar:
                raise Stop("CANDLE_CONFLICT", "Conflicting REST revisions require review.")
            candidates[bar.open_ms] = bar
            if bar.open_ms in self.committed and self.committed[bar.open_ms] != bar:
                raise Stop("COMMITTED_CANDLE_CHANGED", "A committed candle changed. Preserve the strategy checkpoint for explicit review.")
        bars = [bar for start, bar in sorted(candidates.items()) if self.cursor is None or start > self.cursor]
        expected = self.cursor + self.duration if self.cursor is not None else (bars[0].open_ms if bars else None)
        for bar in bars:
            if bar.open_ms != expected:
                raise Stop("CANDLE_GAP", "Contiguous REST recovery is incomplete; new orders remain disabled.")
            expected += self.duration
        latest = (now_ms - self.GRACE_MS) // self.duration * self.duration - self.duration
        new_cursor = bars[-1].open_ms if bars else self.cursor
        if new_cursor is None or new_cursor < latest:
            raise Stop("CANDLE_STALE", "The latest completed candle is unavailable; new orders remain disabled.")
        for bar in bars:
            self.committed[bar.open_ms] = bar
        while len(self.committed) > self.CAPACITY:
            self.committed.popitem(last=False)
        self.cursor = new_cursor
        with self.lock:
            if generation == self.repair_generation:
                self.repair.clear()
            self.buffer.clear()
        self.healthy = not self.repair.is_set()
        self.failures = 0
        return BarBatch(tuple(bars), bool(bars) and not recovering and self.healthy and len(bars) == 1)

    def poll(self, now_ms):
        if self.closed.is_set():
            return None
        self.admission_ready()
        if self.error:
            raise Stop(self.error, "The feed cannot safely reconstruct its cursor. Preserve the checkpoint and review recovery.")
        if self.job:
            response = self.worker.take(self.job)
            if response is None:
                if now_ms - self.last_message > max(30_000, self.duration * 2):
                    self._damage()
                return None
            kind = self.job
            self.job = None
            ok, value = response
            if not ok:
                self._damage()
                self.failures += 1
                self.next_poll = now_ms + min(30_000, 1000 * 2 ** min(self.failures, 5))
                if self.failures >= 8:
                    self.error = "FEED_RECOVERY_EXHAUSTED"
                return None
            if kind == "feed-connect":
                self.info = value
                self.last_message = now_ms
                self.socket_generation += 1
                self.next_poll = 0
            else:
                try:
                    result = self._merge(value, now_ms, self.recovering, self.request_generation)
                except Stop as error:
                    self._damage()
                    self.failures += 1
                    if error.code in {"COMMITTED_CANDLE_CHANGED", "CANDLE_CONFLICT", "CANDLE_HISTORY_BOUND"}:
                        self.error = error.code
                    if self.failures >= 8 and not self.error:
                        self.error = "FEED_RECOVERY_EXHAUSTED"
                    self.next_poll = now_ms + min(30_000, 1000 * 2 ** min(self.failures, 5))
                    return None
                self.next_poll = now_ms + 1000
                return result
        if not self.worker.idle or now_ms < self.next_poll:
            return None
        socket_dead = self.info is not None and hasattr(self.info, "ws_manager") and not self.info.ws_manager.is_alive()
        silent = now_ms - self.last_message > max(30_000, self.duration * 2)
        if self.info is None or socket_dead or silent:
            self._damage()
            self.job = "feed-connect"
            self.worker.submit(self.job, self._connect)
            return None
        latest = (now_ms - self.GRACE_MS) // self.duration * self.duration - self.duration
        if not self.repair.is_set() and self.cursor is not None and latest <= self.cursor:
            return None
        start = self.cursor - 2 * self.duration if self.cursor is not None else latest - (self.CAPACITY - 1) * self.duration
        if (latest - start) // self.duration > self.MAX_BACKFILL:
            self.error = "FEED_REPLAY_TOO_OLD"
            self.healthy = False
            return None
        self.recovering = self.repair.is_set()
        self.request_generation = self.repair_generation
        self.job = "feed-snapshot"
        info = self.info
        self.worker.submit(self.job, lambda: info.candles_snapshot(self.market, self.interval, max(0, start), now_ms))
        return None

    def drain(self):
        """Release a finished feed token during stop, without a new read."""
        if self.job:
            result = self.worker.take(self.job)
            if result is None:
                return
            job, self.job = self.job, None
            if job == "feed-connect" and result[0] and result[1] is not None:
                self.info = result[1]

    def close(self):
        self.closed.set()
        self.healthy = False

    def dispose(self):
        """Called on the shared worker only after bounded owned-order cleanup."""
        info = self.resource or self.info
        if info is not None:
            info.disconnect_websocket()
