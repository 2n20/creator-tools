"""Explicit, local-only distribution of bundled portable skills."""

import os
import stat
from importlib.resources import files
from pathlib import Path

from . import __version__
from .errors import SetupError
from .files import secure_directory

SKILL_NAMES = ("2n20-setup", "2n20-strategy")
AGENT_PATHS = {"codex": ".agents", "cursor": ".cursor", "claude": ".claude"}


def _contents(name):
    if name not in SKILL_NAMES:
        raise SetupError("UNKNOWN_SKILL", "Choose a bundled skill name.")
    root = files("twon20").joinpath("skills", name)
    result = {}

    def visit(directory, prefix=""):
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            if entry.name in (".", "..") or "/" in entry.name or "\\" in entry.name:
                raise SetupError("INVALID_SKILL_ASSET", "Invalid bundled skill path.")
            if isinstance(entry, Path) and entry.is_symlink():
                raise SetupError("INVALID_SKILL_ASSET", "Bundled skill assets cannot be symlinks.")
            relative = prefix + entry.name
            if entry.is_dir():
                visit(entry, relative + "/")
            elif entry.is_file():
                result[relative] = entry.read_bytes()
            else:
                raise SetupError("INVALID_SKILL_ASSET", "Unsupported bundled skill asset.")

    visit(root)
    return result


def _directory(parent, name):
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _matches(descriptor, contents, prefix=""):
    expected = {path[len(prefix):].split("/")[0] for path in contents if path.startswith(prefix)}
    if set(os.listdir(descriptor)) != expected:
        return False
    for name in expected:
        path = prefix + name
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if path in contents:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return False
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    return False
                if handle.read(len(contents[path]) + 1) != contents[path]:
                    return False
        elif stat.S_ISDIR(info.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                if not _matches(fd, contents, path + "/"):
                    return False
            finally:
                os.close(fd)
        else:
            return False
    return True


def _existing(parent, name, contents):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        return False
    try:
        if not _matches(fd, contents):
            raise SetupError("SKILL_CONFLICT", "An existing skill differs from the bundled version. Preserve it and choose a different project; nothing was overwritten.")
    finally:
        os.close(fd)
    return True


def _write(parent, name, contents):
    os.mkdir(name, mode=0o700, dir_fd=parent)
    root = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        for relative, data in contents.items():
            directory = os.dup(root)
            try:
                parts = relative.split("/")
                for part in parts[:-1]:
                    following = _directory(directory, part)
                    os.close(directory)
                    directory = following
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(directory)
    finally:
        os.close(root)


def export_skill(directory: Path, name="2n20-setup"):
    contents = _contents(name)
    directory = directory.expanduser().absolute()
    parent = secure_directory(directory.parent)
    try:
        _write(parent, directory.name, contents)
    except FileExistsError:
        raise SetupError("SKILL_DIRECTORY_EXISTS", "The export directory already exists. Choose a new directory; existing skill files were not changed.") from None
    finally:
        os.close(parent)
    return {"skill": name, "version": __version__, "skillFile": str(directory / "SKILL.md"),
            "files": [str(directory / path) for path in contents],
            "nextAction": "Load this portable skill in your agent harness on the strategy machine. Local tools are required to execute its guidance."}


def install_skills(agent: str, project: Path | None = None, name="all"):
    names = SKILL_NAMES if name == "all" else (name,)
    bundles = {item: _contents(item) for item in names}
    if agent == "hermes":
        if project is not None:
            raise SetupError("HERMES_PROJECT_UNSUPPORTED", "Hermes installs into HERMES_HOME/skills (default ~/.hermes/skills); omit --project.")
        base = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().absolute()
        # Existing ancestors are verified; only the explicit Hermes home may be created.
        parent = secure_directory(base.parent)
        try:
            root = _directory(parent, base.name)
        finally:
            os.close(parent)
    elif agent in AGENT_PATHS:
        project = (project or Path.cwd()).expanduser().absolute()
        parent = secure_directory(project)
        base = project / AGENT_PATHS[agent]
        try:
            root = _directory(parent, AGENT_PATHS[agent])
        finally:
            os.close(parent)
    else:
        raise SetupError("UNKNOWN_AGENT", "Choose codex, cursor, claude or hermes.")
    try:
        destination = _directory(root, "skills")
    finally:
        os.close(root)
    installed = []
    try:
        existing = {item: _existing(destination, item, bundle) for item, bundle in bundles.items()}
        for item, bundle in bundles.items():
            if not existing[item]:
                _write(destination, item, bundle)
            installed.append({"skill": item, "status": "unchanged" if existing[item] else "installed",
                              "files": [str(base / "skills" / item / path) for path in bundle]})
    finally:
        os.close(destination)
    return {"agent": agent, "version": __version__, "skills": installed,
            "files": [path for item in installed for path in item["files"]],
            "nextAction": "Reload your agent's skills and use 2n20-strategy or 2n20-setup as needed."}
