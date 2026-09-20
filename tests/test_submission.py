"""Use the unmodified prediction generator on temporary original patch files."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.components import load_component
from src.interfaces import AgentResult, ArtifactDirectory
from src.loading import load_adapter


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        component = Path(__file__).resolve().parents[1] / "datasets/swe_bench_verified"
        self.dataset = load_adapter(load_component(component, "dataset", {}))

    def result(self, name, content, error=None):
        directory = self.root / name
        directory.mkdir()
        if content is not None:
            (directory / "patch.diff").write_text(content, encoding="utf-8")
        return AgentResult("codex", "", "--- text fallback must not be used", 0, 0, 0, [],
                           error, ArtifactDirectory(directory, "/output/" + name))

    def test_original_sorting_raw_patch_empty_missing_and_error_rules(self):
        raw = "\n diff --git a/λ b/λ\n\n"
        results = {"z-task": self.result("z", raw, "agent error"),
                   "a-task": self.result("a", ""),
                   "m-task": self.result("m", None)}
        output = io.StringIO()
        with patch("subprocess.run") as process, redirect_stdout(output):
            plan = self.dataset.prepare_submission(results, self.root / "evaluation",
                                                   agent="codex", mode="run_free")
        process.assert_not_called()
        self.assertIn("Missing patch.diff", output.getvalue())
        self.assertEqual(json.loads(plan.predictions.read_text()), [
            {"instance_id": "a-task", "model_patch": "", "model_name_or_path": "codex_run_free"},
            {"instance_id": "z-task", "model_patch": raw, "model_name_or_path": "codex_run_free"},
        ])
        self.assertEqual(plan.command, ["sb-cli", "submit", "swe-bench_verified", "test",
                                       "--predictions_path", str(plan.predictions),
                                       "--run_id", "swebenchverified_codex_run_free"])
        self.assertEqual((results["z-task"].artifacts.host / "patch.diff").read_text(), raw)

    def test_new_submission_does_not_reuse_previous_patch(self):
        first = self.result("first", "original patch")
        second = self.result("second", None)
        with redirect_stdout(io.StringIO()):
            first_plan = self.dataset.prepare_submission({"task": first}, self.root / "eval1",
                                                         agent="codex", mode="run_free")
            second_plan = self.dataset.prepare_submission({"task": second}, self.root / "eval2",
                                                          agent="codex", mode="run_free", run_id="custom")
        self.assertEqual(json.loads(second_plan.predictions.read_text()), [])
        self.assertEqual(second_plan.command[-1], "custom")
        self.assertEqual(second_plan.report_command,
                         ["sb-cli", "get-report", "swe-bench_verified", "test", "custom"])
        with self.assertRaises(FileExistsError):
            self.dataset.prepare_submission({"task": second}, self.root / "eval1",
                                            agent="codex", mode="run_free")
        self.assertEqual(json.loads(first_plan.predictions.read_text())[0]["model_patch"], "original patch")

    def test_report_preserves_counts_id_lists_and_unknown_fields(self):
        # Synthetic report, not a downloaded or completed evaluation result.
        report = {"completed_instances": 2, "resolved_instances": 1,
                  "completed_ids": ["fixed", "failed"], "resolved_ids": ["fixed"],
                  "submitted_ids": ["fixed", "failed", "pending"],
                  "error_ids": [], "extra": {"source": "synthetic"}}
        path = self.root / "report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        with patch("subprocess.run") as process:
            parsed = self.dataset.read_report(path)
        process.assert_not_called()
        self.assertEqual(parsed, report)
        self.assertNotIn("pass_rate", parsed)
        self.assertNotIn("unresolved_ids", parsed)
        self.assertNotIn("status", parsed)

    def test_missing_partial_and_invalid_reports_are_not_zero_pass_results(self):
        path = self.root / "report.json"
        with self.assertRaises(FileNotFoundError):
            self.dataset.read_report(path)
        path.write_text('{"resolved_ids": []}', encoding="utf-8")
        self.assertEqual(self.dataset.read_report(path), {"resolved_ids": []})
        for body in ('{', '[]', '{"resolved_instances": true}',
                     '{"completed_instances": -1}', '{"resolved_ids": "instance"}'):
            with self.subTest(body=body):
                path.write_text(body, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.dataset.read_report(path)
