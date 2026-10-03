"""Exclude the generated key path from Git before its first private write."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from .errors import SetupError


def git(directory: Path, *arguments):
    try:
        return subprocess.run(["git", "-C", str(directory), *arguments], capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        raise SetupError("GIT_CHECK_UNAVAILABLE", "Git could not verify this checkout in time. No private key was created.") from None


def ensure_key_ignored(directory: Path):
    if not shutil.which("git"):
        # A checkout can still exist when git is unavailable. Do not assume it is safe.
        if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
            raise SetupError("GIT_CHECK_UNAVAILABLE", "Install Git to verify this checkout's key exclusion before creating a key.")
        return
    located = git(directory, "rev-parse", "--show-toplevel")
    if located.returncode:
        if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
            raise SetupError("GIT_CHECK_UNAVAILABLE", "Git could not verify this checkout. No private key was created.")
        return
    root = Path(located.stdout.strip()).resolve()
    relative = (directory / "strategy.key").relative_to(root).as_posix()
    if git(root, "ls-files", "--error-unmatch", "--", relative).returncode == 0:
        raise SetupError("TRACKED_KEY_PATH", "The private key path is already tracked by Git. Stop and untrack it yourself before continuing; no key was read or written.")
    ignored = git(root, "check-ignore", "--quiet", "--no-index", "--", relative)
    if ignored.returncode == 0:
        return
    if ignored.returncode != 1 or any(character in relative for character in "\r\n"):
        raise SetupError("GIT_CHECK_UNAVAILABLE", "Git could not verify the private key exclusion. No key was created.")
    import fcntl
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
    descriptor = os.open(root / ".gitignore", flags, 0o644)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise SetupError("UNSAFE_GITIGNORE", "The checkout's .gitignore cannot safely be updated. No key was created.")
        fcntl.flock(handle, fcntl.LOCK_EX)
        if git(root, "check-ignore", "--quiet", "--no-index", "--", relative).returncode != 0:
            escaped = re.sub(r"([\\*?\[\]#! ])", r"\\\1", relative)
            handle.write("\n# Local 2n20 trading key\n/" + escaped + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    if git(root, "check-ignore", "--quiet", "--no-index", "--", relative).returncode != 0:
        raise SetupError("GIT_EXCLUSION_FAILED", "The private key exclusion could not be confirmed. No key was created.")
