"""Offline tests of selection and isolation; no upstream code is imported."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.components import ConfigError
from src.config import load_experiment


PROJECT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / "experiments/example.toml"
        self.config.parent.mkdir()
        self.sections = {}
        for kind in ("method", "agent", "dataset"):
            directory = self.root / f"{kind}s/selected"
            directory.mkdir(parents=True)
            (directory / "manifest.toml").write_text(
                f'kind = "{kind}"\nname = "selected_{kind}"\n', encoding="utf-8"
            )
            (directory / "adapter.py").write_text(
                'raise RuntimeError("configuration must not import adapters")\n', encoding="utf-8"
            )
            self.sections[kind] = f'[{kind}]\npath = "../{kind}s/selected"\n'
        self.write_config()

    def write_config(self, suffix=""):
        self.config.write_text("\n".join(self.sections.values()) + suffix, encoding="utf-8")

    def test_repository_example_selects_existing_components(self):
        result = load_experiment(PROJECT / "experiments/run_free_codex_verified.toml")
        self.assertEqual(
            (result.method.name, result.agent.name, result.dataset.name, result.dataset.options),
            ("run_free", "codex", "swe_bench_verified", {"limit": 10}),
        )

    def test_component_options_are_passed_without_interpretation(self):
        self.write_config('\n[method.options]\nbudget = 2\n[dataset.options]\nids = ["x", "y"]\n')
        result = load_experiment(self.config)
        self.assertEqual(result.method.options, {"budget": 2})
        self.assertEqual(result.agent.options, {})
        self.assertEqual(result.dataset.options, {"ids": ["x", "y"]})
        self.assertEqual(result.method.path, self.root / "methods/selected")

    def test_unselected_broken_method_does_not_affect_selection(self):
        before = load_experiment(self.config)
        broken = self.root / "methods/broken"
        broken.mkdir()
        (broken / "manifest.toml").write_text("not valid toml [", encoding="utf-8")
        (broken / "adapter.py").write_text("not valid python !", encoding="utf-8")
        (broken / "requirements.txt").write_text("missing-dependency==invalid", encoding="utf-8")
        self.assertEqual(load_experiment(self.config), before)

    def test_invalid_experiment_fields(self):
        cases = (
            ("", "must be a table"),
            ('method = "selected"\n', "must be a table"),
            ('[method]\n', "method.path"),
            ('[method]\npath = 1\n', "method.path"),
            ('[method]\npath = " "\n', "method.path"),
            ('[method]\npath = "../methods/selected"\noptions = 1\n', "method.options"),
            ('[method]\npath = "../methods/selected"\noptinos = {}\n', "unknown method fields"),
            ('[unrecognized]\n', "unknown sections"),
            ('[method', "Cannot read"),
        )
        for body, message in cases:
            with self.subTest(body=body):
                self.config.write_text(body, encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, message):
                    load_experiment(self.config)

    def test_invalid_selected_manifest(self):
        manifest = self.root / "methods/selected/manifest.toml"
        for body, message in (
            ('kind = "agent"\nname = "x"', "kind must be"),
            ('kind = "method"\nname = " "', "name must be"),
            ('kind = "method"\nname = 1', "name must be"),
            ('[', "Cannot read"),
        ):
            with self.subTest(body=body):
                manifest.write_text(body, encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, message):
                    load_experiment(self.config)

    def test_missing_selected_manifest_and_config(self):
        (self.root / "methods/selected/manifest.toml").unlink()
        with self.assertRaisesRegex(ConfigError, "manifest.toml"):
            load_experiment(self.config)
        with self.assertRaisesRegex(ConfigError, "missing.toml"):
            load_experiment(self.root / "missing.toml")

    def test_cli_works_outside_project_and_reports_configuration_errors(self):
        command = [sys.executable, "-B", "-m", "tokenAna", "config", str(self.config)]
        environment = {**os.environ, "PYTHONPATH": str(PROJECT)}
        result = subprocess.run(command, cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(json.loads(result.stdout)["method"]["path"], str(self.root / "methods/selected"))
        self.config.write_text("[", encoding="utf-8")
        result = subprocess.run(command, cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
