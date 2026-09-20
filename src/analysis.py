"""Offline accounting exports and comparisons; no agent or evaluator execution."""

import csv
from datetime import datetime, timezone
import json
from pathlib import Path

from .accounting import METRICS, render_accounting
from .components import Component
from .interfaces import Task
from .loading import load_adapter
from .records import write_json
from .overhead import overhead_rows


POLICIES = ("original", "corrected")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _cell(value):
    if value is None:
        return "null"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _text(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _csv(path, rows, fields):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: _cell(row.get(key)) for key in fields} for row in rows)
    temporary.replace(path)


def _markdown(rows, fields):
    def line(values):
        return "| " + " | ".join(_cell(v).replace("|", "\\|").replace("\n", "<br>")
                                  for v in values) + " |"
    return "\n".join([line(fields), line(["---"] * len(fields)),
                       *(line([row.get(key) for key in fields]) for row in rows)])


def _policy(report, policy):
    return report.get(policy + "_token_accounting", {})


def _identity(report):
    experiment = report.get("experiment", {})
    model = experiment.get("model") or {}
    return {**{kind: experiment.get(kind, {}).get("name") for kind in ("dataset", "method", "agent")},
            "model": model.get("model_id") or experiment.get("agent", {}).get("options", {}).get("model")}


def _dual_rows(report, metrics=()):
    policies = {name: _policy(report, name) for name in POLICIES}
    names = list(dict.fromkeys([*METRICS, *metrics, *(key for p in policies.values()
                                         for key in p.get("metrics", {}))]))
    rows = []
    for name in names:
        row = {"metric": name}
        for policy, accounting in policies.items():
            metric = accounting.get("metrics", {}).get(name, {})
            row.update({f"{policy}_{key}": metric.get(key)
                        for key in ("sum", "mean", "complete")})
            row[f"{policy}_reasons"] = metric.get("reasons", ["not accounted"])
            row[f"{policy}_cases_counted"] = accounting.get("cases_counted")
        row["policy_difference"] = (
            row["corrected_sum"] - row["original_sum"]
            if all(row[f"{p}_complete"] and row[f"{p}_sum"] is not None for p in POLICIES)
            else None)
        rows.append(row)
    return rows


def export_accounting(report, output):
    """Write the same exports for run/resume and independently versioned analyses."""
    summary = _dual_rows(report)
    cases = [{**{key: case.get(key) for key in ("case_id", "repo", "base_commit", "stage", "resolved", "control", "prompt_metadata", "native_final_summary")}, **row}
             for case in report["cases"] for row in _dual_rows(case)]
    fields = list(summary[0])
    write_json(output / "accounting.json", report)
    _text(output / "accounting.txt", render_accounting(report) + "\n")
    _csv(output / "accounting.csv", summary, fields)
    _csv(output / "cases.csv", cases, ["case_id", "repo", "base_commit", "stage", "resolved", "control", "prompt_metadata", "native_final_summary", *fields])
    overhead = overhead_rows(report.get("overhead"))
    overhead_fields = list(overhead[0])
    _csv(output / "overhead.csv", overhead, overhead_fields)
    _csv(output / "overhead-cases.csv", [{"case_id": case["case_id"], **row}
         for case in report["cases"] for row in overhead_rows(case.get("overhead"))],
         ["case_id", *overhead_fields])
    evaluation = report.get("evaluation") or {}
    evaluation_fields = ["selected", "known_outcomes", "resolved", "resolved_over_selected", "resolved_over_completed", "complete"]
    _csv(output / "evaluation.csv", [evaluation], evaluation_fields)
    _text(output / "accounting.md", "# Token accounting\n\n"
          + "Generation status: " + report["run_status"] + ".\n\n"
          + _markdown([_identity(report)], ["dataset", "method", "agent", "model"]) + "\n\n"
          + "policy_difference = corrected − original: accounting policy difference, not method savings.\n\n"
          + _markdown(summary, fields) + "\n\n"
          + "Accounting context: " + _cell(report.get("accounting_context")) + "\n\n"
          + report.get("coverage_note", "")
          + "\n\n## Overhead\n\n" + report.get("overhead", {}).get("note", "Overhead unavailable in legacy report.")
          + "\n\n" + _markdown(overhead, overhead_fields)
          + "\n\n## Evaluation\n\nMissing outcomes remain unknown; ratios use the stated denominators.\n\n"
          + _markdown([evaluation], evaluation_fields)
          + "\n\n[Case metrics](cases.csv) · [Raw usage references and configuration](accounting.json)\n")


