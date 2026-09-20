"""Offline adapter tests; no upstream imports, processes or model calls."""

from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agents.mini_swe_agent.adapter import MiniSweAgent
from src.agents import BoundAgent
from src.components import ConfigError
from src.config import load_experiment
from src.interfaces import ArtifactDirectory, Workspace
from src.models import load_model, model_plan
from src.planning import plan_experiment

ROOT = Path(__file__).resolve().parents[1]


class MiniTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.workspace = Mock(spec=Workspace)
        self.workspace.root = "/testbed"
        self.workspace.new_artifacts.return_value = ArtifactDirectory(self.directory, "/out space/call")
        self.workspace.launch_command.side_effect = lambda argv: argv
        self.workspace.execute.return_value = subprocess.CompletedProcess([], 0, "diff --git a/x b/x\n", "")
        self.model = load_model({"path": "models/gpt-5.6-sol.toml", "api_key_env": "CUSTOM_KEY"}, ROOT)
        self.agent = BoundAgent(MiniSweAgent(), {"executable": "/source build/mini", "timeout": 9}, self.model)
        self.trajectory = {"info": {"exit_status": "Submitted", "submission": "done"}, "messages": [
            {"usage": {"input_tokens": 12, "output_tokens": 3},
             "extra": {"actions": [{"command": "ls"}]}},
            {"extra": {"actions": [], "response": {"usage": {"total_tokens": 7}}}},
        ]}

    def save_trace(self):
        (self.directory / "trajectory.json").write_text(json.dumps(self.trajectory))

    def test_prompt_command_statistics_and_patch(self):
        prompt = "quotes ' and $(literal)\nλ\n"
        def simulate(argv, **kwargs):
            config = json.loads((self.directory / "config.yaml").read_text())
            self.assertEqual(config["run"]["task"], prompt)
            self.assertEqual(config["environment"]["cwd"], "/testbed")
            self.assertNotIn("api_key", config["model"]["model_kwargs"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.save_trace()
            kwargs['stdout'].write('log')
            kwargs['stderr'].write('warning')
            return subprocess.CompletedProcess(argv, 0)
        with patch("agents.mini_swe_agent.adapter.subprocess.run", side_effect=simulate) as process:
            result = self.agent.run(prompt, self.workspace, {})
        self.assertEqual((result.output, result.tokens_used, result.exec_count), ("done", 22, 1))
        self.assertIsNone(result.error)
        self.assertEqual(result.raw_trace, [self.trajectory])
        self.assertEqual((self.directory / "patch.diff").read_text(), "diff --git a/x b/x\n")
        script = process.call_args.args[0][2]
        self.assertIn("MSWEA_CONFIGURED=true", script)
        self.assertIn("${CUSTOM_KEY:?", script)
        self.assertIn("'/source build/mini'", script)
        self.assertNotIn(prompt, script)

    def test_failure_and_timeout_preserve_partial_usage(self):
        self.save_trace()
        for outcome, expected in [(subprocess.CompletedProcess([], 4, "", "failure"), "Non-zero"),
                                  (subprocess.TimeoutExpired("mini", 9, output=b"partial"), "Timeout")]:
            self.workspace.execute.reset_mock()
            with self.subTest(expected=expected), patch("agents.mini_swe_agent.adapter.subprocess.run") as process:
                if isinstance(outcome, Exception):
                    def timeout_with_log(*args, **kwargs):
                        kwargs['stdout'].write('partial')
                        kwargs['stdout'].flush()
                        raise outcome
                    process.side_effect = timeout_with_log
                else:
                    process.return_value = outcome
                result = self.agent.run("task", self.workspace, {})
            self.assertIn(expected, result.error)
            self.assertEqual(result.tokens_used, 22)
            if expected == "Timeout":
                self.workspace.execute.assert_not_called()
                self.assertEqual((self.directory / "stdout.txt").read_text(), "partial")

    def test_invalid_and_incomplete_trajectory_not_success(self):
        for content in ('{broken', '{"info": {}, "messages": null}',
                        '{"info": {}, "messages": []}'):
            (self.directory / "trajectory.json").write_text(content)
            with patch("agents.mini_swe_agent.adapter.subprocess.run",
                       return_value=subprocess.CompletedProcess([], 0, "", "")):
                result = self.agent.run("task", self.workspace, {})
            self.assertIsNotNone(result.error)
            self.assertEqual((self.directory / "trajectory.json").read_text(), content)

    def test_proxy_routes_responses_and_rejects_missing_configuration(self):
        self.save_trace()
        with patch("src.usage_proxy.recording_proxy", return_value=nullcontext("http://127.0.0.1:1234/v1")), \
                patch("agents.mini_swe_agent.adapter.subprocess.run",
                      return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.agent.run("task", self.workspace, {"record_raw_usage": True})
        config = json.loads((self.directory / "config.yaml").read_text())
        self.assertEqual(config["model"]["model_kwargs"]["api_base"], "http://127.0.0.1:1234/v1")
        with self.assertRaisesRegex(ValueError, "explicit supported protocol and model_base_url"):
            MiniSweAgent().run("task", self.workspace, {"executable": "mini", "model": "x",
                                                      "record_raw_usage": True})

    def test_five_model_plans_and_conflict(self):
        config = load_experiment(ROOT / "experiments/run_free_mini_swe_agent_verified.toml")
        for name in ("gpt-5.6-sol", "claude-opus-5", "deepseek-v4.1-flash", "qwen3.8-max", "qwen3-coder-next"):
            model = load_model({"path": f"models/{name}.toml"}, ROOT)
            with patch("subprocess.run", side_effect=AssertionError("no process")):
                plan = plan_experiment(replace(config, model=model))
            self.assertEqual(plan["task_count"], 10)
            self.assertFalse(plan["runtime_ready"])
            self.assertEqual(plan["model_compatibility"]["status"],
                             "blocked" if name.startswith("qwen") else "configuration_compatible")
        with self.assertRaises(ConfigError):
            self.agent.run("task", self.workspace, {"model": "override"})
        qwen = load_model({"path": "models/qwen3-coder-next.toml", "base_url": "http://fake/v1"}, ROOT)
        self.assertEqual(model_plan(MiniSweAgent(), qwen, {})["status"], "configuration_compatible")
