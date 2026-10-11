import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from twon20 import __version__
from twon20.cli import main
from twon20.errors import SetupError
from twon20.skill_export import export_skill, install_skills


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_agent_directories_idempotence_and_unrelated_files(self):
        for agent, folder in (("codex", ".agents"), ("cursor", ".cursor"), ("claude", ".claude")):
            with self.subTest(agent=agent):
                unrelated = self.root / folder / "skills" / "custom"
                unrelated.mkdir(parents=True)
                (unrelated / "SKILL.md").write_text("custom")
                result = install_skills(agent, self.root)
                self.assertEqual(result["version"], __version__)
                self.assertEqual(len(result["files"]), 4)
                for path in result["files"]:
                    path = Path(path)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                    if path.name == "SKILL.md":
                        self.assertIn(f"name: {path.parent.name}", path.read_text())
                again = install_skills(agent, self.root)
                self.assertEqual([item["status"] for item in again["skills"]], ["unchanged", "unchanged"])
                self.assertEqual((unrelated / "SKILL.md").read_text(), "custom")

    def test_conflict_preflight_preserves_existing_skills(self):
        result = install_skills("codex", self.root, "2n20-strategy")
        skill = Path(result["files"][0])
        skill.write_text("user modification")
        with self.assertRaises(SetupError) as caught:
            install_skills("codex", self.root)
        self.assertEqual(caught.exception.code, "SKILL_CONFLICT")
        self.assertEqual(skill.read_text(), "user modification")
        self.assertFalse((skill.parent.parent / "2n20-setup").exists())

    def test_extra_file_conflicts_without_overwriting(self):
        result = install_skills("cursor", self.root, "2n20-setup")
        extra = Path(result["files"][0]).parent / "custom.md"
        extra.write_text("keep")
        with self.assertRaises(SetupError):
            install_skills("cursor", self.root)
        self.assertEqual(extra.read_text(), "keep")

    def test_symlinks_at_each_destination_boundary_are_refused(self):
        for index, relative in enumerate((".agents", ".agents/skills", ".agents/skills/2n20-setup", ".agents/skills/2n20-setup/SKILL.md")):
            project = self.root / str(index)
            project.mkdir()
            target = self.root / f"target-{index}"
            target.mkdir()
            link = project / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises((SetupError, OSError)):
                install_skills("codex", project)
            self.assertEqual(list(target.iterdir()), [])

    def test_hermes_home_is_mocked_and_project_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True), patch("twon20.skill_export.Path.home", return_value=self.root):
            result = install_skills("hermes", name="2n20-strategy")
        self.assertEqual(result["files"], [str(self.root / ".hermes/skills/2n20-strategy/SKILL.md")])
        with patch.dict(os.environ, {"HERMES_HOME": str(self.root / "profile")}):
            result = install_skills("hermes")
        self.assertTrue(all(path.startswith(str(self.root / "profile/skills")) for path in result["files"]))
        with self.assertRaises(SetupError) as caught:
            install_skills("hermes", self.root)
        self.assertEqual(caught.exception.code, "HERMES_PROJECT_UNSUPPORTED")

    def test_cli_install_defaults_to_cwd_and_has_no_discovery(self):
        output = io.StringIO()
        with patch("twon20.skill_export.Path.cwd", return_value=self.root), patch("twon20.cli.discover", side_effect=AssertionError("network discovery")), contextlib.redirect_stdout(output):
            code = main(["skill", "install", "--agent", "codex", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(output.getvalue())["files"]), 4)

    def test_cli_named_export_and_install(self):
        for args in (["skill", "export", "--name", "2n20-strategy", "--directory", str(self.root / "export"), "--json"],
                     ["skill", "install", "--agent", "claude", "--project", str(self.root), "--name", "2n20-setup", "--json"]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(args), 0)
            self.assertEqual(len(json.loads(output.getvalue())["files"]), 1 if args[1] == "export" else 3)

    def test_nested_reference_assets_export_and_install(self):
        bundle = {"SKILL.md": b"skill", "references/guide.md": b"reference"}
        with patch("twon20.skill_export._contents", return_value=bundle):
            result = export_skill(self.root / "portable")
            self.assertEqual((self.root / "portable/references/guide.md").read_bytes(), b"reference")
            self.assertEqual(len(result["files"]), 2)
            install_skills("codex", self.root, "2n20-setup")
            result = install_skills("codex", self.root, "2n20-setup")
            self.assertEqual(result["skills"][0]["status"], "unchanged")

    def test_invalid_names_do_not_create_agent_directory(self):
        with self.assertRaises(SetupError):
            install_skills("codex", self.root, "../outside")
        self.assertFalse((self.root / ".agents").exists())
