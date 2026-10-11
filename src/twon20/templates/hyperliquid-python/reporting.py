"""Durable local report snapshots; optional bounded best-effort Telegram delivery."""
import json
import os
from queue import Empty, Full, Queue
import re
from threading import Event, Thread
import urllib.request

from risk import Stop


class ConsoleReporter:
    def emit(self, report):
        print(json.dumps({"schema": "2n20-runtime-report-v1", **report}, sort_keys=True), flush=True)

    def close(self):
        pass


class TelegramReporter:
    """Credentials are consumed only by the explicitly enabled local runtime.

    No exception, URL or credentials enter a report. Local checkpoint reports
    are authoritative. Lost acknowledgements make exactly-once delivery
    impossible; event IDs let a reader recognize a duplicate.
    """
    def __init__(self, local=None):
        self.local = local or ConsoleReporter()
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
        if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,128}", token) or not re.fullmatch(r"-?[0-9]{1,24}", chat):
            raise Stop("TELEGRAM_CONFIG_REQUIRED", "Set the two documented Telegram environment variables locally; never put values in public configuration.")
        self.url, self.chat = "https://api.telegram.org/bot" + token + "/sendMessage", chat
        self.queue, self.closed = Queue(maxsize=16), Event()
        self.thread = Thread(target=self._deliver, name="2n20-reporting", daemon=True)
        self.thread.start()

    def emit(self, report):
        self.local.emit(report)
        # A full telemetry queue loses remote delivery only, never local state.
        try:
            self.queue.put_nowait(json.dumps(report, sort_keys=True)[:3500])
        except Full:
            pass

    def _deliver(self):
        while not self.closed.is_set():
            try:
                text = self.queue.get(timeout=0.25)
            except Empty:
                continue
            data = json.dumps({"chat_id": self.chat, "text": text}).encode()
            for attempt in range(2):
                try:
                    request = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(request, timeout=3) as response:
                        if response.status != 200:
                            raise ValueError()
                        response.read(8192)
                    break
                except Exception:
                    if self.closed.wait(0.5 * (attempt + 1)):
                        break

    def close(self):
        self.closed.set()
        self.local.close()
