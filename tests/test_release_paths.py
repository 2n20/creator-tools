import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("release_paths", Path(__file__).resolve().parents[1] / "scripts/build_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleasePathTests(unittest.TestCase):
    def test_generated_state_and_credentials_cannot_ship(self):
        for path in ("twon20/templates/hyperliquid-python/runtime.public.json",
                     "twon20/templates/hyperliquid-python/.runtime-paper-public/report.json",
                     "twon20/.runner-state/lock", "twon20/setup/strategy.key",
                     "twon20/.env.local", "twon20/notes.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                release.validate_names([path])

    def test_public_template_and_guides_remain_distributable(self):
        release.validate_names(["twon20/templates/hyperliquid-python/config.public.example.json",
                                "twon20/templates/hyperliquid-python/runtime.py",
                                "twon20/skills/2n20-strategy/SKILL.md"])
