"""Private-permission public journals, durable intent and per-account locks."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from twon20.files import open_output, secure_directory
from risk import Stop


def directory(path):
    path = Path(path).expanduser().absolute()
    parent = secure_directory(path.parent)
    try:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    finally:
        os.close(parent)
    return open_output(path)


def exclusive_lock(output, name):
    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=output.descriptor)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise Stop("UNSAFE_LOCK", "Use a regular owned lock file with private permissions.")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError:
        os.close(descriptor)
        raise Stop("RUNNER_BUSY", "Another local runner owns this account or state directory. Keep the existing intent and wait for that process.") from None
    except BaseException:
        os.close(descriptor)
        raise


class AccountGuard:
    """A retained public registry blocks bypassing unresolved intent by path."""
    def __init__(self, output, name):
        self.output, self.name = output, name
        self.reservation = None

    def reserve(self, journal, scope):
        reservation = {"schema": "2n20-runner-account-intent-v1", "scope": scope,
                       "journalDirectory": str(journal.output.path)}
        if self.output.exists(self.name):
            try:
                old = json.loads(self.output.read(self.name, 8192))
            except ValueError:
                raise Stop("REGISTRY_CONFLICT", "The account intent registry is invalid. Preserve it and review recovery; never reset it automatically.") from None
            if not isinstance(old, dict) or set(old) != {"schema", "scope", "journalDirectory"} or old.get("schema") != reservation["schema"] or not isinstance(old.get("journalDirectory"), str) or len(old["journalDirectory"]) > 4096 or not old["journalDirectory"].startswith("/") or any(character in old["journalDirectory"] for character in "\n\r\0"):
                raise Stop("REGISTRY_CONFLICT", "The account registry contains unsupported public state. Preserve it and review recovery.")
            if old != reservation:
                raise Stop("ACTIVE_INTENT", "This account has an unresolved intent in another selected directory or key binding. Resume the original state directory; do not create another experiment or reset the registry.", {"stateDirectory": old["journalDirectory"]})
        else:
            self.output.atomic_json(self.name, reservation)
        self.reservation = reservation

    def clear(self):
        if self.reservation is None:
            raise Stop("REGISTRY_CONFLICT", "There is no matching account reservation to finish.")
        old = json.loads(self.output.read(self.name, 8192))
        if old != self.reservation:
            raise Stop("REGISTRY_CONFLICT", "The account reservation changed; preserve it for review.")
        os.unlink(self.name, dir_fd=self.output.descriptor)
        os.fsync(self.output.descriptor)


@contextlib.contextmanager
def account_lock(account, lock_root=None):
    # Shared across this Unix user's setup/project directories. Never claim
    # this coordinates other users, containers, hosts or arbitrary bots.
    root = Path(lock_root) if lock_root else Path.home() / ".cache" / "2n20-runner-locks"
    if not root.parent.exists():
        parent = secure_directory(root.parent.parent)
        try:
            try: os.mkdir(root.parent.name, 0o700, dir_fd=parent)
            except FileExistsError: pass
        finally: os.close(parent)
    output = directory(root)
    descriptor = None
    try:
        name = hashlib.sha256(account.lower().encode()).hexdigest()
        descriptor = exclusive_lock(output, name + ".lock")
        if not output.exists(".gitignore"): output.write(".gitignore", "*\n!.gitignore\n")
        yield AccountGuard(output, name + ".active.public.json")
    finally:
        if descriptor is not None: os.close(descriptor)
        output.close()


class Journal:
    def __init__(self, path, scope):
        self.output = directory(path)
        self.lock = None
        self.scope = scope
        try:
            self.lock = exclusive_lock(self.output, "runner.lock")
            # Validate prior directory ownership/binding before an account
            # registry can reserve this path or any intent is changed.
            for name in ("smoke.public.json", "collateral.public.json"):
                if self.output.exists(name): self.read(name)
            self.ignore()
        except BaseException:
            if self.lock is not None: os.close(self.lock)
            self.output.close()
            raise

    def ignore(self):
        # This ignore exists before journals; preserve every unrelated rule.
        if not self.output.exists(".gitignore"):
            self.output.write(".gitignore", "*\n!.gitignore\n")
        else:
            text = self.output.read(".gitignore", 16384)
            if not text.endswith("*\n!.gitignore\n"):
                import secrets
                temporary = ".gitignore." + secrets.token_hex(8) + ".tmp"
                self.output.write(temporary, text + ("" if text.endswith("\n") else "\n") + "*\n!.gitignore\n")
                try:
                    os.replace(temporary, ".gitignore", src_dir_fd=self.output.descriptor, dst_dir_fd=self.output.descriptor)
                    os.fsync(self.output.descriptor)
                finally:
                    if self.output.exists(temporary): os.unlink(temporary, dir_fd=self.output.descriptor)

    def close(self):
        os.close(self.lock)
        self.output.close()

    def read(self, name):
        if not self.output.exists(name): return None
        value = json.loads(self.output.read(name, 16384))
        if not isinstance(value, dict) or value.get("schema") != "2n20-starter-journal-v1" or value.get("scope") != self.scope:
            raise Stop("JOURNAL_CONFLICT", "This journal belongs to another verified account/key. Preserve it and review recovery.")
        return value

    def save(self, name, value):
        self.output.atomic_json(name, {"schema": "2n20-starter-journal-v1", "scope": self.scope, **value})

    def public_path(self, name):
        return str(self.output.path / name)