def analyze_run(run, output=None):
    """Recompute using saved configuration/tasks, never the experiment TOML or dataset."""
    from .run_accounting import build_accounting

    run = Path(run).resolve()
    snapshot, state = _read(run / "config.json"), _read(run / "state.json")
    if (run / "tasks.json").is_file():
        tasks = [Task(**value) for value in _read(run / "tasks.json")]
    elif (run / "accounting.json").is_file():
        tasks = [Task(c["case_id"], c["repo"], c["base_commit"], "")
                 for c in _read(run / "accounting.json")["cases"]]
    else:
        raise ValueError("saved task identities unavailable: tasks.json or accounting.json is required")
    if len({t.instance_id for t in tasks}) != len(tasks):
        raise ValueError("duplicate saved task IDs")
    components = [load_adapter(Component(**{**snapshot[kind], "path": Path(snapshot[kind]["path"])}))
                  for kind in ("method", "agent")]
    report = build_accounting(run, state, tasks, *components)
    report["experiment"] = snapshot
    if state.get("status") == "running":
        # Includes a process killed before it could persist an interrupted state.
        for section in [report, *report["cases"]]:
            for policy in POLICIES:
                for metric in _policy(section, policy).get("metrics", {}).values():
                    metric["complete"] = False
                    metric["reasons"].append("run is marked running; artifact snapshot may be changing")
            overhead = section.get("overhead", {})
            for group in [overhead.get("original", {}), *overhead.get("corrected", {}).values()]:
                for metric in group.get("metrics", {}).values():
                    metric["complete"] = False
                    metric["reasons"].append("run is marked running; artifact snapshot may be changing")
    report["analysis"] = {"version": "analysis-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                          "note": "Recomputed from saved artifacts using the currently installed adapters."}
    target = (Path(output) if output is not None else
              run / "analysis" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).resolve()
    target.mkdir(parents=True, exist_ok=False)
    export_accounting(report, target)
    return {"output": str(target), "report": report}


def _load_report(path):
    path = Path(path).resolve()
    path = path / "accounting.json" if path.is_dir() else path
    report = _read(path)
    if (not isinstance(report, dict) or report.get("schema_version") != 1 or
            not isinstance(report.get("cases"), list)):
        raise ValueError(f"{path}: unsupported accounting report")
    if (path.parent / "state.json").is_file():
        state = _read(path.parent / "state.json")
        status = state.get("status")
        if status == "running" or status != report.get("run_status"):
            raise ValueError(f"{path}: report may be stale; stop the run and use analyze")
        latest = state.get("evaluation", {}).get("report_summary")
        if latest and _read(Path(latest)) != report.get("evaluation"):
            raise ValueError(f"{path}: evaluation changed; use analyze to refresh the report")
    if "experiment" not in report:
        report["experiment"] = _read(path.parent / "config.json")
    return report, str(path)


def _case_map(report):
    dataset = report["experiment"]["dataset"]["name"]
    result = {}
    for case in report["cases"]:
        key = (dataset, case["case_id"], case["repo"], case["base_commit"])
        if not all(isinstance(v, str) and v for v in key) or key in result:
            raise ValueError("invalid or duplicate case identity in accounting report")
        result[key] = case
    return result


def _counted(cases, policy):
    if any(_policy(c, policy).get("cases_counted") is None for c in cases.values()):
        return None
    return {key for key, c in cases.items() if _policy(c, policy)["cases_counted"] > 0}


def compare_runs(paths, output):
    if len(paths) < 2:
        raise ValueError("compare requires at least two runs; the first is the baseline")
    loaded = [_load_report(path) for path in paths]
    reports = [report for report, _ in loaded]
    case_maps = [_case_map(report) for report in reports]
    summary, cases, inputs = [], [], []
    baseline = reports[0]
    for index, (report, source) in enumerate(loaded):
        experiment = report["experiment"]
        inputs.append({"index": index, "source": source, "run": report["run"],
                       "run_status": report["run_status"], "experiment": experiment,
                       "analysis": report.get("analysis"), "accounting_context": report.get("accounting_context"),
                       "coverage_note": report.get("coverage_note"),
                       "evaluation": report.get("evaluation")})
        for policy in POLICIES:
            current, base = _policy(report, policy), _policy(baseline, policy)
            issues = []
            if set(case_maps[index]) != set(case_maps[0]):
                issues.append("selected task sets differ")
            if not current.get("rule") or current.get("rule") != base.get("rule"):
                issues.append("accounting rules differ or are unavailable")
            counted, base_counted = _counted(case_maps[index], policy), _counted(case_maps[0], policy)
            if counted is None or base_counted is None or counted != base_counted:
                issues.append("counted case sets differ or are unavailable")
            if report["run_status"] != "submission_prepared" or baseline["run_status"] != "submission_prepared":
                issues.append("generation is not complete in both runs")
            names = dict.fromkeys([*METRICS, *base.get("metrics", {}), *current.get("metrics", {})])
            for name in names:
                metric, reference = current.get("metrics", {}).get(name, {}), base.get("metrics", {}).get(name, {})
                reasons = list(issues)
                if not metric.get("complete") or not reference.get("complete"):
                    reasons.append("metric incomplete or not accounted")
                row = {"run_index": index, **_identity(report), "policy": policy, "rule": current.get("rule"),
                       "cases_selected": len(case_maps[index]), "cases_counted": current.get("cases_counted"),
                       "metric": name, "sum": metric.get("sum"), "mean": metric.get("mean"),
                       "complete": metric.get("complete"), "metric_reasons": metric.get("reasons", ["not accounted"])}
                for value in ("sum", "mean"):
                    left, right = reference.get(value), metric.get(value)
                    valid = not reasons and left is not None and right is not None
                    row[f"delta_{value}"] = right - left if valid else None
                    row[f"change_pct_{value}"] = 100 * (right - left) / left if valid and left else None
                row["comparison_reasons"] = reasons + (
                    ["zero or unknown baseline: percentage unavailable"]
                    if not reasons and any(reference.get(v) in (None, 0) for v in ("sum", "mean")) else [])
                summary.append(row)
    # Full outer alignment: absent cases stay absent; no implicit intersection or zero filling.
    for key in sorted(set().union(*(set(mapping) for mapping in case_maps))):
        metrics = {name for mapping in case_maps for policy in POLICIES
                   for name in _policy(mapping.get(key, {}), policy).get("metrics", {})}
        for index, mapping in enumerate(case_maps):
            case = mapping.get(key)
            for row in _dual_rows(case or {}, sorted(metrics)):
                cases.append({"dataset": key[0], "case_id": key[1], "repo": key[2], "base_commit": key[3],
                              "run_index": index, "present": case is not None,
                              "stage": case.get("stage") if case else None,
                              "resolved": case.get("resolved") if case else None, **row})
    overhead = [{"run_index": index, **row} for index, report in enumerate(reports)
                for row in overhead_rows(report.get("overhead"))]
    overhead_cases = [{"run_index": index, "case_id": case["case_id"], **row}
                      for index, report in enumerate(reports) for case in report["cases"]
                      for row in overhead_rows(case.get("overhead"))]
    result = {"schema_version": 1, "baseline_index": 0, "inputs": inputs,
              "overhead": overhead, "overhead_cases": overhead_cases,
              "summary": summary, "cases": cases,
              "note": "Changes compare the same policy against run 0. No evaluation success is inferred."}
    target = Path(output).resolve()
    target.mkdir(parents=True, exist_ok=False)
    write_json(target / "comparison.json", result)
    _csv(target / "summary.csv", summary, list(summary[0]))
    _csv(target / "overhead.csv", overhead, list(overhead[0]))
    _csv(target / "overhead-cases.csv", overhead_cases, ["run_index", "case_id", *list(overhead_rows(None)[0])])
    overhead_text = _markdown(overhead, list(overhead[0]))
    _text(target / "overhead.txt", overhead_text + "\n")
    _csv(target / "cases.csv", cases, ["dataset", "case_id", "repo", "base_commit", "run_index", "present",
                                       "stage", "resolved", *list(_dual_rows({})[0])])
    evaluation_rows = [{"run_index": index, **_identity(report), **{
        key: (report.get("evaluation") or {}).get(key) for key in
        ("selected", "known_outcomes", "resolved", "resolved_over_selected", "resolved_over_completed", "complete")}}
        for index, report in enumerate(reports)]
    _csv(target / "evaluation.csv", evaluation_rows, list(evaluation_rows[0]))
    _text(target / "comparison.md", "# Run comparison\n\nBaseline: run 0. Percentages require identical task and counted-case sets, "
          "matching rules, completed generation, and complete metrics.\n\n"
          "Completeness is limited to declared recording coverage; original completeness describes policy reproduction.\n\n"
          + _markdown([{"index": i["index"], "source": i["source"]} for i in inputs], ["index", "source"])
          + "\n\n" + _markdown(summary, list(summary[0]))
          + "\n\n## Overhead\n\nOverlapping groups are not additive to total consumption. Values use each run's stated denominator; no cross-rule savings inferred.\n\n" + overhead_text
          + "\n\n## Evaluation\n\n" + _markdown(evaluation_rows, list(evaluation_rows[0]))
          + "\n\n[All aligned cases](cases.csv) · [Full comparison and configuration](comparison.json)\n")
    return {"output": str(target), "runs": len(reports), "baseline": loaded[0][1],
            "overhead_text": overhead_text}
