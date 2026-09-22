"""Fixed research selections, independent of execution and accounting engines.

Only public task metadata enters the study manifest. This module neither runs
agents nor infers missing experimental results from a planned matrix.
"""

from collections import Counter
import csv
from pathlib import Path

from .components import read_toml
from .records import write_json

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("baseline", "run_free", "turn_control", "agent_diet", "attn_compress", "eet", "swe_pruner_pro")
AGENTS = ("mini", "trae", "opencode", "codex")
MODELS = ("gpt-5.6-sol", "claude-opus-5", "deepseek-v4.1-flash", "qwen3.8-max", "Qwen3-Coder-Next")
DATASETS = {"deepswe": "deepswe", "verified": "swe_bench_verified"}


def supported(method, agent, model):
    """Declared framework scope, not a claim of runtime verification."""
    return (method in METHODS and agent in AGENTS and model in MODELS
            and (method != "swe_pruner_pro" or model == "Qwen3-Coder-Next")
            and (agent != "codex" or model in ("gpt-5.6-sol", "Qwen3-Coder-Next")))


def task_catalog(dataset):
    if dataset == "deepswe":
        from datasets.deepswe.tasks import select_tasks
        return {r.task.instance_id: {"instance_id": r.task.instance_id, "repo": r.task.repo,
                "base_commit": r.task.base_commit, "language": r.language} for r in select_tasks()}
    if dataset == "verified":
        from datasets.swe_bench_verified.adapter import SweBenchVerified
        return {t.instance_id: {"instance_id": t.instance_id, "repo": t.repo,
                "base_commit": t.base_commit, "language": "python"} for t in SweBenchVerified().tasks()}
    raise ValueError(f"Unknown dataset: {dataset}")


def task_list(path):
    values = [line.strip() for line in Path(path).read_text().splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    if not values or len(values) != len(set(values)):
        raise ValueError(f"Task list must be nonempty and unique: {path}")
    return values


def configuration_id(dataset, method, agent, model):
    return "__".join((dataset, method, agent, model))


def load_study(path):
    path = Path(path).resolve()
    spec = read_toml(path)
    allowed = {"version", "name", "pricing", "datasets", "groups", "analysis", "execution", "expected"}
    if spec.keys() - allowed or spec.get("version") != 1 or not spec.get("name"):
        raise ValueError("Study requires version=1, name, and only documented fields")
    datasets = {}
    for name, item in spec.get("datasets", {}).items():
        if item.keys() - {"tasks", "count"}:
            raise ValueError(f"Unknown dataset selection fields: {name}")
        ids = task_list(path.parent / item["tasks"])
        catalog = task_catalog(name)
        if set(ids) - catalog.keys():
            raise ValueError(f"{name}: unknown task IDs in fixed list")
        if len(ids) != item.get("count", len(ids)):
            raise ValueError(f"{name}: fixed task count differs from configured count")
        datasets[name] = [catalog[i] for i in ids]
    configurations, identities, groups = [], set(), set()
    for group in spec.get("groups", []):
        if group.keys() - {"name", "dataset", "methods", "agents", "models", "baseline"}:
            raise ValueError("Unknown study group fields")
        name, dataset = group["name"], group["dataset"]
        if name in groups or dataset not in datasets:
            raise ValueError("Study group names must be unique and reference a selected dataset")
        groups.add(name)
        for dimension in ("methods", "agents", "models"):
            values = group[dimension]
            if not isinstance(values, list) or not values or len(values) != len(set(values)):
                raise ValueError(f"{name}: {dimension} must be a nonempty unique list")
        baseline = group.get("baseline", False)
        if type(baseline) is not bool or baseline != ("baseline" in group["methods"]):
            raise ValueError(f"{name}: baseline flag must agree with methods")
        for agent in group["agents"]:
            for model in group["models"]:
                for method in group["methods"]:
                    if not supported(method, agent, model):
                        raise ValueError(f"Unsupported study combination: {method}/{agent}/{model}")
                    key = configuration_id(dataset, method, agent, model)
                    if key in identities:
                        raise ValueError(f"Duplicate study configuration: {key}")
                    identities.add(key)
                    configurations.append(dict(id=key, group=name, dataset=dataset, method=method,
                        agent=agent, model=model, selected=len(datasets[dataset]),
                        baseline_id=configuration_id(dataset, "baseline", agent, model)
                                    if baseline and method != "baseline" else None))
    if not configurations:
        raise ValueError("Study must select at least one configuration")
    selected = sum(c["selected"] for c in configurations)
    expected = spec.get("expected", {})
    if (expected.get("configurations", len(configurations)) != len(configurations)
            or expected.get("task_runs", selected) != selected):
        raise ValueError("Expanded study does not match expected configuration/task-run counts")
    from .pricing import load_pricing
    prices = load_pricing(path.parent / spec["pricing"]) if spec.get("pricing") else load_pricing()
    return dict(version=1, name=spec["name"], datasets=datasets, configurations=configurations,
                selected=selected, pricing=prices, analysis=spec.get("analysis", {}),
                execution=spec.get("execution", {}))


def study_rows(study):
    """Every arm receives the same ordered task set; no per-arm next-N selection."""
    return [{**{k: c[k] for k in ("group", "dataset", "method", "agent", "model", "baseline_id")},
             "configuration_id": c["id"], "case": task["instance_id"], "language": task["language"],
             "id": c["id"] + "__" + task["instance_id"]}
            for c in study["configurations"] for task in study["datasets"][c["dataset"]]]


def export_study(path, output, *, runs=None, jobs=4, figures=True):
    """Offline matrix export. Statistical reports are added by the analysis layer."""
    study = load_study(path)
    if type(jobs) is not int or jobs < 1:
        raise ValueError('jobs must be positive')
    output = Path(output).resolve()
    rows = study_rows(study)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "study.json", study)
    with (output / "matrix.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Study selection", "", f"{study['name']}: {len(study['configurations'])} configurations, "
             f"{len(rows):,} selected task runs.", "", "| Dataset | Configurations | Task runs |",
             "| --- | ---: | ---: |"]
    for dataset in study["datasets"]:
        arms = [c for c in study["configurations"] if c["dataset"] == dataset]
        lines.append(f"| {dataset} | {len(arms)} | {sum(c['selected'] for c in arms)} |")
    lines += ["", "Status: selection only; no agents, services or evaluators were executed.",
              "Actual model IDs, agent/method options and runtime settings are frozen on execution.",
              "This export contains no measured statistics, savings or rankings."]
    (output / "study.md").write_text("\n".join(lines) + "\n")
    result = dict(status="selection_only", output=str(output), configurations=len(study["configurations"]),
                  selected=len(rows), dataset_task_runs=dict(Counter(r["dataset"] for r in rows)))
    if runs is not None:
        from .study_report import build_report
        result.update(build_report(study, rows, runs, output, jobs=jobs, figures=figures))
    return result
