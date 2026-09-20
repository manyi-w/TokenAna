"""Dry-run CLI tests without containers, model calls or filesystem outputs."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.cli import main


PROJECT = Path(__file__).resolve().parents[1]


class PlanningTests(unittest.TestCase):
    def invoke(self, args):
        output = io.StringIO()
        with patch("sys.argv", ["tokenAna", *args]), redirect_stdout(output):
            code = main()
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_ten_tasks_without_execution_or_output_files(self):
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.run") as process:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                result = self.invoke(["run", str(PROJECT / "experiments/run_free_codex_verified.toml"),
                                      "--dry-run"])
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                os.chdir(previous)
        process.assert_not_called()
        self.assertEqual(result["task_count"], 10)
        self.assertEqual(result["tasks"][0]["instance_id"], "astropy__astropy-12907")
        self.assertEqual(result["status"], "plan_only")
        self.assertFalse(result["runtime_ready"])
        self.assertEqual(result["agent"]["missing_options"], ["agent.options.executable"])
        self.assertEqual(result["dataset"]["submission_template"][:4],
                         ["sb-cli", "submit", "swe-bench_verified", "test"])
        self.assertNotIn("patch", result["tasks"][0])

    def test_real_run_without_flag_is_rejected(self):
        with patch("sys.argv", ["tokenAna", "run", "unused.toml"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)

    def test_only_selected_component_directories_are_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sections = []
            for kind in ("method", "agent", "dataset"):
                component = root / kind
                component.mkdir()
                (component / "manifest.toml").write_text(
                    f'kind="{kind}"\nname="custom"\nentrypoint="Adapter"\n')
                (component / "adapter.py").write_text(
                    'class Adapter:\n'
                    '    def tasks(self, **options): return []\n'
                    '    def plan(self, options): return {"custom": True}\n'
                    '    def run(self, *args): raise AssertionError("must not execute")\n')
                sections.append(f'[{kind}]\npath="{kind}"\n')
            broken = root / "broken"
            broken.mkdir()
            (broken / "adapter.py").write_text("invalid python !")
            (broken / "manifest.toml").write_text("invalid toml [")
            config = root / "experiment.toml"
            config.write_text("\n".join(sections))
            result = self.invoke(["run", str(config), "--dry-run"])
        self.assertEqual(result["task_count"], 0)
        self.assertEqual(result["dataset"], {"custom": True})
