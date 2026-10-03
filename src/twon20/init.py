"""Create a reviewed starter project without touching an existing directory."""

import hashlib
import ctypes
import errno
import os
import secrets
import shlex
import stat
import sys
from importlib.resources import files
from pathlib import Path, PurePosixPath

from . import __version__
from .errors import SetupError
from .files import Output, secure_directory

TEMPLATE = "hyperliquid-python"
PUBLIC_SOURCE = "https://github.com/2n20/creator-tools"
MANIFEST = "2n20-template.public.json"
REQUIRED_ASSETS = frozenset({"README.md", "runner.py", "strategy.py", "adapter.py", "risk.py", "journal.py", "execution.py", "requirements-runner.txt", "config.public.example.json", "fixture.candles.public.json", "dependency-review.json"})


def approved_asset(name):
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return False
    if len(path.parts) == 2 and path.parts[0] == "tests":
        return path.name.endswith(".py")
    return len(path.parts) == 1 and (path.name in REQUIRED_ASSETS or path.name == ".gitignore" or path.name.endswith((".py", ".md", ".public.json")))


def template_assets(template):
    if template != TEMPLATE:
        raise SetupError("UNSUPPORTED_TEMPLATE", "Choose the supported hyperliquid-python template.")
    root = files("twon20").joinpath("templates", template)
    assets = {}

    def visit(folder, prefix=""):
        for child in sorted(folder.iterdir(), key=lambda item: item.name):
            if child.name == "__pycache__" or child.name.endswith((".pyc", ".pyo")):
                continue
            name = prefix + child.name
            if hasattr(child, "is_symlink") and child.is_symlink():
                raise SetupError("UNSAFE_TEMPLATE", "The bundled template contains a symbolic link. No project was created.")
            if child.is_dir():
                if name != "tests":
                    raise SetupError("UNSAFE_TEMPLATE", "The bundled template has an unsupported asset directory.")
                visit(child, name + "/")
            elif child.is_file() and approved_asset(name):
                data = child.read_bytes()
                if len(data) > 262144:
                    raise SetupError("UNSAFE_TEMPLATE", "A bundled template asset exceeds the supported size.")
                assets[name] = data.decode("utf-8")
            else:
                raise SetupError("UNSAFE_TEMPLATE", "The bundled template contains an unsupported asset. No project was created.")

    try:
        visit(root)
    except (FileNotFoundError, UnicodeError):
        raise SetupError("TEMPLATE_UNAVAILABLE", "The installed release does not contain a complete reviewed starter. Reinstall the published official release.") from None
    if not REQUIRED_ASSETS <= assets.keys() or sum(len(text.encode("utf-8")) for text in assets.values()) > 2097152:
        raise SetupError("TEMPLATE_UNAVAILABLE", "The installed release does not contain a complete reviewed starter. No project was created.")
    return assets


def remove_staged(parent, name):
    """Delete only this command's staged tree through no-follow directory handles."""
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        with os.scandir(descriptor) as children:
            for child in children:
                if child.is_dir(follow_symlinks=False):
                    remove_staged(descriptor, child.name)
                else:
                    os.unlink(child.name, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def project_commands(directory):
    return {
        "enterProject": shlex.join(["cd", str(directory)]),
        "createEnvironment": "python3 -m venv .venv",
        "installReviewedRunner": ".venv/bin/python -m pip --isolated install --require-hashes --only-binary=:all: -r requirements-runner.txt",
        "installPublishedCli": f".venv/bin/python -m pip --isolated install 2n20=={__version__}",
        "onboard": ".venv/bin/2n20 onboard --vault '<official vault page URL>' --json",
        "checkConnection": ".venv/bin/python runner.py check --vault '<official vault page URL>' --directory '<retained setup directory>' --market BTC",
        "paper": ".venv/bin/python runner.py",
        "paperPublicData": ".venv/bin/python runner.py paper --public-data --market BTC",
    }


def publish_staged(parent, staged, destination):
    """Atomically publish without replacing even an empty concurrent destination."""
    if sys.platform == "darwin":
        name, flag = "renameatx_np", 0x00000004  # Apple RENAME_EXCL, sys/stdio.h.
    elif sys.platform.startswith("linux"):
        name, flag = "renameat2", 1  # Linux RENAME_NOREPLACE, uapi/linux/fs.h.
    else:
        raise SetupError("UNSUPPORTED_FILESYSTEM", "Starter generation requires Linux, macOS or WSL with atomic exclusive directory publication.")
    try:
        operation = getattr(ctypes.CDLL(None, use_errno=True), name)
    except (AttributeError, OSError):
        raise SetupError("UNSUPPORTED_FILESYSTEM", "The system cannot publish this project atomically without overwriting an existing directory.") from None
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    ctypes.set_errno(0)
    if operation(parent, os.fsencode(staged), parent, os.fsencode(destination), flag) != 0:
        code = ctypes.get_errno() or errno.EIO
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise SetupError("PROJECT_EXISTS", "The project directory appeared during generation. Existing files were not read or changed.")
        if code in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
            raise SetupError("UNSUPPORTED_FILESYSTEM", "This filesystem cannot publish the project atomically without replacing an existing directory.")
        raise OSError(code, os.strerror(code))


def init_project(directory: Path, template=TEMPLATE):
    directory = directory.expanduser().absolute()
    parent = secure_directory(directory.parent)
    staged = None
    try:
        info = os.fstat(parent)
        shared_writes = stat.S_IMODE(info.st_mode) & 0o022
        if info.st_uid not in (os.getuid(), 0) or shared_writes and not info.st_mode & stat.S_ISVTX:
            raise SetupError("UNSAFE_PROJECT_PARENT", "Choose a parent directory owned by you with no shared write access, or a root-owned sticky temporary directory.")
        try:
            os.stat(directory.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SetupError("PROJECT_EXISTS", "The project directory already exists. Choose a new directory; existing files were not read or changed.")
        assets = template_assets(template)
        staged = ".2n20-init-" + secrets.token_hex(12) + ".tmp"
        os.mkdir(staged, mode=0o700, dir_fd=parent)
        stage_path = directory.parent / staged
        for name, contents in assets.items():
            relative = PurePosixPath(name)
            descriptor = os.open(staged, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                for part in relative.parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = following
                output = Output(stage_path / relative.parent, descriptor)
                output.write(relative.name, contents)
            finally:
                os.close(descriptor)
        descriptor = os.open(staged, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        output = Output(stage_path, descriptor)
        try:
            output.json(MANIFEST, {"schema": "2n20-project-template-v1", "template": template, "version": __version__, "source": PUBLIC_SOURCE,
                                  "files": {name: hashlib.sha256(contents.encode("utf-8")).hexdigest() for name, contents in assets.items()}})
        finally:
            output.close()
        publish_staged(parent, staged, directory.name)
        staged = None
        os.fsync(parent)
        return {"schema": "2n20-project-init-v1", "initialized": True, "directory": str(directory), "template": template, "templateVersion": __version__,
                "publicManifestFile": str(directory / MANIFEST), "files": sorted([*assets, MANIFEST]),
                "commands": project_commands(directory), "keysCreated": False, "dependenciesInstalled": False,
                "nextAction": "Create the environment and install reviewed requirements. Run the paper example, then onboard with your vault link on this strategy machine. Keep live execution disabled until separately authorized."}
    finally:
        try:
            if staged is not None:
                try:
                    remove_staged(parent, staged)
                except FileNotFoundError:
                    pass
        finally:
            os.close(parent)
