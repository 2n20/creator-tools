"""Verify archives and installed, bundled assets without private checkout access."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from build_release import validate_names


def run(*arguments, cwd):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-m", "twon20", *arguments], cwd=cwd,
                            env=environment, capture_output=True, text=True, check=True)
    return json.loads(result.stdout) if "--json" in arguments else result.stdout


def main():
    root = Path(__file__).resolve().parent.parent
    for artifact in sorted((root / "dist").iterdir()):
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
        else:
            with tarfile.open(artifact) as archive:
                names = archive.getnames()
        validate_names(names)
        for required in ("skills/2n20-setup/SKILL.md", "templates/hyperliquid-python/runner.py",
                         "templates/hyperliquid-python/requirements-runner.txt"):
            assert any(name.endswith(required) for name in names), required
    with tempfile.TemporaryDirectory(prefix="creator-tools-artifact-") as temporary:
        working = Path(temporary).resolve()
        run("--version", cwd=working)
        run("onboard", "--help", cwd=working)
        assert "--paper-checked" in run("complete-setup", "--help", cwd=working)
        run("skill", "export", "--directory", str(working / "skill"), cwd=working)
        assert (working / "skill/SKILL.md").is_file()
        result = run("init", str(working / "project"), "--template", "hyperliquid-python", "--json", cwd=working)
        assert result["keysCreated"] is False and result["dependenciesInstalled"] is False
        assert not list((working / "project").rglob("*.key"))
        subprocess.run([sys.executable, "runner.py"], cwd=working / "project", check=True)
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                       cwd=working / "project", check=True)
    print("Installed CLI, skill, template, paper mode and starter tests verified.")


if __name__ == "__main__":
    main()
