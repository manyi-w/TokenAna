"""Public JSON repair tasks; prepared containers, no inferred evaluation service."""

import json
from pathlib import Path
from dataclasses import dataclass

from src.components import Component
from src.interfaces import Task
from src.loading import load_adapter
from src.records import write_json


@dataclass
class LocalSubmission:
    predictions: Path
    command: list
    report_command: list


class RepositoryTasks:
    def __init__(self):
        directory = Path(__file__).resolve().parent.parent / "swe_bench_verified"
        self.workspace_adapter = load_adapter(Component("dataset", "workspace", directory, {}, "SweBenchVerified"))

    def tasks(self, *, data_path, limit=None):
        path = Path(data_path)
        if not path.is_absolute():
            raise ValueError("repository task data_path must be absolute")
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a nonnegative integer")
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("repository tasks must be a JSON array")
        tasks = []
        for record in records[:limit]:
            if not isinstance(record, dict):
                raise ValueError("repository task must be an object")
            values = {key: record.get(key) for key in ("instance_id", "repo", "base_commit", "problem_statement")}
            if any(not isinstance(value, str) or not value for value in values.values()):
                raise ValueError("repository task requires four nonempty public string fields")
            tasks.append(Task(**values))
        return tasks

    def plan(self, options):
        return {"data_path": options.get("data_path"), "evaluation": "not configured",
                "required": ["Prepared task containers at each base commit"], "environment_checked": False}

    def validate_runtime(self, tasks, runtime):
        return self.workspace_adapter.validate_runtime(tasks, runtime)

    def prepare(self, task, directory, runtime):
        return self.workspace_adapter.prepare(task, directory, runtime)

    def collect_saved(self, trace):
        return self.workspace_adapter.collect_saved(trace)

    def prepare_submission(self, results, directory, **identity):
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / "patches.json"
        write_json(path, {case: {"patch": (result.artifacts.host / "patch.diff").read_text(),
                                "error": result.error} for case, result in results.items()})
        return LocalSubmission(path, [], [])

    def read_report(self, path):
        raise ValueError("repository-tasks requires a dataset-specific evaluator")
