"""Explicit evaluation commands and local report association; never run during generation."""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess

from .records import write_json


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset(run):
    from .components import Component
    from .loading import load_adapter

    component = _read(run / "config.json")["dataset"]
    return load_adapter(Component(**{**component, "path": Path(component["path"])}))


def _saved_run_id(submission):
    # Compatibility with already-saved sb-cli plans, before explicit run_id.
    if submission.get("run_id"):
        return submission["run_id"]
    command = submission.get("command", [])
    return command[-1] if command else None


def evaluate(run, action, *, execute=False, report=None, run_id=None):
    run = Path(run).resolve()
    state = _read(run / "state.json")
    if state.get("status") == "running":
        raise ValueError("stop generation before evaluation")
    submission = _read(run / "submission.json")
    if action == "attach":
        if report is None or not run_id or run_id != _saved_run_id(submission):
            raise ValueError("attach requires --report and --run-id matching the saved submission")
        return attach_report(run, Path(report), run_id)
    if action == "local":
        if submission.get("kind") != "local":
            raise ValueError("this dataset has no saved local evaluation plan")
        return _evaluate_local(run, submission, execute=execute)
    if action not in ("submit", "fetch"):
        raise ValueError("unknown evaluation action")
    if not submission.get("command") or not submission.get("report_command"):
        raise ValueError("this dataset has no configured remote evaluator")
    command = submission["command" if action == "submit" else "report_command"]
    if action == "submit" and not Path(submission["predictions"]).is_file():
        raise ValueError("saved predictions are missing")
    if not execute:
        return {"status": "plan_only", "command": command, "run": str(run)}
    evaluation = state.setdefault("evaluation", {})
    if action == "submit" and evaluation.get("remote_submission") in ("started", "succeeded", "unknown"):
        raise ValueError("submission already attempted; inspect its remote status before resubmitting")
    directory = run / "evaluation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, exist_ok=False)
    record = {"action": action, "command": command, "status": "started", "directory": str(directory)}
    evaluation.setdefault("remote_actions", []).append(record)
    if action == "submit":
        evaluation["remote_submission"] = "started"
    write_json(run / "state.json", state)
    try:
        with (directory / "stdout.txt").open("w") as out, (directory / "stderr.txt").open("w") as err:
            process = subprocess.run(command, cwd=directory, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
        record.update(status="succeeded" if process.returncode == 0 else "failed", returncode=process.returncode)
        if action == "submit":
            evaluation["remote_submission"] = "succeeded" if process.returncode == 0 else "unknown"
    except BaseException:
        record["status"] = "unknown"
        if action == "submit":
            evaluation["remote_submission"] = "unknown"
        raise
    finally:
        write_json(run / "state.json", state)
    return record


@contextmanager
def _local_lock(run):
    parent = run / "evaluation"
    parent.mkdir(exist_ok=True)
    lock = parent / "local.lock"
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError("local evaluation is locked; after a hard stop, inspect/stop its containers "
                         "before removing evaluation/local.lock") from None
    try:
        with stream:
            stream.write(datetime.now(timezone.utc).isoformat())
        yield
    finally:
        lock.unlink()


def _evaluate_local(run, submission, *, execute):
    dataset = _dataset(run)
    planner, evaluator = getattr(dataset, "plan_evaluation", None), getattr(dataset, "evaluate_local", None)
    if not callable(planner) or not callable(evaluator):
        raise ValueError("dataset has no local evaluation implementation")
    plan = planner(run, submission)
    if not execute:
        return {"status": "plan_only", "run": str(run), "plan": plan}
    with _local_lock(run):
        state = _read(run / "state.json")
        if state.get("status") == "running":
            raise ValueError("stop generation before evaluation")
        evaluation = state.setdefault("evaluation", {})
        actions = evaluation.setdefault("local_actions", [])
        previous = next((Path(item["report"]) for item in reversed(actions)
                         if item.get("run_id") == submission["run_id"]
                         and item.get("report") and Path(item["report"]).is_file()), None)
        directory = run / "evaluation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory.mkdir(exist_ok=False)
        record = {"action": "local", "status": "started", "run_id": submission["run_id"],
                  "directory": str(directory), "report": str(directory / "report.json"), "plan": plan}
        actions.append(record)
        write_json(run / "state.json", state)
        try:
            source = Path(evaluator(run, submission, directory, previous))
            record.update(status="succeeded", report=str(source))
            summary = attach_report(run, source, submission["run_id"])
            return {**record, "summary": summary}
        except BaseException as error:
            record.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                          error=f"{type(error).__name__}: {error}")
            raise
        finally:
            # attach_report updates the same state file; retain that report association.
            current = _read(run / "state.json")
            for item in current["evaluation"]["local_actions"]:
                if item["directory"] == str(directory):
                    item.update(record)
                    break
            write_json(run / "state.json", current)


def attach_report(run, source, run_id):
    submission = _read(run / "submission.json")
    if run_id != _saved_run_id(submission):
        raise ValueError("report run_id differs from the saved submission")
    dataset = _dataset(run)
    raw = dataset.read_report(source)
    tasks = _read(run / "tasks.json")
    selected = {task["instance_id"] for task in tasks}
    if submission.get("kind") == "local" and "cases" in raw and (
            not isinstance(raw["cases"], dict) or not set(raw["cases"]) <= selected):
        raise ValueError("report contains invalid or foreign case records")
    if "run_id" in raw and raw["run_id"] != run_id:
        raise ValueError("report run_id differs from the saved submission")
    for key, ids in raw.items():
        if key.endswith("_ids"):
            if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
                raise ValueError(f"invalid {key}")
            if len(ids) != len(set(ids)) or not set(ids) <= selected:
                raise ValueError(f"duplicate or foreign IDs in {key}")
            count = raw.get(key.removesuffix("_ids") + "_instances")
            if count is not None and count != len(ids):
                raise ValueError(f"count disagrees with {key}")
    resolved, unresolved = set(raw.get("resolved_ids", [])), set(raw.get("unresolved_ids", []))
    if resolved & unresolved:
        raise ValueError("resolved and unresolved IDs overlap")
    completed = set(raw.get("completed_ids", []))
    if "completed_ids" in raw and not (resolved | unresolved) <= completed:
        raise ValueError("outcome IDs are not in completed_ids")
    directory = run / "evaluation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "report.json", raw)
    outcomes = {case: True if case in resolved else False if case in unresolved else None for case in selected}
    summary = {"run_id": run_id, "source": str(source.resolve()), "report": str(directory / "report.json"),
               "association": "explicit user run-id assertion; selected task IDs validated",
               "outcomes": outcomes, "selected": len(selected),
               "resolved": len(resolved) if "resolved_ids" in raw else None,
               "known_outcomes": len(resolved | unresolved),
               "resolved_over_selected": len(resolved) / len(selected) if selected and "resolved_ids" in raw else None,
               "resolved_over_completed": len(resolved) / len(completed) if completed and "resolved_ids" in raw else None,
               "complete": resolved | unresolved == selected,
               "note": "Both denominators shown explicitly; missing case outcomes remain unknown."}
    write_json(directory / "summary.json", summary)
    state = _read(run / "state.json")
    state.setdefault("evaluation", {}).update(report_summary=str(directory / "summary.json"))
    for case, outcome in outcomes.items():
        if case in state["tasks"]:
            state["tasks"][case]["resolved"] = outcome
    write_json(run / "state.json", state)
    return summary
