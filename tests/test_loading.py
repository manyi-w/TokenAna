"""Selected adapter loading, relative imports and module isolation."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from src.components import Component, ConfigError, load_component
from src.config import load_experiment
from src.interfaces import Agent, AgentResult, Workspace
from src.loading import load_adapter


PROJECT = Path(__file__).resolve().parents[1]


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.modules_before = set(sys.modules)
        self.addCleanup(self.remove_loaded_modules)

    def remove_loaded_modules(self):
        for name in set(sys.modules) - self.modules_before:
            if name.startswith("_tokenana_component_"):
                del sys.modules[name]

    def test_config_connects_real_adapters_with_simulated_agent(self):
        config = load_experiment(PROJECT / "experiments/run_free_codex_verified.toml")
        method = load_adapter(config.method)
        dataset = load_adapter(config.dataset)
        tasks = dataset.tasks(**config.dataset.options)
        agent = Mock(spec=Agent)
        agent.run.return_value = AgentResult("fake", "", "", 0, 0, 0, [])
        workspace = Mock(spec=Workspace)
        self.assertEqual(len(tasks), 10)
        for task in tasks:
            result = method.run(task, agent, workspace, config.method.options)
            self.assertIs(result.calls[0], agent.run.return_value)
        self.assertEqual(agent.run.call_count, 10)
        self.assertEqual(workspace.mock_calls, [])
        self.assertTrue(callable(load_adapter(config.agent).run))
        with self.assertRaisesRegex(ConfigError, "no adapter entrypoint"):
            load_adapter(Component("agent", "unfinished", PROJECT, {}))

    def test_same_name_components_and_unselected_broken_sibling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broken = root / "broken"
            broken.mkdir()
            (broken / "manifest.toml").write_text("invalid [", encoding="utf-8")
            (broken / "adapter.py").write_text("import nonexistent_dependency", encoding="utf-8")
            loaded = []
            for value in (1, 2):
                directory = root / str(value)
                directory.mkdir()
                (directory / "manifest.toml").write_text(
                    'kind="method"\nname="same"\nentrypoint="Example"\n', encoding="utf-8")
                (directory / "helper.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
                (directory / "adapter.py").write_text(
                    'from .helper import VALUE\nclass Example:\n    value = VALUE\n',
                    encoding="utf-8")
                loaded.append(load_adapter(load_component(directory, "method", {})))
            self.assertEqual([item.value for item in loaded], [1, 2])
            component = load_component(root / "1", "method", {})
            (root / "1/adapter.py").write_text('from .helper import VALUE\nraise RuntimeError("failed")\n', encoding="utf-8")
            before = {name for name in sys.modules if name.startswith("_tokenana_component_")}
            with self.assertRaisesRegex(RuntimeError, "failed"):
                load_adapter(component)
            self.assertEqual(before, {name for name in sys.modules if name.startswith("_tokenana_component_")})
