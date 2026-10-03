"""Private files are created exclusively and accessed through directory handles."""

import json
import os
import re
import secrets
import stat
from pathlib import Path

from eth_account import Account

from .discovery import Discovery, address
from .errors import SetupError
from .rpc import parse_json
from .git_safety import ensure_key_ignored


def secure_directory(path: Path) -> int:
    if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY")) or not hasattr(os, "getuid"):
        raise SetupError("UNSUPPORTED_FILESYSTEM", "Run setup on Linux, macOS or WSL with private file permissions.")
    path = path.expanduser().absolute()
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class Output:
    def __init__(self, path: Path, descriptor: int):
        self.path = path
        self.descriptor = descriptor

    def close(self):
        os.close(self.descriptor)

    def write(self, name: str, text: str):
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(self.descriptor)

    def json(self, name: str, value: dict):
        self.write(name, json.dumps(value, indent=2) + "\n")
        return self.path / name

    def exists(self, name: str) -> bool:
        try:
            os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def atomic_json(self, name: str, value: dict):
        if self.exists(name):
            info = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise SetupError("UNSAFE_CHECKPOINT", "The public checkpoint is not a safe local file. No key was changed.")
        temporary = name + "." + secrets.token_hex(8) + ".tmp"
        self.json(temporary, value)
        try:
            os.replace(temporary, name, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)
            os.fsync(self.descriptor)
        finally:
            if self.exists(temporary):
                os.unlink(temporary, dir_fd=self.descriptor)

    def read(self, name: str, limit: int) -> str:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.descriptor)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise SetupError("UNSAFE_FILE_PERMISSIONS", "The setup file must be a regular file owned by you with mode 600. Do not share its private material.")
            text = handle.read(limit + 1)
            if len(text) > limit:
                raise SetupError("INVALID_SETUP_FILE", "The setup file has an unexpected format. No private material was displayed.")
            return text


def open_output(path: Path) -> Output:
    path = path.expanduser().absolute()
    descriptor = secure_directory(path)
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(descriptor)
        raise SetupError("UNSAFE_DIRECTORY_PERMISSIONS", "The setup directory must be owned by you with mode 700.")
    return Output(path, descriptor)


def new_output(found: Discovery, path: Path | None = None) -> Output:
    path = (path if path is not None else Path.cwd() / f"2n20-{found.vault[2:10]}-{secrets.token_hex(6)}").expanduser().absolute()
    parent = secure_directory(path.parent)
    try:
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
    except FileExistsError:
        raise SetupError("OUTPUT_EXISTS", "The output directory already exists. No key was read or overwritten. Use a new directory, or renew your existing setup with 2n20 renew --directory <directory>.") from None
    finally:
        os.close(parent)
    return open_output(path)


def retain_key(output: Output, found: Discovery):
    ensure_key_ignored(output.path)
    output.json("key-reservation.public.json", {"schema": "2n20-key-reservation-v1", "vaultLink": found.link})
    wallet = Account.create()
    output.write("strategy.key", wallet.key.hex().removeprefix("0x") + "\n")
    output.json("setup.public.json", {"schema": "2n20-local-setup-v1", "vaultLink": found.link, "tradingKey": wallet.address})
    return wallet


def load_setup(output: Output):
    try:
        name = "setup.recovered.public.json" if output.exists("setup.recovered.public.json") else "setup.public.json"
        value = parse_json(output.read(name, 2000))
    except ValueError:
        raise SetupError("INVALID_SETUP_FILE", "This directory does not contain a valid 2n20 setup record.") from None
    if not isinstance(value, dict) or set(value) != {"schema", "vaultLink", "tradingKey"} or value["schema"] != "2n20-local-setup-v1" or not isinstance(value["vaultLink"], str):
        raise SetupError("INVALID_SETUP_FILE", "This directory does not contain a valid 2n20 setup record.")
    return value


def load_key(output: Output, metadata: dict):
    ensure_key_ignored(output.path)
    text = output.read("strategy.key", 128).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
        raise SetupError("INVALID_PRIVATE_FILE", "The local key file has an unexpected format. No private material was displayed or changed.")
    try:
        wallet = Account.from_key(text)
    except ValueError:
        raise SetupError("INVALID_PRIVATE_FILE", "The local key file cannot be used. No private material was displayed or changed.") from None
    if address(wallet.address) != address(metadata["tradingKey"]):
        raise SetupError("KEY_RECORD_MISMATCH", "The private file does not match its public setup record. No files were changed.")
    return wallet


def read_public(path: Path) -> str:
    path = path.expanduser().absolute()
    if not path.name.endswith(".public.json"):
        raise SetupError("PUBLIC_FILE_ONLY", "Select a consent.public.json file. Never provide strategy.key, environment files or wallet backups.")
    parent = secure_directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SetupError("PUBLIC_FILE_ONLY", "Select a regular public consent JSON file.")
            text = handle.read(2001)
            if len(text) > 2000:
                raise SetupError("INVALID_CONSENT", "The public consent file exceeds the supported size.")
            return text
    finally:
        os.close(parent)
