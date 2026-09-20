"""Run and resume the complete local chain with every process simulated."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.config import load_experiment
from src.execution import run_experiment


PROJECT = Path(__file__).resolve().parents[1]
DATA = json.loads((PROJECT / "datasets/swe_bench_verified/data/swe_bench_verified.json").read_text())
RAW_PATCH = "\ndiff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-old\n+new\n"


class SimulatedProcesses:
    def __init__(self, commits, interrupt_model_call=None):
        self.commits = iter(commits)
        self.interrupt_model_call = interrupt_model_call
        self.model_calls = 0
        self.roots = {}
        self.heads = {}
        self.commands = []

    def __call__(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[:2] == ["docker", "create"]:
            name = argv[argv.index("--name") + 1]
            mount = argv[argv.index("--mount") + 1]
            source = mount.split(",src=", 1)[1].split(",dst=", 1)[0]
            self.roots[name] = Path(source)
            self.heads[name] = next(self.commits)
            return subprocess.CompletedProcess(argv, 0, name + "\n", "")
        if argv[:2] in (["docker", "start"], ["docker", "rm"]):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["docker", "exec"]:
            name = argv[5]
            if "rev-parse" in argv:
                return subprocess.CompletedProcess(argv, 0, self.heads[name] + "\n", "")
            if "status" in argv and "--porcelain" in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
            self.model_calls += 1
            if self.model_calls == self.interrupt_model_call:
                raise KeyboardInterrupt()
            call = sorted(self.roots[name].glob("call-*"))[-1]
            trace = [
                {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
                {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}},
            ]
            (call / "trace.jsonl").write_text(
                "\n".join(json.dumps(item) for item in trace) + "\n", encoding="utf-8"
            )
            (call / "patch.diff").write_text(RAW_PATCH, encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected process: {argv}")


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def config(self, limit):
        path = self.root / f"experiment-{limit}.toml"
        path.write_text(
            f'[method]\npath = "{PROJECT / "methods/run_free"}"\n'
            f'[agent]\npath = "{PROJECT / "agents/codex"}"\n'
            '[agent.options]\nexecutable = "/opt/tokenana/codex"\ntimeout = 9\n'
            f'[dataset]\npath = "{PROJECT / "datasets/swe_bench_verified"}"\n'
            f'[dataset.options]\nlimit = {limit}\n',
            encoding="utf-8",
        )
        return load_experiment(path)

    @staticmethod
    def runtime(limit):
        return {
            "network": "none",
            "command_prefix": ["/opt/conda/bin/conda", "run", "-n", "testbed"],
            "images": {record["instance_id"]: "prepared:test" for record in DATA[:limit]},
            "environment": {"MODEL_ENDPOINT": "http://fake-model.invalid"},
        }

    def test_complete_chain_preserves_raw_patch_and_stops_before_submit(self):
        process = SimulatedProcesses([DATA[0]["base_commit"]])
        output = self.root / "run"
        with patch("subprocess.run", side_effect=process):
            result = run_experiment(self.config(1), self.runtime(1), output)

        self.assertEqual(result["status"], "submission_prepared")
        state = json.loads((output / "state.json").read_text())
        task = state["tasks"][DATA[0]["instance_id"]]
        attempt = output / task["directory"]
        self.assertEqual(task["stage"], "collected")
        self.assertEqual(json.loads((attempt / "result.json").read_text())["patch"], RAW_PATCH.strip())
        submission = json.loads((output / "submission.json").read_text())
        predictions = json.loads(Path(submission["predictions"]).read_text())
        self.assertEqual(predictions[0]["model_patch"], RAW_PATCH)
        self.assertEqual(submission["command"][:4],
                         ["sb-cli", "submit", "swe-bench_verified", "test"])
        self.assertFalse(any(command and command[0] == "sb-cli" for command in process.commands))
        self.assertEqual(process.model_calls, 1)
        operations = [json.loads(line)["operation"] for line in
                      (attempt / "container.jsonl").read_text().splitlines()]
        self.assertEqual(operations, ["create", "start", "rm"])
        self.assertTrue((output / "config.json").is_file())
        self.assertTrue((output / "runtime.json").is_file())

    def test_resume_keeps_completed_task_and_creates_a_new_attempt(self):
        config = self.config(2)
        runtime = self.runtime(2)
        output = self.root / "interrupted"
        unselected = self.root / "unselected-method"
        unselected.mkdir()
        (unselected / "adapter.py").write_text("invalid python !", encoding="utf-8")
        first = SimulatedProcesses([record["base_commit"] for record in DATA[:2]],
                                   interrupt_model_call=2)
        with patch("subprocess.run", side_effect=first), self.assertRaises(KeyboardInterrupt):
            run_experiment(config, runtime, output)
        interrupted = json.loads((output / "state.json").read_text())
        first_id, second_id = (record["instance_id"] for record in DATA[:2])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["tasks"][first_id]["stage"], "collected")
        first_generation = output / interrupted["tasks"][first_id]["directory"] / "generation.json"
        original_generation = first_generation.read_text()
        (unselected / "adapter.py").write_text("changed and still invalid !", encoding="utf-8")

        resumed_process = SimulatedProcesses([DATA[1]["base_commit"]])
        with patch("subprocess.run", side_effect=resumed_process):
            result = run_experiment(config, runtime, output, resume=True)
        self.assertEqual(result["status"], "submission_prepared")
        self.assertEqual(resumed_process.model_calls, 1)
        self.assertEqual(first_generation.read_text(), original_generation)
        final = json.loads((output / "state.json").read_text())
        self.assertEqual(len(final["tasks"][first_id]["attempts"]), 1)
        self.assertEqual(len(final["tasks"][second_id]["attempts"]), 2)
        self.assertTrue(all((output / path).is_dir()
                            for path in final["tasks"][second_id]["attempts"]))
        submission = json.loads((output / "submission.json").read_text())
        self.assertEqual(len(json.loads(Path(submission["predictions"]).read_text())), 2)

    def test_resume_rejects_changed_runtime_and_missing_collected_result(self):
        config = self.config(1)
        runtime = self.runtime(1)
        output = self.root / "checked"
        with patch("subprocess.run", side_effect=SimulatedProcesses([DATA[0]["base_commit"]])):
            run_experiment(config, runtime, output)
        changed = {**runtime, "network": "different"}
        with self.assertRaisesRegex(ValueError, "runtime configuration differs"):
            run_experiment(config, changed, output, resume=True)
        state = json.loads((output / "state.json").read_text())
        attempt = output / state["tasks"][DATA[0]["instance_id"]]["directory"]
        (attempt / "result.json").unlink()
        with patch("subprocess.run") as process, self.assertRaisesRegex(ValueError, "artifact is missing"):
            run_experiment(config, runtime, output, resume=True)
        process.assert_not_called()
