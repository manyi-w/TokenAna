"""DeepSWE task selection and artifact handoff; no Pier imports during planning."""

from collections import Counter
import shutil

from src.interfaces import PatchResult, SubmissionPlan
from src.records import write_json
from .tasks import select_tasks


class DeepSWE:
    supports_model_channel = True
    requires_record_raw_usage = True

    def __init__(self):
        self.records = {}

    def tasks(self, *, data_path=None, languages=None, task_ids=None, limit=None):
        records = select_tasks(data_path=data_path, languages=languages, task_ids=task_ids, limit=limit)
        self.records = {record.task.instance_id: record for record in records}
        return [record.task for record in records]

    def validate_selection(self, method, tasks):
        allowed = getattr(method, "task_languages", None)
        if allowed is not None and any(self.records[t.instance_id].language not in allowed for t in tasks):
            raise ValueError("DeepSWE task languages do not match the selected method; filter the dataset explicitly")

    def plan(self, options):
        records = select_tasks(**options)
        return {"task_count": len(records), "languages": dict(Counter(r.language for r in records)),
                "workspace": "/app", "network": "none", "patch_rule": "deepswe-committed-v1",
                "evaluation": "local Pier Verifier in a separate prepared image",
                "evaluation_template": ["tokenAna", "evaluate", "<run>", "local", "--execute"],
                "required": ["Prepared per-task agent and verifier images; no automatic pull/build",
                             "Verifier image: root Python 3.12+, installed Pier distribution metadata and dependencies",
                             "Agent image: GNU timeout for the original collect-hook timeout",
                             "Docker storage quota support for each task's storage_mb",
                             "runtime.model_channel: kind=unix_socket, python=absolute prepared Python 3 path",
                             "Linux Docker on the same host; record_raw_usage=true; explicit model address"],
                "environment_checked": False, "runtime_ready": False}

    def validate_runtime(self, tasks, runtime):
        if runtime.get("network") != "none":
            raise ValueError("DeepSWE requires runtime.network='none'")
        for name in ("images", "verifier_images"):
            values = runtime.get(name)
            if not isinstance(values, dict) or any(
                    not isinstance(values.get(t.instance_id), str) or not values[t.instance_id] for t in tasks):
                raise ValueError(f"runtime.{name} must explicitly cover every selected task")
        from pathlib import PurePosixPath
        verifier_python = runtime.get("verifier_python")
        if not isinstance(verifier_python, str) or not PurePosixPath(verifier_python).is_absolute():
            raise ValueError("DeepSWE requires an absolute prepared verifier_python path")
        prefix = runtime.get("command_prefix", [])
        if not isinstance(prefix, list) or any(not isinstance(item, str) for item in prefix):
            raise ValueError("command_prefix must be an argv list")
        from src.model_channel import validate_channel
        validate_channel(runtime, supported=True)

    def prepare(self, task, directory, runtime):
        from .workspace import prepare
        return prepare(self.records[task.instance_id], directory, runtime)

    def collect_saved(self, trace):
        if trace.artifacts is None:
            raise ValueError("agent result has no artifact directory")
        directory = trace.artifacts.host
        status = directory / "patch-capture.status"
        if not status.is_file() or status.read_text().strip() != "captured":
            return PatchResult("", False, trace.error or "DeepSWE patch capture missing or failed")
        # No textual response fallback; an empty committed patch is a real empty submission.
        patch = (directory / "model.patch").read_bytes().decode("utf-8")
        return PatchResult(patch, bool(patch.strip()) and not trace.error, trace.error or "")

    def prepare_submission(self, results, directory, *, agent, mode, run_id=None):
        if not run_id or not set(results) <= self.records.keys():
            raise ValueError("DeepSWE submission requires a run ID and selected task results")
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=False)
        cases = {}
        for case, record in self.records.items():
            target = directory / case
            target.mkdir()
            # Snapshot only the public instruction and configuration needed by Pier.
            (target / "task.toml").write_text(record.config_text, encoding="utf-8")
            (target / "instruction.md").write_bytes(record.task.problem_statement.encode("utf-8"))
            entry = {"repo": record.task.repo, "base_commit": record.task.base_commit,
                     "language": record.language, "task_directory": case,
                     "patch": None, "status": "not_submitted"}
            trace = results.get(case)
            if trace is not None and trace.submission_eligible:
                if trace.artifacts is None:
                    raise ValueError(f"{case}: selected call has no artifact directory")
                captured = self.collect_saved(trace)
                status = trace.artifacts.host / "patch-capture.status"
                if status.is_file() and status.read_text().strip() == "captured":
                    shutil.copyfile(trace.artifacts.host / "model.patch", target / "model.patch")
                    entry.update(patch=f"{case}/model.patch", status="ready", agent_error=trace.error)
                else:
                    entry.update(status="capture_error", error=captured.error)
            cases[case] = entry
        predictions = directory / "predictions.json"
        write_json(predictions, {"dataset": "deepswe", "run_id": run_id, "agent": agent,
                                 "method": mode, "patch_rule": "deepswe-committed-v1", "cases": cases})
        return SubmissionPlan(predictions, [], [], kind="local", run_id=run_id)

    def plan_evaluation(self, run, submission):
        from .evaluation import evaluation_inputs
        manifest, runtime = evaluation_inputs(run, submission)
        return {"kind": "local", "run_id": submission["run_id"],
                "ready_ids": [case for case, item in manifest["cases"].items() if item["status"] == "ready"],
                "verifier_images": runtime["verifier_images"],
                "verifier_python": runtime["verifier_python"],
                "network": "none", "environment_checked": False,
                "note": "Execute only with --execute; images and Pier dependencies must already exist"}

    def evaluate_local(self, run, submission, directory, previous_report=None):
        from .evaluation import evaluate_local
        return evaluate_local(run, submission, directory, previous_report)

    def read_report(self, path):
        from .evaluation import read_report
        return read_report(path)
