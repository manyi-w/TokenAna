"""Read local task descriptions using the standard library only."""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from src.interfaces import Task


DEFAULT_TASKS = Path(__file__).parent / "data/tasks"
LANGUAGES = {"python", "go", "typescript", "javascript", "rust"}


@dataclass(frozen=True)
class TaskRecord:
    task: Task
    language: str
    config: dict
    config_text: str


def read_task(directory):
    directory = Path(directory)
    text = (directory / "task.toml").read_text(encoding="utf-8")
    config = tomllib.loads(text)
    metadata = config.get("metadata", {})
    fields = [metadata.get(name) for name in
              ("task_id", "repository_url", "base_commit_hash", "language")]
    if any(not isinstance(value, str) or not value.strip() for value in fields):
        raise ValueError(f"{directory}: missing public task metadata")
    case, repo, base, language = fields
    if case in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", case):
        raise ValueError(f"{directory}: invalid task ID")
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", base) or language not in LANGUAGES:
        raise ValueError(f"{directory}: invalid base commit or unsupported language")
    if config.get("schema_version") != "1.3" or config.get("steps"):
        raise ValueError(f"{directory}: only single-step schema 1.3 tasks are supported")
    env, agent, verifier = (config.get(key, {}) for key in ("environment", "agent", "verifier"))
    if (env.get("os") != "linux" or agent.get("network_mode") != "no-network"
            or verifier.get("network_mode") != "no-network"
            or verifier.get("environment_mode") != "separate"):
        raise ValueError(f"{directory}: requires Linux, isolated agent and separate offline verifier")
    expected = (f"cd /app && mkdir -p /logs/artifacts && git config --global --add safe.directory /app "
                f"&& git diff --binary {base} HEAD > /logs/artifacts/model.patch")
    hooks = verifier.get("collect", [])
    if (len(hooks) != 1 or hooks[0].get("command") != expected
            or hooks[0].get("service", "main") != "main"):
        raise ValueError(f"{directory}: unsupported patch collection contract")
    for settings in (agent, verifier, hooks[0]):
        timeout = settings.get("timeout_sec")
        if type(timeout) not in (int, float) or not 0 < timeout < float("inf"):
            raise ValueError(f"{directory}: requires a finite positive timeout")
    task = Task(case, repo, base, (directory / "instruction.md").read_bytes().decode("utf-8"))
    return TaskRecord(task, language, config, text)


def select_tasks(*, data_path=None, languages=None, task_ids=None, limit=None):
    root = DEFAULT_TASKS if data_path is None else Path(data_path)
    if data_path is not None and not root.is_absolute():
        raise ValueError("DeepSWE data_path must be absolute")
    if not root.is_dir():
        raise ValueError(f"DeepSWE task directory is missing: {root}")
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit must be a nonnegative integer")
    for name, values in (("languages", languages), ("task_ids", task_ids)):
        if values is not None and (not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))):
            raise ValueError(f"{name} must be a list of unique nonempty strings")
    if languages is not None and not set(languages) <= LANGUAGES:
        raise ValueError("unsupported DeepSWE language filter")
    records = [read_task(path.parent) for path in root.glob("*/task.toml")]
    indexed = {record.task.instance_id: record for record in records}
    if len(indexed) != len(records):
        raise ValueError("duplicate DeepSWE task IDs")
    if task_ids is not None and not set(task_ids) <= indexed.keys():
        raise ValueError("unknown DeepSWE task IDs: " + ", ".join(sorted(set(task_ids) - indexed.keys())))
    return [indexed[case] for case in sorted(indexed)
            if (task_ids is None or case in task_ids)
            and (languages is None or indexed[case].language in languages)][:limit]
