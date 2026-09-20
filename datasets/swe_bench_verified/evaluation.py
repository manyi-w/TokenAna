"""Prepare original sb-cli submission inputs without submitting anything."""

import json
from pathlib import Path
import shutil
from typing import Any, Mapping

from src.interfaces import AgentResult, SubmissionPlan
from .compatibility.execution_control.scripts.generate_predictions import generate_predictions


def prepare_submission(
    results: Mapping[str, AgentResult], directory: Path, *,
    agent: str, mode: str, run_id: str | None = None,
) -> SubmissionPlan:
    """Snapshot explicitly selected calls into the original script's layout.

    directory must be new. Callers must finish/stop writers before preparing a
    submission. No success/error filter or textual patch fallback is applied.
    """
    for name in (agent, mode, *results):
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError("agent, mode and instance IDs must be single directory names")
    for result in results.values():
        if result.artifacts is None:
            raise ValueError("selected call has no artifact directory association")

    directory = directory.resolve()
    directory.mkdir(parents=True)
    output = directory / "output"
    base = output / "swebenchverified" / agent / mode
    base.mkdir(parents=True)
    for instance_id, result in results.items():
        destination = base / instance_id
        destination.mkdir()
        source = result.artifacts.host / "patch.diff"
        if source.exists():
            shutil.copy2(source, destination / "patch.diff")

    predictions = directory / f"swebenchverified_{agent}_{mode}.json"
    generate_predictions(str(output), "swebenchverified", agent, mode, str(predictions))
    command = ["sb-cli", "submit", "swe-bench_verified", "test",
               "--predictions_path", str(predictions),
               "--run_id", run_id or f"swebenchverified_{agent}_{mode}"]
    report_command = ["sb-cli", "get-report", "swe-bench_verified", "test", command[-1]]
    return SubmissionPlan(predictions, command, report_command, run_id=command[-1])


def read_report(path: Path) -> dict[str, Any]:
    """Read a saved sb-cli report without changing counts or inferring outcomes.

    Missing fields stay missing; absent IDs do not become failed evaluations.
    This parses a report snapshot, not remote job status or a pass-rate metric.
    """
    with path.open(encoding="utf-8") as stream:
        report = json.load(stream)
    if not isinstance(report, dict):
        raise ValueError("sb-cli report must be a JSON object")
    for name in ("submitted", "completed", "resolved", "unresolved", "error", "empty_patch"):
        count_key, ids_key = f"{name}_instances", f"{name}_ids"
        if count_key in report and (type(report[count_key]) is not int or report[count_key] < 0):
            raise ValueError(f"{count_key} must be a non-negative integer")
        if ids_key in report and (not isinstance(report[ids_key], list) or
                                 any(not isinstance(item, str) for item in report[ids_key])):
            raise ValueError(f"{ids_key} must be a list of instance IDs")
    return report
