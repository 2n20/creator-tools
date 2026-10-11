"""One bounded atomic checkpoint for bars, strategy, targets, fills and reports."""
import json

from journal import Journal
from risk import Stop

NAME = "runtime.public.json"
MAX_BYTES = 262144
MAX_HISTORY = 128
MAX_REPORTS = 64
MAX_FILLS = 256


class RuntimeJournal(Journal):
    def read_runtime(self):
        if not self.output.exists(NAME):
            return None
        try:
            state = json.loads(self.output.read(NAME, MAX_BYTES))
            self.validate(state)
            return state
        except (ValueError, TypeError, KeyError):
            raise Stop("RUNTIME_CHECKPOINT_INVALID", "Preserve the runtime checkpoint and review its bounded schema.") from None

    def validate(self, state):
        if not isinstance(state, dict) or state.get("schema") != "2n20-runtime-checkpoint-v1" or state.get("scope") != self.scope:
            raise Stop("RUNTIME_CHECKPOINT_CONFLICT", "The runtime checkpoint belongs to another scope or unsupported schema.")
        if len(state.get("history", [])) > MAX_HISTORY or len(state.get("reports", [])) > MAX_REPORTS:
            raise Stop("RUNTIME_CAPACITY", "Checkpoint capacity is exhausted; preserve all evidence and inspect local reports.")
        child = state.get("execution", {}).get("child")
        if child and len(child.get("fills", [])) > MAX_FILLS:
            raise Stop("FILL_CAPACITY", "Owned-child fill evidence exceeds the reviewed bound; preserve the child identity.")
        # Save and read apply the identical byte bound. Never truncate evidence.
        raw = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_BYTES - 1024:
            raise Stop("RUNTIME_CAPACITY", "The atomic checkpoint exceeded its byte limit; no effect is authorized.")

    def save_runtime(self, state):
        self.validate(state)
        # Output.atomic_json uses indented serialization. Check exactly that
        # representation as well as the compact structural budget.
        if len((json.dumps(state, indent=2, sort_keys=True) + "\n").encode()) > MAX_BYTES:
            raise Stop("RUNTIME_CAPACITY", "The serialized checkpoint exceeds its read/write byte limit.")
        self.output.atomic_json(NAME, state)
