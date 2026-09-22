"""Serial single-call execution with task-boundary recovery."""

from contextlib import redirect_stdout
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4

from .agents import BoundAgent
from .config import ExperimentConfig, config_dict
from .interfaces import AgentResult, ArtifactDirectory, PatchResult
from .control import validate_method
from .loading import load_adapter
from .records import RecordingAgent, write_json
from .run_accounting import save_accounting
from .capabilities import accounting_plan
from .telemetry import span


def _json_value(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _restore_trace(directory: Path) -> AgentResult:
    generation = _read_json(directory / "generation.json")
    calls = generation.get("calls") if isinstance(generation, dict) else None
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        raise ValueError(f"{directory}: invalid generation artifact")
    values = dict(calls[0])
    artifacts = values.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"{directory}: generation has no artifact directory")
    values["artifacts"] = ArtifactDirectory(Path(artifacts["host"]), artifacts["execution"])
    return AgentResult(**values)


def _snapshots(config: ExperimentConfig, runtime: dict):
    return _json_value(config_dict(config)), _json_value(runtime)


def run_experiment(
    config: ExperimentConfig, runtime: dict, output: Path, *, resume: bool = False,
    on_accounting=None, pricing=None,
) -> dict:
    method = load_adapter(config.method)
    agent = BoundAgent(load_adapter(config.agent), config.agent.options, config.model,
                       getattr(method, "version", "legacy-unversioned"))
    agent_options = agent.effective_options()
    validate_method(method, agent.adapter, config.method.options, agent_options)
    dataset = load_adapter(config.dataset)
    tasks = dataset.tasks(**config.dataset.options)
    validate_selection = getattr(dataset, "validate_selection", None)
    if callable(validate_selection):
        validate_selection(method, tasks)
    if len({task.instance_id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task IDs")
    for task in tasks:
        if task.instance_id in ("", ".", "..") or "/" in task.instance_id or "\\" in task.instance_id:
            raise ValueError("task IDs must be single directory names")
    dataset.validate_runtime(tasks, runtime)
    accounting = accounting_plan(method, agent.adapter, agent_options, config.model, runtime, dataset)
    if accounting["errors"]:
        raise ValueError("; ".join(accounting["errors"]))
    if agent.adapter.plan(agent_options).get("missing_options", []):
        raise ValueError("agent runtime options are incomplete")

    output = output.resolve()
    if runtime.get('retain_files') and 'retention_mode' not in runtime:
        saved_runtime = _read_json(output / 'runtime.json') if resume else {}
        if not resume or 'retention_mode' in saved_runtime:
            runtime = {**runtime, 'retention_mode': 'research'}
    from .retention import retention_mode
    retention_mode(runtime)
    config_snapshot, runtime_snapshot = _snapshots(config, runtime)
    state_path = output / "state.json"
    if resume:
        if _read_json(output / "config.json") != config_snapshot:
            raise ValueError("experiment configuration differs from the saved run")
        if _read_json(output / "runtime.json") != runtime_snapshot:
            raise ValueError("runtime configuration differs from the saved run")
        if ((output / "tasks.json").is_file() and
                _read_json(output / "tasks.json") != _json_value([asdict(task) for task in tasks])):
            raise ValueError("dataset tasks differ from the saved run")
        state = _read_json(state_path)
        if not isinstance(state.get("tasks"), dict):
            raise ValueError("saved run state is invalid")
        if state.get("method_version", "legacy-unversioned") != agent.method_version:
            raise ValueError("saved method version differs; analyze the old run or start a new run")
        state.pop("error", None)
        state["status"] = "running"
    else:
        from .pricing import load_pricing, validate_pricing
        price_snapshot = validate_pricing(pricing) if pricing is not None else load_pricing()
        output.mkdir(parents=True)
        write_json(output / 'pricing.json', price_snapshot)
        write_json(output / "config.json", config_snapshot)
        write_json(output / "runtime.json", runtime_snapshot)
        write_json(output / "tasks.json", [asdict(task) for task in tasks])
        state = {
            "status": "running",
            "method_version": agent.method_version,
            "tasks": {},
            "accounting_preflight": accounting,
            "evaluation": {"stage": "not_submitted", "attempts": []},
        }
    write_json(state_path, state)

    results = {}
    failed = False
    try:
        for task in tasks:
            saved = state["tasks"].get(task.instance_id)
            if saved and saved.get("stage") != "collected" and (runtime.get('never_regenerate') or getattr(method, "resume_policy", None) == "never_regenerate"):
                previous = output / saved["directory"]
                if not (previous / "generation.json").exists() and (previous / "calls.json").exists():
                    journal = _read_json(previous / "calls.json")
                    calls = journal.get("calls", [])
                    if len(calls) == 1 and calls[0].get("id") == "call-0001" and calls[0].get("stage") == "returned":
                        returned = previous / "call-0001.json"
                        if returned.exists():
                            write_json(previous / "generation.json", {"calls": [_read_json(returned)]})
                if (previous / "generation.json").exists():
                    trace = _restore_trace(previous)
                    collected = (dataset.collect_saved(trace) if trace.submission_eligible else
                                 PatchResult("", False, trace.error or "not eligible for submission"))
                    write_json(previous / "result.json", asdict(collected))
                    saved.update(stage="collected", success=collected.success, agent_error=trace.error)
                    results[task.instance_id] = trace
                    write_json(state_path, state)
                    continue
                if saved.get("stage") in ("generating", "generation_interrupted"):
                    saved.update(stage="generation_interrupted", success=False,
                                 agent_error="Interrupted generation is not retried by this method")
                    write_json(state_path, state)
                    continue
            if saved and saved.get("stage") == "collected":
                directory = output / saved["directory"]
                if not (directory / "result.json").is_file():
                    raise ValueError(f"{task.instance_id}: collected result artifact is missing")
                results[task.instance_id] = _restore_trace(directory)
                continue

            task_state = saved or {"attempts": []}
            attempts = task_state.setdefault("attempts", [])
            relative = Path("tasks") / task.instance_id / f"attempt-{len(attempts) + 1:04d}"
            directory = output / relative
            directory.mkdir(parents=True)
            attempts.append(str(relative))
            task_state.update(stage="preparing", directory=str(relative), resolved=None)
            state["tasks"][task.instance_id] = task_state
            write_json(state_path, state)
            recorded_agent = RecordingAgent(agent, directory, case_id=task.instance_id)
            with dataset.prepare(task, directory, runtime) as workspace:
                task_state["stage"] = "generating"
                write_json(state_path, state)
                with span(directory, 'generation', case_id=task.instance_id):
                    result = method.run(task, recorded_agent, workspace, config.method.options)
                write_json(directory / "generation.json", asdict(result))
                if len(result.calls) != 1:
                    raise ValueError("minimal runner requires one explicitly selected agent call")
            trace = result.calls[0]
            with span(directory, 'collect', case_id=task.instance_id):
                collected = (dataset.collect_saved(trace) if trace.submission_eligible else
                             PatchResult("", False, trace.error or "not eligible for submission"))
            write_json(directory / "result.json", asdict(collected))
            results[task.instance_id] = trace
            task_state.update(stage="collected", agent_error=trace.error,
                              success=collected.success)
            write_json(state_path, state)

        evaluation = state.setdefault("evaluation", {"stage": "not_submitted", "attempts": []})
        prepared = False
        if evaluation.get("stage") == "prepared":
            submission = _read_json(output / "submission.json")
            prepared = Path(submission["predictions"]).is_file()
        if not prepared:
            attempts = evaluation.setdefault("attempts", [])
            relative = Path(f"submission-{len(attempts) + 1:04d}")
            attempts.append(str(relative))
            evaluation.update(stage="preparing", directory=str(relative))
            evaluation.setdefault("run_id", f"tokenana-{uuid4().hex}")
            write_json(state_path, state)
            with (output / "prediction-generation.log").open("w", encoding="utf-8") as log, redirect_stdout(log):
                submission = dataset.prepare_submission(
                    {key: value for key, value in results.items() if value.submission_eligible},
                    output / relative, agent=config.agent.name, mode=config.method.name,
                    run_id=evaluation["run_id"],
                )
            write_json(output / "submission.json", asdict(submission))
            evaluation["stage"] = "prepared"
        state["status"] = "submission_prepared"
    except BaseException as error:
        failed = True
        state.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                     error=str(error))
        write_json(state_path, state)
        raise
    finally:
        try:
            with span(output, 'accounting'):
                report = save_accounting(output, state, tasks, method, agent.adapter)
            if on_accounting:
                on_accounting(report)
        except Exception as error:
            state["accounting"] = {"status": "failed", "error": str(error)}
            if not failed:
                state.update(status="failed", error=f"accounting failed: {error}")
                raise
        finally:
            write_json(state_path, state)
    return {"output": str(output), **state}
