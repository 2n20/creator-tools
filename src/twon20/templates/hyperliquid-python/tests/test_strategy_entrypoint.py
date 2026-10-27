"""A creator replaces only the strategy and its public parameters."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import twon20
from configuration import configured_strategy, load_config
from executor import execution_state, plan_child
from risk import Stop
from test_runtime import intent, snapshot

ROOT = Path(__file__).resolve().parents[1]
CUSTOM = '''from decimal import Decimal
from models import Target

class Threshold:
    warmup_bars = 1
    def __init__(self, threshold):
        self.threshold = Decimal(threshold)
    def evaluate(self, bars, checkpoint):
        count = checkpoint.get("evaluations", 0) + 1
        fraction = Decimal(1) if bars[-1].close > self.threshold else Decimal(0)
        return Target(fraction, "custom_threshold"), {"evaluations": count}

def build_strategy(parameters):
    if set(parameters) != {"threshold"}:
        raise ValueError("threshold required")
    return Threshold(parameters.pop("threshold"))
'''


class StrategyEntrypointTests(unittest.TestCase):
    def test_factory_cannot_mutate_nested_public_configuration(self):
        parameters = {"nested": {"window": 3}}
        def factory(value):
            value["nested"].pop("window")
            return SimpleNamespace(warmup_bars=1, evaluate=lambda bars, checkpoint: (None, checkpoint))
        with patch("configuration.build_strategy", side_effect=factory):
            configured_strategy(parameters)
            configured_strategy(parameters)
        self.assertEqual(parameters, {"nested": {"window": 3}})

    def test_invalid_factory_contract_fails_during_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom.public.json"
            path.write_text((ROOT / "config.public.example.json").read_text())
            for instance in (None, SimpleNamespace(warmup_bars=1),
                             SimpleNamespace(warmup_bars=True, evaluate=lambda *_: None),
                             SimpleNamespace(warmup_bars=129, evaluate=lambda *_: None)):
                with patch("configuration.build_strategy", return_value=instance), self.assertRaises(Stop) as error:
                    load_config(path)
                self.assertEqual(error.exception.code, "STRATEGY_INTERFACE_INVALID")

    def test_replacing_only_strategy_and_parameters_runs_and_resumes_without_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            for source in ROOT.glob("*.py"):
                shutil.copyfile(source, project / source.name)
            shutil.copyfile(ROOT / "fixture.candles.public.json", project / "fixture.candles.public.json")
            (project / "strategy.py").write_text(CUSTOM)
            settings = load_config(ROOT / "config.public.example.json")
            settings["strategy"] = {"threshold": "1"}
            (project / "custom.public.json").write_text(json.dumps(settings))
            command = [sys.executable, "runner.py", "run", "--mode", "paper", "--feed", "fixture",
                       "--config", "custom.public.json", "--state-directory", ".runtime-custom", "--max-bars", "2"]
            environment = {"PYTHONPATH": str(Path(twon20.__file__).resolve().parents[1]),
                           "PYTHONDONTWRITEBYTECODE": "1"}
            first = subprocess.run(command, cwd=project, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            state_path = project / ".runtime-custom/runtime.public.json"
            before = json.loads(state_path.read_text())
            self.assertEqual(before["strategy"]["evaluations"], 2)
            self.assertEqual(before["execution"]["active"]["reason"], "custom_threshold")
            second = subprocess.run(command, cwd=project, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            after = json.loads(state_path.read_text())
            self.assertGreater(after["strategy"]["evaluations"], before["strategy"]["evaluations"])
            self.assertGreater(after["cursor"], before["cursor"])
            self.assertEqual(after["mode"], "paper")
            self.assertFalse(list(project.rglob("*.key")))

    def test_immediate_ioc_is_explicit_and_retains_size_and_price_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom.public.json"
            settings = load_config(ROOT / "config.public.example.json")
            self.assertIsNone(settings["execution"]["iocAfterSeconds"])
            settings["execution"].update(iocAfterSeconds=0, slippageBps=20, childNotional="12")
            path.write_text(json.dumps(settings))
            limits = load_config(path)["execution"]
            plan = plan_child(intent(), execution_state(1000), snapshot(), limits, 1000)
            self.assertEqual(plan["tif"], "Ioc")
            from decimal import Decimal
            self.assertLessEqual(Decimal(plan["price"]) * Decimal(plan["size"]), Decimal("12"))
            self.assertLessEqual(Decimal(plan["price"]), Decimal(intent()["arrivalPrice"]) * Decimal("1.002"))
            for invalid in (-1, True, 1.5, limits["chaseSeconds"]):
                settings["execution"]["iocAfterSeconds"] = invalid
                path.write_text(json.dumps(settings))
                with self.assertRaises(Stop):
                    load_config(path)

    def test_default_strategy_still_rejects_unknown_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom.public.json"
            settings = load_config(ROOT / "config.public.example.json")
            settings["strategy"]["unused"] = "1"
            path.write_text(json.dumps(settings))
            with self.assertRaises(Stop) as error:
                load_config(path)
            self.assertEqual(error.exception.code, "STRATEGY_CONFIG_INVALID")
