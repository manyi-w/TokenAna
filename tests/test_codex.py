"""Exercise the inherited caller using fixture traces and simulated processes."""

import json
from contextlib import nullcontext
from pathlib import Path
from shlex import quote
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agents.codex.adapter import Codex
from src.attribution import codex_attribution
from src.agents import BoundAgent
from src.interfaces import ArtifactDirectory, Workspace


class CodexTests(unittest.TestCase):
    def test_raw_usage_route_preserves_original_caller(self):
        options = {**self.options, "record_raw_usage": True,
                   "model_base_url": "http://fake-upstream/v1", "model_api_key_env": "TEST_KEY"}
        with patch("src.usage_proxy.recording_proxy", return_value=nullcontext("http://127.0.0.1:12345/v1")) as proxy:
            with patch("agents.codex.compatibility.agent_caller.subprocess.run",
                       return_value=subprocess.CompletedProcess([], 0, "original output", "")):
                result = Codex().run("12345678", self.workspace, options)
        proxy.assert_called_once_with(self.output / "api-records", "http://fake-upstream/v1", timeout=60,
                                      protocol="responses", provider=None, attribution={}, attribution_resolver=codex_attribution)
        command = self.workspace.launch_command.call_args.args[0][2]
        self.assertIn("127.0.0.1:12345", command)
        self.assertIn("supports_websockets = false", command)
        self.assertIn("TEST_KEY", command)
        self.assertEqual((result.output, result.tokens_used), ("original output", 2))
        self.assertEqual(options["model_base_url"], "http://fake-upstream/v1")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.workspace = Mock(spec=Workspace)
        self.workspace.new_artifacts.return_value = ArtifactDirectory(
            self.output, "/output with spaces/call-1")
        self.workspace.launch_command.return_value = ["fake-workspace-launch"]
        self.options = {"executable": "/opt/source build/codex", "timeout": 17}
        self.agent = BoundAgent(Codex(), self.options)

    def test_fixture_statistics_and_workspace_launch(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] /
                              "agents/codex/compatibility/fixtures/codex_trace.json").read_text())
        prompt = "Preserve 'quotes' and $(text)\nUnicode λ\n"

        def simulate_process(*args, **kwargs):
            self.assertEqual((self.output / "prompt.txt").read_text(), prompt)
            (self.output / "trace.jsonl").write_text(
                "\n".join(json.dumps(item) for item in fixture["raw_trace"]))
            return subprocess.CompletedProcess(args[0], 0, "ignored stdout", "")

        with patch("agents.codex.compatibility.agent_caller.subprocess.run",
                   side_effect=simulate_process) as process:
            result = self.agent.run(prompt, self.workspace, {})
        self.assertEqual(result.output, fixture["output"])
        self.assertEqual(result.tokens_used, fixture["tokens_used"])
        self.assertEqual(result.exec_count, fixture["exec_count"])
        self.assertEqual(result.raw_trace, fixture["raw_trace"])
        self.assertIsNone(result.error)
        self.assertEqual(result.prompt, prompt)
        self.assertEqual(result.artifacts.host, self.output)
        process.assert_called_once()
        self.assertEqual(process.call_args.args[0], ["fake-workspace-launch"])
        self.assertEqual(process.call_args.kwargs["timeout"], 17)
        self.workspace.launch_command.assert_called_once()
        argv = self.workspace.launch_command.call_args.args[0]
        self.assertEqual(argv[:2], ["bash", "-c"])
        self.assertIn(quote(self.options["executable"]) + " exec", argv[2])
        self.assertIn(quote("/output with spaces/call-1/trace.jsonl"), argv[2])
        self.assertNotIn(prompt, argv[2])
        self.workspace.execute.assert_not_called()

    def test_model_provider_overrides_are_explicit_and_shell_quoted(self):
        options = {**self.options, "model": "fake-model", "model_provider": "fake-provider",
                   "model_base_url": "http://host.docker.internal:8000/v1", "wire_api": "responses"}
        agent = BoundAgent(Codex(), options)
        with patch("agents.codex.compatibility.agent_caller.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "", "")):
            agent.run("prompt", self.workspace, {})
        command = self.workspace.launch_command.call_args.args[0][2]
        self.assertIn("model_providers.fake-provider", command)
        self.assertIn("http://host.docker.internal:8000/v1", command)
        self.assertIn('model_provider=\"fake-provider\"', command)
        self.assertIn('model=\"fake-model\"', command)

    def test_original_fallback_and_errors(self):
        for returncode, stderr, error in (
            (0, "needs_follow_up: false", None),
            (3, "", "Non-zero exit code: 3"),
            (0, "real error", "real error"),
        ):
            with self.subTest(returncode=returncode, stderr=stderr):
                (self.output / "trace.jsonl").write_text("")
                with patch("agents.codex.compatibility.agent_caller.subprocess.run",
                           return_value=subprocess.CompletedProcess([], returncode, " fallback ", stderr)):
                    result = self.agent.run("12345678", self.workspace, {})
                self.assertEqual((result.output, result.tokens_used, result.exec_count),
                                 ("fallback", 2, 0))
                self.assertEqual(result.error, error)

    def test_original_timeout_and_per_call_override(self):
        with patch("agents.codex.compatibility.agent_caller.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("fake", 3)) as process:
            result = self.agent.run("prompt", self.workspace, {"timeout": 3})
        self.assertEqual((result.error, result.duration_sec, result.tokens_used, result.raw_trace),
                         ("Timeout", 3, 0, []))
        self.assertEqual(process.call_args.kwargs["timeout"], 3)
        self.assertEqual(self.options["timeout"], 17)
        self.assertEqual(result.artifacts.host, self.output)
        process.assert_called_once()
