"""Explicit portable skill export, with no automatic agent configuration writes."""

import os
from importlib.resources import files
from pathlib import Path

from .errors import SetupError
from .files import open_output, secure_directory


def export_skill(directory: Path):
    directory = directory.expanduser().absolute()
    parent = secure_directory(directory.parent)
    try:
        os.mkdir(directory.name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        raise SetupError("SKILL_DIRECTORY_EXISTS", "The export directory already exists. Choose a new directory; existing skill files were not changed.") from None
    finally:
        os.close(parent)
    output = open_output(directory)
    try:
        contents = files("twon20").joinpath("skills/2n20-setup/SKILL.md").read_text(encoding="utf-8")
        output.write("SKILL.md", contents)
    finally:
        output.close()
    return {"skill": "2n20-setup", "skillFile": str(directory / "SKILL.md"),
            "nextAction": "Load this portable skill in your agent harness on the strategy machine. No other harness files were changed."}
