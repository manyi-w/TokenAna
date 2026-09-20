"""Verify legacy patch selection through a simulated workspace."""

from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from src.components import load_component
from src.interfaces import AgentResult, Workspace
from src.loading import load_adapter


class CollectionTests(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).resolve().parents[1] / "datasets/swe_bench_verified"
        self.dataset = load_adapter(load_component(directory, "dataset", {}))
        self.workspace = Mock(spec=Workspace)

    def collect(self, diff, output, error=None):
        self.workspace.execute.return_value = subprocess.CompletedProcess(
            ["git", "diff"], 0, diff, "")
        trace = AgentResult("fake", "prompt", output, 12, 1, 0.5, [], error)
        return self.dataset.collect(self.workspace, trace)

    def test_git_diff_wins_and_agent_error_is_preserved(self):
        for error in (None, "Timeout"):
            with self.subTest(error=error):
                result = self.collect("\n diff --git a/file b/file\n", "--- textual patch", error)
                self.assertEqual(result.patch, "diff --git a/file b/file")
                self.assertEqual(result.success, error is None)
                self.assertEqual(result.error, error or "")
        self.workspace.execute.assert_called_with(["git", "diff"])

    def test_text_fallback_preserves_original_marker_and_trailing_text_rules(self):
        for marker in ("diff --git a/x b/x", "--- a/x", "+++ b/x"):
            with self.subTest(marker=marker):
                body = marker + "\n@@ -1 +1 @@\n-old\n+new\n```\ntrailing explanation"
                result = self.collect(" \n", "intro\n```diff\n" + body + "\n")
                self.assertEqual(result.patch, body)
                self.assertTrue(result.success)

    def test_prose_empty_output_and_indented_markers_are_not_patches(self):
        for output in ("", "I fixed the bug", "  diff --git a/x b/x\nnot a marker"):
            with self.subTest(output=output):
                result = self.collect("", output)
                self.assertEqual(result.patch, "")
                self.assertFalse(result.success)
        result = self.collect("", "--- a/x\n+++ b/x", "agent error")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "agent error")

    def test_git_failure_and_timeout_do_not_become_text_fallback(self):
        trace = AgentResult("fake", "", "--- fallback", 0, 0, 0, [])
        self.workspace.execute.return_value = subprocess.CompletedProcess(
            ["git", "diff"], 128, "", "not a git repository")
        with self.assertRaises(subprocess.CalledProcessError):
            self.dataset.collect(self.workspace, trace)
        self.workspace.execute.side_effect = subprocess.TimeoutExpired("git", 10)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.dataset.collect(self.workspace, trace)
