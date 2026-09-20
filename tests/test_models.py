"""Model selection, adapter mapping and rejection without model services."""

from dataclasses import replace
import json
from pathlib import Path
import shlex
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

from agents.codex.adapter import Codex, WorkspaceCaller
from src.agents import BoundAgent
from src.components import ConfigError
from src.config import config_dict, load_experiment
from src.execution import _snapshots, run_experiment
from src.interfaces import ArtifactDirectory
from src.models import load_model, model_plan
from src.planning import plan_experiment


PROJECT = Path(__file__).resolve().parents[1]
NAMES = ("claude-opus-5", "gpt-5.6-sol", "deepseek-v4.1-flash",
         "qwen3.8-max", "qwen3-coder-next")


def profile(name, **overrides):
    return load_model({"path": f"models/{name}.toml", **overrides}, PROJECT)


class ModelTests(unittest.TestCase):
    def test_all_profiles_and_explicit_api_alias(self):
        for name in NAMES:
            self.assertEqual(profile(name).name, name)
        self.assertEqual(profile("deepseek-v4.1-flash").model_id, "deepseek-flash")
        self.assertEqual(profile("qwen3.8-max").base_url, "")

    def test_invalid_config_and_credentials_are_rejected(self):
        for overrides in ({"api_key": "not-a-real-key"}, {"api_key_env": "sk-example"},
                          {"model_id": 7}, {"base_url": "https://user:secret@example.com"},
                          {"base_url": "https://example.com?key=secret"},
                          {"base_url": "https://{WorkspaceId}.example.com"}):
            with self.subTest(overrides=overrides), self.assertRaises(ConfigError):
                profile("gpt-5.6-sol", **overrides)
        for section in (None, "gpt-5.6-sol", {}, {"path": 1}):
            with self.subTest(section=section), self.assertRaises(ConfigError):
                load_model(section, PROJECT)

    def test_config_does_not_import_adapters_or_read_credentials(self):
        with patch("src.loading.load_adapter", side_effect=AssertionError("import")), \
                patch.dict("os.environ", {"OPENAI_API_KEY": "secret-not-to-print"}):
            config = load_experiment(PROJECT / "experiments/run_free_codex_model.toml")
            serialized = json.dumps(config_dict(config), default=str)
        self.assertEqual(config.model.name, "gpt-5.6-sol")
        self.assertNotIn("secret-not-to-print", serialized)
        legacy = load_experiment(PROJECT / "experiments/run_free_codex_verified.toml")
        self.assertNotIn("model", config_dict(legacy))

    def test_dry_run_displays_all_five_and_reports_blockers(self):
        config = load_experiment(PROJECT / "experiments/run_free_codex_model.toml")
        for name in NAMES:
            with self.subTest(name=name), patch("subprocess.run") as process:
                result = plan_experiment(replace(config, model=profile(name)))
                self.assertEqual(result["task_count"], 10)
                self.assertEqual(result["config"]["model"]["name"], name)
                self.assertFalse(result["runtime_ready"])
                status = "configuration_compatible" if name == "gpt-5.6-sol" else "blocked"
                self.assertEqual(result["model_compatibility"]["status"], status)
                process.assert_not_called()
        qwen = profile("qwen3.8-max", base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(model_plan(Codex(), qwen, {})["status"], "configuration_compatible")

    def test_codex_command_contains_model_endpoint_and_key_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            from src.interfaces import Workspace
            workspace = Mock(spec=Workspace)
            workspace.launch_command.side_effect = lambda argv: argv
            model = profile("gpt-5.6-sol", base_url="http://127.0.0.1:8000/v1")
            options = Codex().configure_model(model, {"timeout": 3})
            caller = WorkspaceCaller(workspace, ArtifactDirectory(Path(directory), "/output"),
                                     "/opt/tokenana/codex", options)
            command = caller._build_codex_command("test", str(Path(directory) / "trace.jsonl"))
            args = shlex.split(command[2])
            overrides = [args[index + 1] for index, arg in enumerate(args) if arg == "-c"]
            provider = tomllib.loads(overrides[0])["model_providers"]["tokenana-model"]
            self.assertEqual(provider["env_key"], "OPENAI_API_KEY")
            self.assertEqual(provider["base_url"], model.base_url)
            self.assertEqual(provider["wire_api"], "responses")
            self.assertIn('model="gpt-5.6-sol"', overrides)

    def test_bound_model_reaches_agent_and_rejects_silent_override(self):
        adapter = Codex()
        adapter.run = Mock(return_value="result")
        bound = BoundAgent(adapter, {"timeout": 3}, profile("gpt-5.6-sol"))
        self.assertEqual(bound.run("prompt", object(), {"timeout": 5}), "result")
        self.assertEqual(adapter.run.call_args.args[2]["model"], "gpt-5.6-sol")
        self.assertEqual(adapter.run.call_args.args[2]["timeout"], 5)
        for key in ("model", "model_provider", "model_base_url", "wire_api", "model_api_key_env"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                bound.run("prompt", object(), {key: "other"})
        self.assertEqual(bound.options, {"timeout": 3})

    def test_new_adapter_reuses_all_profiles_without_core_name_registration(self):
        class NewAgent:
            model_protocols = ("responses", "chat_completions", "anthropic_messages")

            def configure_model(self, model, options):
                return {**options, "selected_model": model}

            def run(self, prompt, workspace, options):
                return options["selected_model"]

        for name in NAMES:
            model = profile(name, base_url="http://127.0.0.1:8000")
            self.assertEqual(BoundAgent(NewAgent(), {}, model).run("prompt", None, {}), model)
        self.assertEqual(model_plan(object(), model, {})["status"], "blocked")

    def test_incompatible_run_fails_before_output_or_process_creation(self):
        config = load_experiment(PROJECT / "experiments/run_free_codex_model.toml")
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.run") as process:
            output = Path(directory) / "run"
            with self.assertRaisesRegex(ConfigError, "anthropic_messages"):
                run_experiment(replace(config, model=profile("claude-opus-5")), {}, output)
            self.assertFalse(output.exists())
            process.assert_not_called()

    def test_model_identity_is_part_of_resume_snapshot(self):
        config = load_experiment(PROJECT / "experiments/run_free_codex_model.toml")
        original, _ = _snapshots(config, {})
        for key, value in (("model_id", "other"), ("base_url", "https://other.example.com"),
                           ("api_key_env", "OTHER_KEY")):
            changed, _ = _snapshots(replace(config, model=replace(config.model, **{key: value})), {})
            self.assertNotEqual(original, changed)


if __name__ == "__main__":
    unittest.main()
