"""One in-flight network task, no request cancellation fiction or shared state writes."""
from queue import Empty, Queue
from threading import Event, Thread
import time

from risk import Stop


class SerialIO:
    def __init__(self):
        self.requests, self.results = Queue(maxsize=1), Queue(maxsize=1)
        self.token = None
        self.closed = False
        self.finalizer = None
        self.thread = Thread(target=self._work, name="2n20-network", daemon=True)
        self.thread.start()

    @property
    def idle(self):
        return self.token is None and not self.closed

    def submit(self, token, function):
        if not self.idle:
            return False
        self.token = token
        self.requests.put_nowait((token, function))
        return True

    def take(self, token):
        if self.token != token:
            return None
        try:
            result = self.results.get_nowait()
        except Empty:
            return None
        self.token = None
        return result

    def _work(self):
        try:
            while True:
                task = self.requests.get()
                if task is None:
                    return
                _, function = task
                try:
                    value = (True, function())
                except Stop as error:
                    value = (False, error.code)
                except BaseException:
                    value = (False, "NETWORK_OPERATION_UNAVAILABLE")
                self.results.put(value)
                if self.closed:
                    return
        finally:
            if self.finalizer:
                try:
                    self.finalizer()
                except Exception:
                    pass

    def close(self, finalizer=None):
        if finalizer is not None:
            self.finalizer = finalizer
        if self.closed:
            return
        self.closed = True
        # The final SDK socket shutdown can join internal threads, so it runs
        # here, after owned cleanup, never on the foreground coordinator.
        try:
            self.requests.put_nowait(None)
        except Exception:
            pass


class Admission:
    """Checked in the worker immediately before signing and each SDK effect."""
    def __init__(self, clock=None, session_deadline=None, cleanup_deadline=None):
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.session_deadline = session_deadline
        self.cleanup_deadline = cleanup_deadline if cleanup_deadline is not None else session_deadline
        self.stopping = Event()
        self.feed_ready = Event()
        self.generation = None
        self.feed_check = lambda: True

    def check(self, intent_id, deadline, cleanup=False):
        now = self.clock()
        # Cleanup is permitted on feed failure/stop, inside the originally
        # retained session authorization only. No deadline is renewed on restart.
        limit = self.cleanup_deadline if cleanup else self.session_deadline
        if limit is not None and now >= limit:
            raise Stop("SESSION_EXPIRED", "The retained live session expired. Preserve owned child identity for explicit recovery.")
        if cleanup:
            return
        if self.stopping.is_set():
            raise Stop("RUNTIME_STOPPED", "The stop request forbids a new order.")
        if self.generation != intent_id:
            raise Stop("TARGET_SUPERSEDED", "A newer target replaced this order's intent.")
        if now >= deadline:
            raise Stop("CHASE_EXPIRED", "The retained target deadline expired before submission.")
        if not self.feed_ready.is_set() or not self.feed_check():
            raise Stop("FEED_NOT_READY", "Fresh, fully processed candle data is required before submission.")
