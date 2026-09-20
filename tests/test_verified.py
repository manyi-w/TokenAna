"""Offline dataset mapping tests; do not import Hugging Face or the harness."""

from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from src.config import load_experiment


PROJECT = Path(__file__).resolve().parents[1]
# Load this file directly: the root datasets directory is not a Python package.
spec = importlib.util.spec_from_file_location(
    "verified_adapter_test", PROJECT / "datasets/swe_bench_verified/adapter.py"
)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
SweBenchVerified = adapter.SweBenchVerified


class VerifiedTests(unittest.TestCase):
    def test_repository_data_and_config_limit(self):
        config = load_experiment(PROJECT / "experiments/run_free_codex_verified.toml")
        dataset = SweBenchVerified(config.dataset.path / "data/swe_bench_verified.json")
        tasks = dataset.tasks()
        self.assertEqual(len(tasks), 500)
        self.assertEqual(len({task.instance_id for task in tasks}), 500)
        self.assertEqual(tasks[0].instance_id, "astropy__astropy-12907")
        self.assertEqual(dataset.tasks(**config.dataset.options), tasks[:10])
        self.assertEqual(SweBenchVerified().tasks(limit=1), tasks[:1])
        self.assertEqual(dataset.tasks(limit=0), [])
        self.assertEqual(dataset.tasks(limit=501), tasks)

    def test_only_public_fields_leave_dataset(self):
        public = dict(instance_id="example", repo="owner/repo", base_commit="abc",
                      problem_statement="Unicode λ\n  preserve whitespace\n")
        record = {**public, "patch": "SECRET_PATCH", "test_patch": "SECRET_TEST",
                  "FAIL_TO_PASS": ["hidden"], "hints_text": "PRIVATE_HINT"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            task, = SweBenchVerified(path).tasks()
        self.assertEqual(asdict(task), public)

    def test_invalid_limit_is_rejected(self):
        for limit in (-1, True, 1.5, "10"):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "limit"):
                SweBenchVerified().tasks(limit=limit)
