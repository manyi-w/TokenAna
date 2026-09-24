"""Rebuild run accounting from persisted attempts, without executing components."""

from dataclasses import asdict, replace
import json
from pathlib import Path

from .accounting import METRICS, CaseUsage, OriginalCase
from .overhead import overhead_accounting


def original_supported(method, agent):
    formats = {getattr(agent, "original_trace_format", None),
               *getattr(agent, "original_compatible_trace_formats", ())}
    return (bool(formats.intersection(getattr(method, "original_trace_formats", ()))) and
            callable(getattr(agent, getattr(method, "original_reader", "read_original_case"), None)) and
            callable(getattr(method, "original_accounting", None)))


def _unknown(case_id, reason):
    return CaseUsage(case_id, None, coverage_complete=False, issues=(reason,))


def _original_report(method, originals, supported, issues=(), variant=None):
    if not supported:
        return {"rule": None, "cases_counted": None,
                "metrics": {name: {"sum": None, "mean": None, "complete": False,
                                   "reasons": ["original accounting is unavailable for this combination"]}
                            for name in METRICS}}
    report = method.original_accounting(originals)
    if variant:
        report["rule"] += "+" + variant
        report["note"] += " Compatible native-agent log projection; not historical author results."
    if issues:
        for metric in report["metrics"].values():
            metric["complete"] = False
            metric["reasons"] += list(issues)
    return report


def _artifact_directories(directory):
    """Include orphaned directories after interruption or damaged journals."""
    known = {}
    issues = []
    path = directory / "calls.json"
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(journal, dict) or not isinstance(journal.get("calls"), list):
            raise ValueError("invalid call journal")
        for call in journal["calls"]:
            if (not isinstance(call["id"], str) or not call["id"] or
                    not isinstance(call["artifacts"], list)):
                raise ValueError("invalid call record")
            if not call["artifacts"]:
                issues.append(f"{directory.name}/{call['id']}: no recorded artifacts")
            for relative in call["artifacts"]:
                if not isinstance(relative, str) or Path(relative).is_absolute():
                    raise ValueError("artifact must be a relative path")
                artifact = (directory / relative).resolve()
                artifact.relative_to(directory.resolve())
                if artifact in known:
                    raise ValueError("artifact belongs to multiple calls")
                known[artifact] = call["id"]
    except (OSError, ValueError, TypeError, KeyError) as error:
        issues.append(f"{directory.name}: call journal unavailable ({type(error).__name__})")
    for artifact in sorted((directory / "artifacts").glob("call-*")):
        if artifact.is_dir() and artifact.resolve() not in known:
            known[artifact.resolve()] = artifact.name
            issues.append(f"{directory.name}/{artifact.name}: artifact has no call association")
    return known, issues


