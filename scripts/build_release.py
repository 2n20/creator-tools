#!/usr/bin/env python3
"""Build standard Python distributions with reproducible archive metadata.

Run with the reviewed release environment and a fixed SOURCE_DATE_EPOCH.
The wheel uses the backend's standard reproducibility support. The sdist is
normalized after python -m build to remove local ownership and clock metadata.
"""

import argparse
import copy
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def validate_names(names):
    for name in names:
        path = PurePosixPath(name)
        basename = path.name
        runtime_directories = {".local", ".venv", ".runner-state", ".git", ".agents", ".cursor", ".claude", "node_modules"}
        runtime_files = {"setup.public.json", "onboarding.public.json", "key-reservation.public.json", "smoke.public.json", "collateral.public.json", "strategy.public.json"}
        if path.is_absolute() or ".." in path.parts or runtime_directories.intersection(path.parts) or basename in runtime_files or basename in {"config.json", "notes.txt", ".env", ".pypirc"} or basename.startswith(".env.") or basename.endswith((".key", ".pem", ".log")):
            raise ValueError("Distribution contains a forbidden path; publication is blocked.")


def normalize_sdist(source: Path, target: Path, epoch: int):
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        validate_names(member.name for member in members)
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError("Distribution contains unsupported archive entries; publication is blocked.")
        with target.open("xb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=epoch) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as normalized:
                    for original in sorted(members, key=lambda member: member.name):
                        member = copy.copy(original)
                        member.mtime = epoch
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        member.pax_headers = {}
                        member.mode = 0o755 if member.isdir() else 0o644
                        content = archive.extractfile(original) if original.isfile() else None
                        try:
                            normalized.addfile(member, content)
                        finally:
                            if content is not None:
                                content.close()


def build(outdir: Path, epoch: int):
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="2n20-release-build-") as temporary:
        staging = Path(temporary)
        raw_dir = staging / "raw"
        normalized_dir = staging / "normalized"
        normalized_dir.mkdir()
        subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(raw_dir)], cwd=PACKAGE_ROOT, check=True)
        artifacts = sorted(raw_dir.iterdir())
        if len(artifacts) != 2 or sum(artifact.suffix == ".whl" for artifact in artifacts) != 1 or sum(artifact.name.endswith(".tar.gz") for artifact in artifacts) != 1:
            raise ValueError("Build must produce exactly one wheel and one sdist.")
        for artifact in artifacts:
            destination = normalized_dir / artifact.name
            if artifact.suffix == ".whl":
                with zipfile.ZipFile(artifact) as wheel:
                    validate_names(wheel.namelist())
                shutil.copyfile(artifact, destination)
            else:
                normalize_sdist(artifact, destination, epoch)
            target = outdir / artifact.name
            replacement = outdir / (artifact.name + ".tmp")
            with destination.open("rb") as source, replacement.open("xb") as handle:
                shutil.copyfileobj(source, handle)
            replacement.replace(target)
            print(f"SHA256 {hashlib.sha256(destination.read_bytes()).hexdigest()}  {artifact.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=PACKAGE_ROOT / "dist")
    args = parser.parse_args()
    raw_epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not raw_epoch.isascii() or not raw_epoch.isdecimal() or not 0 <= int(raw_epoch) <= 0xFFFFFFFF:
        parser.error("Set SOURCE_DATE_EPOCH to the release's fixed Unix timestamp.")
    try:
        build(args.outdir.expanduser().absolute(), int(raw_epoch))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Release build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
