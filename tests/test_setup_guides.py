import hashlib
from importlib.resources import files
from pathlib import Path
import tempfile
import unittest

from twon20.errors import SetupError
from twon20.skill_export import export_skill, install_skills


class SetupGuideTests(unittest.TestCase):
    def test_preserves_approved_website_instructions(self):
        expected = {
            "step-2.md": "e98aed0db5f8a29eb5d9ce28adf0d849142bf4087bcdb5a6fe663d47d1652421",
            "step-3.md": "6e93b64a21463b94a201785478d025f9e7a8907ea3c63a21b271044446df87e1",
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
        self.assertIn("perform that work only after the creator supplies the Step 3 request", step_two)
        self.assertNotIn("After the creator's wallet approval, read the Step 3 guide and continue", skill)
        self.assertIn("After the creator supplies the website's Step 3 request, verify the outcome", skill)

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