def build_accounting(output, state, tasks, method, agent, *, pricing=None):
    output = Path(output).resolve()
    originals, api_usages, details = [], [], []
    original_issues, local_services = [], []
    supported = original_supported(method, agent)
    variant = getattr(agent, "original_accounting_variant", None)
    for task in tasks:
        saved = state["tasks"].get(task.instance_id, {})
        api_pieces, local_compute, selected, task_local_services = [], [], [], []
        attempts = list(dict.fromkeys(saved.get("attempts", [])))
        for relative in attempts:
            directory = output / relative
            artifacts, issues = _artifact_directories(directory)
            if not artifacts and not (directory / "calls.json").exists():
                # An old run's preparing stage precedes any method/agent execution.
                if saved.get("directory") == relative and saved.get("stage") == "preparing":
                    issues = []
            for artifact, call_id in artifacts.items():
                from .raw_usage import read_raw_usage
                from .service_usage import read_services
                identity = dict(case_id=task.instance_id, attempt_id=directory.name,
                                call_id=f'{call_id}/{artifact.name}')
                reader = getattr(agent, "read_case_usage", None)
                api_pieces.append(reader(artifact, **identity) if reader else
                                  read_raw_usage(artifact / 'api-records', **identity))
                for channel in sorted((artifact / "auxiliary-records").glob("*")):
                    if channel.is_dir():
                        api_pieces.append(read_raw_usage(channel, **{**identity,
                            'call_id': f'{identity["call_id"]}/aux/{channel.name}'}))
                services = read_services(artifact / "service-records", case_id=task.instance_id,
                    attempt_id=directory.name, call_id=f"{call_id}/{artifact.name}/services",
                    local_compute=local_compute)
                task_local_services.extend(o for o in services.operations if o.purpose == 'compression_service')
                if any(o.purpose == 'unknown' for o in services.operations):
                    api_pieces.append(_unknown(task.instance_id, 'Service purpose unknown; generation coverage cannot be established'))
                if services.operations or services.issues:
                    # Purpose, not HTTP transport or record directory, determines
                    # whether self-hosted generation belongs in API accounting.
                    generation_ops = [o for o in services.operations if o.purpose in ('main', 'agent_auxiliary', 'method_auxiliary')]
                    generation_usage = [o for o in services.observations if o.purpose in ('main', 'agent_auxiliary', 'method_auxiliary')]
                    if generation_ops or generation_usage:
                        api_pieces.append(CaseUsage(task.instance_id, True, generation_usage,
                            all(o.complete and not o.issues for o in generation_ops),
                            [reason for o in generation_ops for reason in o.issues], generation_ops))
            if issues:
                api_pieces.append(_unknown(task.instance_id, '; '.join(issues)))
            if saved.get("directory") == relative:
                selected = list(artifacts)

        api_called = (True if any(p.llm_called is True for p in api_pieces) else
                      None if not attempts or any(p.llm_called is None for p in api_pieces) else False)
        api_usage = CaseUsage(task.instance_id, api_called,
            [o for p in api_pieces for o in p.observations],
            bool(attempts) and all(p.coverage_complete for p in api_pieces),
            [s for p in api_pieces for s in p.issues], [o for p in api_pieces for o in p.operations])
        api_usages.append(api_usage)
        local_services.extend(task_local_services)
        original = OriginalCase(task.instance_id, None, None)
        if supported and len(selected) == 1:
            try:
                reader = getattr(agent, getattr(method, "original_reader", "read_original_case"))
                original = reader(selected[0], task.instance_id)
                enrich = getattr(method, "enrich_original", None)
                if callable(enrich):
                    original = enrich(original, selected[0])
                original = replace(original, method_data={**original.method_data,
                    '_source': original.method_data.get('_source', str(selected[0] / 'method-state.json')),
                    '_sources': {**original.method_data.get('_sources', {}),
                        'case_id': {'source': str(output / 'tasks.json'), 'locator': 'instance_id=' + task.instance_id},
                        'source_error': {'source': str(selected[0] / 'method-state.json'), 'locator': '/source_error'}}})
            except (OSError, ValueError) as error:
                original_issues.append(f"{task.instance_id}: original files unreadable ({type(error).__name__})")
        elif supported and len(selected) > 1:
            original_issues.append(f"{task.instance_id}: no unique original call selection")
        prompt_metadata = None
        if len(selected) == 1:
            path = selected[0] / "prompt-metadata.json"
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    prompt_metadata = value
            except (OSError, ValueError):
                pass  # Old/missing metadata stays unknown; it does not alter token formulas.
        originals.append(original)
        diagnostic_summary = original.final_summary
        if diagnostic_summary is None and len(selected) == 1 and callable(getattr(agent, 'read_final_summary_case', None)):
            try:
                diagnostic_summary = agent.read_final_summary_case(selected[0], task.instance_id).final_summary
            except (OSError, ValueError, KeyError, TypeError):
                pass
        details.append({"case_id": task.instance_id, "repo": task.repo,
                        "agent_error": saved.get('agent_error'),
                        "base_commit": task.base_commit, "stage": saved.get("stage", "not_started"),
                        "original_attempt": saved.get("directory"),
                        "prompt_metadata": prompt_metadata,
                        "native_final_summary": asdict(diagnostic_summary) if diagnostic_summary else None,
                        "original_artifacts": [str(p.relative_to(output)) for p in selected],
                        "original_token_accounting": _original_report(
                            method, [original], supported,
                            [issue for issue in original_issues if issue.startswith(task.instance_id + ":")],
                            variant=variant)})
        details[-1]["overhead"] = overhead_accounting([api_usage],
            details[-1]["original_token_accounting"].get("overhead"), local_operations=task_local_services)
        from .accounting_v2 import corrected_v2
        details[-1].update(corrected_v2_api=corrected_v2([api_usage], selection_source=str(output / "tasks.json")), api_usage=asdict(api_usage),
                          local_compute=local_compute, attempted=bool(attempts),
                          model_called=api_called, method_state=dict(original.method_data))
        from .research_evidence import native_evidence, normalized_native
        details[-1]['original_evidence'] = native_evidence(original)
        details[-1]['normalized_native'] = normalized_native(selected, api_usage, original)
        from .telemetry import intervals
        details[-1]['timing'] = intervals(output / 'tasks' / task.instance_id)

    original_report = _original_report(method, originals, supported, original_issues, variant=variant)
    evaluation = None
    summary = state.get("evaluation", {}).get("report_summary")
    if summary:
        evaluation = json.loads(Path(summary).read_text(encoding="utf-8"))
    for detail in details:
        saved = state["tasks"].get(detail["case_id"], {})
        detail["resolved"] = saved.get("resolved")
        detail["control"] = None
        if saved.get("directory"):
            generation = output / saved["directory"] / "generation.json"
            if generation.exists():
                try:
                    payload = json.loads(generation.read_text())
                    calls = payload.get("calls", [])
                    if len(calls) == 1:
                        detail["control"] = calls[0].get("control")
                except (OSError, ValueError, TypeError, AttributeError):
                    detail["control_error"] = "generation metadata unreadable"
    report = {"schema_version": 2, "run": str(output), "run_status": state["status"],
            "accounting_context": {
                "method_version": state.get("method_version", "legacy-unversioned"),
                "original_agent_variant": variant or getattr(agent, "original_trace_format", None),
                "native_summary_sources": sorted({d["native_final_summary"]["source"] for d in details
                                                   if d["native_final_summary"]}),
                "patch_rules": sorted({d["prompt_metadata"]["patch_rule"] for d in details
                                      if d["prompt_metadata"] and isinstance(d["prompt_metadata"].get("patch_rule"), str)}),
                "note": "Saved prompt/patch conventions and native compatibility; no runtime verification implied.",
            },
            "original_token_accounting": original_report,
            "cases": details,
            "overhead": overhead_accounting(api_usages, original_report.get("overhead"), local_operations=local_services),
            "accounting_preflight": state.get("accounting_preflight"),
            "usage_normalization": "http-protocols-v1",
            "evaluation": evaluation,
            "coverage_note": "Recorded HTTP requests only, using each record's explicit protocol (legacy: Responses); unobserved API paths and sessions are not verified."}
    from .pricing import saved_pricing
    prices = pricing if pricing is not None else saved_pricing(output)
    from .accounting_v2 import corrected_v2, api_cost
    report['corrected_v2_api'] = corrected_v2(api_usages, selection_source=str(output / "tasks.json"))
    report['corrected_v2_cost'] = api_cost(api_usages, prices)
    from .telemetry import intervals
    report['timing'] = intervals(output)
    for detail, usage in zip(details, api_usages):
        detail['corrected_v2_cost'] = api_cost([usage], prices)
    if state.get('status') == 'running':
        for section in [report, *details]:
            for metric in section['corrected_v2_api']['metrics'].values():
                metric.update(sum=None, mean=None, complete=False)
                metric['reasons'].append('active run; evidence may change')
            for cost in (section['corrected_v2_cost'], section['corrected_v2_cost'].get('no_cache_discount', {})):
                cost.update(total_usd=None, complete=False)
                cost.setdefault('reasons', []).append('active run; evidence may change')
    from .research_evidence import policy_bridge
    config_path = output / 'config.json'
    method_name = json.loads(config_path.read_text())['method']['name'] if config_path.exists() else type(method).__name__
    report['method_name'] = method_name
    report['policy_bridge'] = policy_bridge(details, original_report, report['corrected_v2_api'],
                                            method_name)
    return report


def save_accounting(output, state, tasks, method, agent):
    from .analysis import export_accounting

    report = build_accounting(output, state, tasks, method, agent)
    report["experiment"] = json.loads((output / "config.json").read_text(encoding="utf-8"))
    export_accounting(report, output)
    state["accounting"] = {"status": "saved", "report": "accounting.json", "text": "accounting.txt",
                           "csv": "accounting.csv", "cases": "cases.csv", "markdown": "accounting.md",
                           "evaluation": "evaluation.csv"}
    state["accounting"].update(overhead="overhead.csv", overhead_cases="overhead-cases.csv")
    return report
