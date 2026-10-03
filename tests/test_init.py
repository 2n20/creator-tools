import contextlib
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from twon20 import __version__
from twon20.cli import main
from twon20.errors import SetupError
from twon20.files import Output
from twon20.init import MANIFEST, PUBLIC_SOURCE, REQUIRED_ASSETS, init_project, publish_staged, template_assets


class InitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.destination = self.parent / "new strategy"
        self.library = self.parent / "library"
        self.template = self.library / "templates" / "hyperliquid-python"
        self.template.mkdir(parents=True)
        for name in REQUIRED_ASSETS:
            (self.template / name).write_text("# Disposable public template fixture: " + name + "\n")
        (self.template / "tests").mkdir()
        (self.template / "tests" / "test_runner.py").write_text("# Disposable public test source\n")
        (self.template / ".gitignore").write_text("*.key\n.venv/\n")
        source = patch("twon20.init.files", return_value=self.library)
        source.start()
        self.addCleanup(source.stop)

    def test_init_writes_complete_versioned_public_project_with_private_permissions(self):
        with patch("subprocess.run", side_effect=AssertionError("Init must not install or execute")), patch("eth_account.Account.create", side_effect=AssertionError("Init must not create keys")):
            result = init_project(self.destination)
        self.assertEqual(result["templateVersion"], __version__)
        self.assertFalse(result["keysCreated"])
        self.assertFalse(result["dependenciesInstalled"])
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)
        manifest = json.loads((self.destination / MANIFEST).read_text())
        self.assertEqual((manifest["template"], manifest["version"], manifest["source"]), ("hyperliquid-python", __version__, PUBLIC_SOURCE))
        self.assertEqual(set(manifest["files"]), set(result["files"]) - {MANIFEST})
        for name, digest in manifest["files"].items():
            generated = self.destination / name
            self.assertEqual(generated.read_bytes(), (self.template / name).read_bytes())
            self.assertEqual(hashlib.sha256(generated.read_bytes()).hexdigest(), digest)
            self.assertEqual(stat.S_IMODE(generated.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.destination / "tests").stat().st_mode), 0o700)
        self.assertIn("'<official vault page URL>'", result["commands"]["onboard"])
        self.assertEqual(result["commands"]["paper"], ".venv/bin/python runner.py")
        self.assertIn("--require-hashes", result["commands"]["installReviewedRunner"])
        self.assertFalse(list(self.destination.rglob("*.key")))

    def test_existing_empty_directory_file_and_symlink_are_never_replaced(self):
        self.destination.mkdir()
        with self.assertRaises(SetupError) as caught:
            init_project(self.destination)
        self.assertEqual(caught.exception.code, "PROJECT_EXISTS")
        self.assertEqual(list(self.destination.iterdir()), [])
        self.destination.rmdir()
        self.destination.write_text("public marker")
        with self.assertRaises(SetupError):
            init_project(self.destination)
        self.assertEqual(self.destination.read_text(), "public marker")
        self.destination.unlink()
        self.destination.symlink_to(self.template, target_is_directory=True)
        with self.assertRaises(SetupError):
            init_project(self.destination)
        self.assertTrue(self.destination.is_symlink())

    def test_missing_parent_and_parent_symlink_are_rejected_without_creation(self):
        with self.assertRaises(OSError):
            init_project(self.parent / "missing" / "project")
        alias = self.parent / "alias"
        alias.symlink_to(self.template, target_is_directory=True)
        with self.assertRaises(OSError):
            init_project(alias / "project")
        self.assertFalse((self.template / "project").exists())
        self.assertFalse(list(self.parent.glob(".2n20-init-*")))

    def test_failed_copy_and_interruption_remove_only_the_staged_project(self):
        for failure in (OSError("Disposable write failure"), KeyboardInterrupt()):
            with patch.object(Output, "write", side_effect=failure), self.assertRaises(type(failure)):
                init_project(self.destination)
            self.assertFalse(self.destination.exists())
            self.assertFalse(list(self.parent.glob(".2n20-init-*")))

    def test_failed_publication_cleans_staged_without_destination_reservation(self):
        with patch("twon20.init.publish_staged", side_effect=OSError("Disposable publication failure")), self.assertRaises(OSError):
            init_project(self.destination)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.parent.glob(".2n20-init-*")))

    def test_native_publication_refuses_last_moment_empty_destination(self):
        original = publish_staged
        def raced(parent, staged, destination):
            self.destination.mkdir()
            return original(parent, staged, destination)
        with patch("twon20.init.publish_staged", side_effect=raced), self.assertRaises(SetupError) as caught:
            init_project(self.destination)
        self.assertEqual(caught.exception.code, "PROJECT_EXISTS")
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(list(self.parent.glob(".2n20-init-*")))

    def test_shared_nonsticky_parent_and_unsupported_publication_fail_closed(self):
        self.parent.chmod(0o777)
        with self.assertRaises(SetupError) as caught:
            init_project(self.destination)
        self.assertEqual(caught.exception.code, "UNSAFE_PROJECT_PARENT")
        self.parent.chmod(0o700)
        with patch("twon20.init.sys.platform", "unsupported"), self.assertRaises(SetupError) as caught:
            init_project(self.destination)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_FILESYSTEM")
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.parent.glob(".2n20-init-*")))

    def test_destination_created_during_generation_is_preserved_even_when_empty(self):
        assets = template_assets("hyperliquid-python")
        def appeared(template):
            self.destination.mkdir()
            return assets
        with patch("twon20.init.template_assets", side_effect=appeared), self.assertRaises(SetupError) as caught:
            init_project(self.destination)
        self.assertEqual(caught.exception.code, "PROJECT_EXISTS")
        self.assertTrue(self.destination.is_dir())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(list(self.parent.glob(".2n20-init-*")))

    def test_invalid_missing_and_symlink_template_assets_fail_before_publication(self):
        with self.assertRaises(SetupError) as caught:
            init_project(self.destination, "unsupported")
        self.assertEqual(caught.exception.code, "UNSUPPORTED_TEMPLATE")
        (self.template / "runner.py").unlink()
        with self.assertRaises(SetupError) as caught:
            init_project(self.destination)
        self.assertEqual(caught.exception.code, "TEMPLATE_UNAVAILABLE")
        (self.template / "runner.py").symlink_to(self.template / "strategy.py")
        with self.assertRaises(SetupError) as caught:
            init_project(self.destination)
        self.assertEqual(caught.exception.code, "UNSAFE_TEMPLATE")
        self.assertFalse(self.destination.exists())

    def test_cli_json_and_readable_output_are_public_and_do_not_execute_runner(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(["init", str(self.destination), "--template", "hyperliquid-python", "--json"]), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["schema"], "2n20-project-init-v1")
        self.assertEqual(stderr.getvalue(), "")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["init", str(self.parent / "other"), "--template", "hyperliquid-python"]), 0)
        self.assertIn("python3 -m venv .venv", stdout.getvalue())
        self.assertIn("runner.py", stdout.getvalue())
        self.assertNotIn("--execute LIVE_SMOKE", stdout.getvalue())

    def test_bundled_cache_files_are_not_copied(self):
        cache = self.template / "__pycache__"
        cache.mkdir()
        (cache / "runner.cpython-312.pyc").write_bytes(b"disposable cache")
        result = init_project(self.destination)
        self.assertFalse((self.destination / "__pycache__").exists())
        self.assertFalse(any(name.endswith(".pyc") for name in result["files"]))

    def test_cli_json_failure_and_interruption_preserve_machine_readable_result(self):
        self.destination.mkdir()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(["init", str(self.destination), "--template", "hyperliquid-python", "--json"]), 1)
        result = json.loads(stdout.getvalue())
        self.assertFalse(result["initialized"])
        self.assertEqual(result["errorCode"], "PROJECT_EXISTS")
        self.assertEqual(stderr.getvalue(), "")
        self.destination.rmdir()
        stdout = io.StringIO()
        with patch.object(Output, "write", side_effect=KeyboardInterrupt()), contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["init", str(self.destination), "--template", "hyperliquid-python", "--json"]), 130)
        self.assertEqual(json.loads(stdout.getvalue())["errorCode"], "INIT_INTERRUPTED")
        self.assertFalse(self.destination.exists())
