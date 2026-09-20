"""Simulate Docker process calls; no daemon or running container is needed."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.interfaces import ArtifactDirectory
from src.workspaces import DockerWorkspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.host = Path(temporary.name)
        self.prefix = ("/opt/conda/bin/conda", "run", "--no-capture-output", "-n", "testbed")
        self.workspace = DockerWorkspace("prepared-container", "/repo with spaces",
                                         ArtifactDirectory(self.host, "/artifacts"), self.prefix)

    def test_launch_only_builds_argv_and_preserves_arguments(self):
        argv = ["tool", "with spaces", "$(not-a-command)", "'quoted'"]
        with patch("src.workspaces.subprocess.run") as process:
            command = self.workspace.launch_command(argv)
        process.assert_not_called()
        self.assertEqual(command, ["docker", "exec", "-i", "--workdir", "/repo with spaces",
                                   "prepared-container", *self.prefix, *argv])

    def test_execute_preserves_nonzero_exit_and_timeout(self):
        failure = subprocess.CompletedProcess([], 7, "output", "failure")
        with patch("src.workspaces.subprocess.run", return_value=failure) as process:
            self.assertIs(self.workspace.execute(["check"], timeout=9), failure)
        self.assertEqual(process.call_args.kwargs["timeout"], 9)
        self.assertEqual(process.call_args.args[0], self.workspace.launch_command(["check"]))
        with patch("src.workspaces.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("docker", 9)) as process:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.workspace.execute(["check"], timeout=9)
            process.assert_called_once()

    def test_file_content_is_separate_from_shell_and_errors_propagate(self):
        content = "Unicode λ\n$(touch unwanted) 'quote'\n"
        path = "directory/file ' with spaces.txt"
        with patch("src.workspaces.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, content, "")) as process:
            self.assertEqual(self.workspace.read_text(path), content)
            self.assertEqual(process.call_args.args[0][-3:],
                             ["cat", "--", "/repo with spaces/" + path])
            self.workspace.write_text(path, content)
            self.assertEqual(process.call_args.kwargs["input"], content)
            self.assertNotIn(content, process.call_args.args[0])
            self.assertEqual(process.call_args.args[0][-1], "/repo with spaces/" + path)
            self.assertTrue(process.call_args.kwargs["check"])
        with patch("src.workspaces.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 1, "", "missing")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.workspace.read_text("missing.txt")
        with patch("src.workspaces.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "docker")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.workspace.write_text("missing/file", content)

    def test_file_paths_are_repository_relative(self):
        with patch("src.workspaces.subprocess.run") as process:
            for path in ("/etc/passwd", "../outside", "nested/../../outside"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.workspace.read_text(path)
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.workspace.write_text(path, "content")
            process.assert_not_called()

    def test_artifacts_are_fresh_and_share_the_mount_relative_path(self):
        first = self.workspace.new_artifacts()
        (first.host / "trace.jsonl").write_text("previous trace")
        second = self.workspace.new_artifacts()
        self.assertNotEqual(first.host, second.host)
        self.assertEqual(first.host.parent, self.host.resolve())
        self.assertTrue(second.host.is_dir())
        self.assertEqual(second.execution, "/artifacts/" + second.host.name)
        self.assertFalse((second.host / "trace.jsonl").exists())
        self.assertEqual((first.host / "trace.jsonl").read_text(), "previous trace")
