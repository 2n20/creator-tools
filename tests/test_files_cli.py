import argparse
import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from twon20.cli import main, renew, setup
from twon20.discovery import discover
from twon20.errors import SetupError
from twon20.files import load_key, load_setup, new_output, open_output, read_public, retain_key

from support import LINK, FakeRpc


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.rpc = FakeRpc()
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)

    def output(self):
        return new_output(self.found, self.parent / "setup")

    def test_exclusive_private_creation_permissions_and_recovery(self):
        output = self.output()
        self.addCleanup(output.close)
        wallet = retain_key(output, self.found)
        self.assertEqual(stat.S_IMODE(output.path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((output.path / "strategy.key").stat().st_mode), 0o600)
        recovered = load_key(output, load_setup(output))
        self.assertEqual(wallet.address, recovered.address)
        with self.assertRaises(FileExistsError):
            output.write("strategy.key", "not-a-key")
        self.assertEqual(load_key(output, load_setup(output)).address, wallet.address)
        with self.assertRaises(SetupError) as caught:
            self.output()
        self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")

    def test_directory_and_key_symlinks_hardlinks_and_loose_permissions_rejected(self):
        real = self.parent / "real"
        real.mkdir(mode=0o700)
        alias = self.parent / "alias"
        alias.symlink_to(real)
        with self.assertRaises(OSError):
            new_output(self.found, alias / "setup")
        output = self.output()
        self.addCleanup(output.close)
        wallet = retain_key(output, self.found)
        path = output.path / "strategy.key"
        path.chmod(0o644)
        with self.assertRaises(SetupError):
            load_key(output, load_setup(output))
        path.chmod(0o600)
        os.link(path, self.parent / "extra.key")
        with self.assertRaises(SetupError):
            load_key(output, load_setup(output))
        (self.parent / "extra.key").unlink()
        backup = output.path / "backup.key"
        path.rename(backup)
        path.symlink_to(backup)
        with self.assertRaises(OSError):
            load_key(output, load_setup(output))
        self.assertNotIn(wallet.key.hex(), str(path))

    def test_public_reader_rejects_private_file_names(self):
        for name in ("strategy.key", "config.json", ".env", "notes.txt"):
            with self.assertRaises(SetupError) as caught:
                read_public(self.parent / name)
            self.assertEqual(caught.exception.code, "PUBLIC_FILE_ONLY")

    def test_missing_parent_and_existing_output_never_create_or_replace_keys(self):
        with self.assertRaises(OSError):
            new_output(self.found, self.parent / "absent" / "setup")
        output = self.output()
        self.addCleanup(output.close)
        wallet = retain_key(output, self.found)
        before = (output.path / "strategy.key").read_bytes()
        with self.assertRaises(SetupError):
            new_output(self.found, output.path)
        self.assertEqual(before, (output.path / "strategy.key").read_bytes())
        self.assertEqual(wallet.address, load_key(output, load_setup(output)).address)

    def test_network_failure_retains_key_and_renew_never_overwrites(self):
        args = argparse.Namespace(vault=LINK, output=self.parent / "setup")
        with patch("twon20.cli.discover", return_value=self.found), patch("twon20.cli.Rpc", return_value=self.rpc), patch("twon20.cli.prepare", side_effect=SetupError("RPC_UNAVAILABLE", "Public fixture failed")):
            with self.assertRaises(SetupError) as caught:
                setup(args)
        self.assertIn("onboard --vault", caught.exception.message)
        output = open_output(args.output)
        self.addCleanup(output.close)
        wallet = load_key(output, load_setup(output))
        before = (output.path / "strategy.key").read_bytes()
        with patch("twon20.cli.discover", return_value=self.found), patch("twon20.cli.Rpc", return_value=self.rpc):
            first = renew(argparse.Namespace(directory=args.output))
            second = renew(argparse.Namespace(directory=args.output))
        self.assertNotEqual(first["publicConsentFile"], second["publicConsentFile"])
        self.assertEqual(before, (output.path / "strategy.key").read_bytes())
        self.assertEqual(first["tradingKey"], wallet.address)
        self.assertTrue(Path(first["publicConsentFile"]).exists())

    def test_cli_setup_and_failures_never_print_private_material(self):
        args = ["setup", "--vault", LINK, "--output", str(self.parent / "setup"), "--json"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("twon20.cli.discover", return_value=self.found), patch("twon20.cli.Rpc", return_value=self.rpc), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 1)
        output = open_output(self.parent / "setup")
        self.addCleanup(output.close)
        wallet = load_key(output, load_setup(output))
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(wallet.key.hex(), combined)
        self.assertNotIn(wallet.key.hex().removeprefix("0x"), combined)
        self.assertIn("consent.public.json", combined)
        self.assertIn("OUTPUT_EXISTS", combined)
        self.assertNotIn("Traceback", combined)

    def test_setup_fails_before_creating_output_when_discovery_fails(self):
        path = self.parent / "setup"
        with patch("twon20.cli.discover", side_effect=SetupError("WRONG_CHAIN", "Wrong chain")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["setup", "--vault", LINK, "--output", str(path)]), 1)
        self.assertFalse(path.exists())
