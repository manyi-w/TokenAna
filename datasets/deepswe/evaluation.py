"""Explicit local evaluation of saved committed patches; never starts an agent."""

import json
import math
from pathlib import Path
import shutil
import subprocess

from src.interfaces import ArtifactDirectory
from src.records import write_json
from src.workspaces import DockerWorkspace
from .tasks import read_task
from .workspace import check_repository, container, resource_options


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _inside(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("submission paths must be relative")
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    return path


def evaluation_inputs(run, submission):
    predictions = Path(submission["predictions"])
    manifest = _read(predictions)
    if not isinstance(manifest, dict) or manifest.get("dataset") != "deepswe" or manifest.get("run_id") != submission.get("run_id"):
        raise ValueError("DeepSWE submission identity mismatch")
    cases = manifest.get("cases")
    selected = {task["instance_id"]: task for task in _read(run / "tasks.json")}
    if not isinstance(cases, dict) or set(cases) != set(selected):
        raise ValueError("DeepSWE submission must describe exactly the saved task selection")
    runtime = _read(run / "runtime.json")
    images, executable = runtime.get("verifier_images"), runtime.get("verifier_python")
    if (not isinstance(images, dict) or not isinstance(executable, str)
            or not Path(executable).is_absolute()):
        raise ValueError("runtime requires verifier_images and an absolute in-image verifier_python")
    for case, item in cases.items():
        if not isinstance(item, dict) or item.get("status") not in ("ready", "capture_error", "not_submitted"):
            raise ValueError(f"{case}: invalid saved submission state")
        record = read_task(_inside(predictions.parent, item["task_directory"]))
        if (record.task.instance_id != case or record.task.repo != selected[case]["repo"]
                or record.task.base_commit != selected[case]["base_commit"]
                or record.task.problem_statement != selected[case]["problem_statement"]
                or item.get("repo") != record.task.repo or item.get("base_commit") != record.task.base_commit):
            raise ValueError(f"{case}: submission task snapshot differs from saved run")
        if item["status"] == "ready":
            if not _inside(predictions.parent, item["patch"]).is_file():
                raise ValueError(f"{case}: submitted patch is missing")
            if not isinstance(images.get(case), str) or not images[case]:
                raise ValueError(f"{case}: prepared verifier image is missing from runtime")
            resource_options(record.config["verifier"]["environment"])
    return manifest, runtime


def _require_ctrf(directory):
    report = _read(Path(directory) / "logs/verifier/ctrf.json")
    if (not isinstance(report, dict) or not isinstance(report.get("results"), dict)
            or not isinstance(report["results"].get("tests"), list)):
        raise ValueError("missing or invalid native CTRF report")


def _case_evaluation(case, item, source, runtime, directory):
    record = read_task(_inside(source, item["task_directory"]))
    config = record.config
    logs = directory / "logs"
    (logs / "verifier").mkdir(parents=True)
    (logs / "artifacts").mkdir()
    (logs / "agent").mkdir()
    shutil.copyfile(_inside(source, item["patch"]), logs / "artifacts/model.patch")
    component = Path(__file__).parent.resolve()
    mounts = [(logs, "/logs", False),
              (logs / "artifacts/model.patch", "/logs/artifacts/model.patch", True),
              (_inside(source, item["task_directory"]), "/tokenana/task", True),
              (component / "upstream/pier/src", "/tokenana/pier/src", True),
              (component / "verifier_worker.py", "/tokenana/verifier_worker.py", True)]
    outcome = {"status": "error", "resolved": None, "directory": str(directory), "rewards": None}
    try:
        with container(runtime["verifier_images"][case], directory, config["verifier"]["environment"], mounts,
                       retention=runtime.get('retain_files', False),
                       relaxed_storage=runtime.get('relaxed_storage', False),
                       environment={**config["environment"].get("env", {}),
                                    **config["verifier"]["environment"].get("env", {})}) as name:
            workspace = DockerWorkspace(name, "/app", ArtifactDirectory(logs, "/logs"))
            check_repository(workspace, record.task.base_commit)
            # Verifier images contain the task's unchanged tests, not a reference solution.
            workspace.execute(["bash", "-c", "test -f /tests/test.sh && test -f /tests/grader.py "
                               "&& test -f /tests/test.patch && test -f /tests/config.json "
                               "&& test ! -e /solution"], timeout=60).check_returncode()
            argv = workspace.launch_command(["env", "PYTHONDONTWRITEBYTECODE=1",
                    "PYTHONPATH=/tokenana/pier/src", runtime["verifier_python"], "-B", "/tokenana/verifier_worker.py"])
            with (directory / "stdout.txt").open("w") as out, (directory / "stderr.txt").open("w") as err:
                process = subprocess.run(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                         timeout=config["verifier"]["timeout_sec"])
            outcome["returncode"] = process.returncode
            if process.returncode:
                raise ValueError(f"Pier worker exited with code {process.returncode}")
            result = _read(logs / "pier-result.json")
            rewards = result.get("rewards") if isinstance(result, dict) else None
            if not isinstance(rewards, dict):
                raise ValueError("Pier returned no reward mapping")
            outcome["rewards"] = rewards
            reward = rewards.get("reward")
            if type(reward) not in (int, float) or not math.isfinite(reward) or reward not in (0, 1):
                raise ValueError("verifier reward is not 0 or 1 (including the -1 crash sentinel)")
            _require_ctrf(directory)
            outcome.update(status="completed", resolved=reward == 1)
        # Successful container cleanup is part of a completed local evaluation.
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        outcome.update(status="error", resolved=None, error=f"{type(error).__name__}: {error}")
    write_json(directory / "case-result.json", outcome)
    return outcome


def _report(run_id, cases):
    result = {"dataset": "deepswe", "run_id": run_id, "cases": cases}
    groups = {"resolved": [case for case, item in cases.items() if item.get("resolved") is True],
              "unresolved": [case for case, item in cases.items() if item.get("resolved") is False],
              "completed": [case for case, item in cases.items() if item["status"] == "completed"],
              "error": [case for case, item in cases.items() if item["status"] == "error"]}
    for name, ids in groups.items():
        result[name + "_ids"] = sorted(ids)
        result[name + "_instances"] = len(ids)
    return result


def evaluate_local(run, submission, directory, previous_report=None):
    manifest, runtime = evaluation_inputs(run, submission)
    source = Path(submission["predictions"]).parent
    previous = read_report(previous_report) if previous_report is not None else None
    if previous is not None and previous.get("run_id") != submission["run_id"]:
        raise ValueError("previous evaluation belongs to a different submission")
    if previous is not None and not set(previous["cases"]) <= set(manifest["cases"]):
        raise ValueError("previous evaluation contains foreign task IDs")
    outcomes = {}
    report_path = directory / "report.json"
    # Retain every completed case before retrying any earlier failure.
    for case, item in manifest["cases"].items():
        saved = previous.get("cases", {}).get(case) if previous else None
        if saved and saved["status"] == "completed":
            old_patch = Path(saved["directory"]) / "logs/artifacts/model.patch"
            if (item["status"] != "ready" or not old_patch.is_file()
                    or old_patch.read_bytes() != _inside(source, item["patch"]).read_bytes()):
                raise ValueError(f"{case}: completed evaluation patch changed or its saved artifact is missing")
            _require_ctrf(saved["directory"])
            outcomes[case] = saved
    write_json(report_path, _report(submission["run_id"], outcomes))
    # A checkpoint is written after each case; a later explicit local action resumes it.
    for case, item in manifest["cases"].items():
        if case in outcomes:
            continue
        if item["status"] != "ready":
            outcomes[case] = {"status": "error" if item["status"] == "capture_error" else "not_submitted",
                              "resolved": None, "error": item.get("error")}
        else:
            case_dir = directory / case
            case_dir.mkdir()
            outcomes[case] = _case_evaluation(case, item, source, runtime, case_dir)
        write_json(report_path, _report(submission["run_id"], outcomes))
    if not outcomes:
        write_json(report_path, _report(submission["run_id"], outcomes))
    return report_path


def read_report(path):
    raw = _read(path)
    if not isinstance(raw, dict) or raw.get("dataset") != "deepswe" or not isinstance(raw.get("cases"), dict):
        raise ValueError("invalid DeepSWE local report")
    if not isinstance(raw.get("run_id"), str) or not raw["run_id"]:
        raise ValueError("DeepSWE report requires run_id")
    for case, item in raw["cases"].items():
        if not isinstance(case, str) or not isinstance(item, dict):
            raise ValueError("invalid DeepSWE report case")
        if item.get("status") not in ("completed", "error", "not_submitted"):
            raise ValueError(f"{case}: invalid evaluation status")
        if item["status"] == "completed":
            if not isinstance(item.get("directory"), str) or not Path(item["directory"]).is_absolute():
                raise ValueError(f"{case}: completed evaluation requires its absolute artifact directory")
            _require_ctrf(item["directory"])
            if type(item.get("resolved")) is not bool:
                raise ValueError(f"{case}: completed result requires a boolean outcome")
            rewards = item.get("rewards")
            reward = rewards.get("reward") if isinstance(rewards, dict) else None
            if type(reward) not in (int, float) or reward not in (0, 1) or (reward == 1) != item["resolved"]:
                raise ValueError(f"{case}: outcome disagrees with native reward")
        elif item.get("resolved") is not None:
            raise ValueError(f"{case}: failed/unsubmitted evaluation cannot claim an outcome")
    expected = _report(raw["run_id"], raw["cases"])
    if any(raw.get(key) != value for key, value in expected.items() if key.endswith(("_ids", "_instances"))):
        raise ValueError("DeepSWE report counts disagree with its cases")
    return raw
