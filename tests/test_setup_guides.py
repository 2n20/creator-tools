import hashlib
from importlib.resources import files
from pathlib import Path
import tempfile
import unittest

from twon20.errors import SetupError
from twon20.skill_export import export_skill, install_skills


class SetupGuideTests(unittest.TestCase):
    def test_preserves_reviewed_connection_only_instructions(self):
        expected = {
            "step-2.md": "6d9023624b655af964e877ccca1d8506856723b7bf2a26d2b5efaefba1d6f5b3",
            "step-3.md": "f7d751427659e334aa228b28974392ece1c15c020cfe862945fc5a7f30b95b11",
        }
        root = files("twon20").joinpath("skills", "2n20-setup")
        skill = root.joinpath("SKILL.md").read_text()
        for name, digest in expected.items():
            with self.subTest(step=name):
                self.assertIn(f"(references/{name})", skill)
                guide = root.joinpath("references", name).read_bytes()
                self.assertEqual(hashlib.sha256(guide).hexdigest(), digest)
                self.assertIn(b"<official vault page URL>", guide)

    def test_step_two_waits_for_the_copied_step_three_request(self):
        skill = files("twon20").joinpath("skills", "2n20-setup", "SKILL.md").read_text()
        step_two = skill.split("### Step 2: Approve trading access\n", 1)[1].split("### Step 3:", 1)[0]
        self.assertIn("Present that link and pause", step_two)
        self.assertIn("copy its connection instructions into this same conversation", step_two)
        self.assertIn("Wait for the Step 3 request before configuring the strategy or reporting completion", step_two)
        self.assertNotIn("After the creator's wallet approval, read the Step 3 guide and continue", skill)
        self.assertIn("After the creator supplies the website's Step 3 request, verify the outcome", skill)

    def test_connection_only_setup_and_optional_live_test_have_separate_boundaries(self):
        root = files("twon20").joinpath("skills", "2n20-setup")
        step_two = root.joinpath("references", "step-2.md").read_text()
        step_three = root.joinpath("references", "step-3.md").read_text()
        skill = root.joinpath("SKILL.md").read_text()
        self.assertIn("Wait for that copied Step 3 request", step_two)
        self.assertNotIn("complete-setup", step_two)
        self.assertNotIn("paper checks", step_two)
        self.assertIn("Do not require paper trading", step_three)
        self.assertIn("--connection-checked --json", step_three)
        self.assertIn("wait for explicit approval of that plan", step_three)
        self.assertNotIn("--paper-checked", skill + step_three)
        self.assertIn("no size or price decimals belong in user configuration", skill)
        self.assertIn("configured and connected", skill)
        self.assertIn("not a successful live test", skill)

    def test_both_steps_and_development_preserve_native_questions(self):
        root = files("twon20").joinpath("skills")
        texts = [root.joinpath("2n20-setup", "references", name).read_text()
                 for name in ("step-2.md", "step-3.md")]
        texts.append(root.joinpath("2n20-strategy", "SKILL.md").read_text())
        questions = [next(line for line in text.splitlines() if line.startswith("For missing details,"))
                     for text in texts]
        self.assertEqual(len(set(questions)), 1)
        for phrase in ("two or three relevant choices", "Allow a custom answer", "Ask for paths and SSH destinations as text",
                       "If question controls are unavailable", "Wait for my answer", "A default selection is not consent"):
            self.assertIn(phrase, questions[0])

    def test_export_and_all_project_installs_keep_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            export_skill(root / "export")
            for agent, folder in (("codex", ".agents"), ("cursor", ".cursor"), ("claude", ".claude")):
                install_skills(agent, root)
                target = root / folder / "skills/2n20-setup"
                for name in ("step-2.md", "step-3.md"):
                    self.assertEqual((target / "references" / name).read_bytes(),
                                     (root / "export/references" / name).read_bytes())
                (target / "references/step-3.md").write_text("creator customization")
                with self.assertRaises(SetupError) as caught:
                    install_skills(agent, root)
                self.assertEqual(caught.exception.code, "SKILL_CONFLICT")
                self.assertEqual((target / "references/step-3.md").read_text(), "creator customization")
