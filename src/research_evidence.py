"""Method-rule replay and conservative, evidence-backed policy bridges."""
from dataclasses import asdict, replace
import json
from pathlib import Path

from .accounting import OriginalCase
from .accounting_trace import atom, calc, constant, missing, expression
from .components import Component, load_component
from .loading import load_adapter
from .run_accounting import _artifact_directories, _original_report, original_supported


def native_evidence(case):
    # Native readers already project usage only; never duplicate complete prompts/patches.
    return dict(case_id=case.case_id, trace=case.trace, patch_present=bool(case.patch and case.patch.strip()),
                final_summary=asdict(case.final_summary) if case.final_summary else None,
                method_data=dict(case.method_data))


def restore_original(value):
    from .accounting import FinalSummary
    return OriginalCase(value['case_id'], value.get('trace'), 'present' if value.get('patch_present') else '',
        FinalSummary(**value['final_summary']) if value.get('final_summary') else None, value.get('method_data', {}))


def baseline_projection(run, method_name, task_id):
    """Replay each method's original reader/filter/rounding on the same baseline."""
    run = Path(run)
    config = json.loads((run / 'config.json').read_text())
    state = json.loads((run / 'state.json').read_text())
    component = config['agent']
    agent = load_adapter(Component(**{**component, 'path': Path(component['path'])}))
    method = load_adapter(load_component(Path(__file__).resolve().parents[1] / 'methods' / method_name, 'method', {}))
    saved = state['tasks'].get(task_id, {})
    case, issues = OriginalCase(task_id, None, None), []
    if saved.get('directory'):
        artifacts, issues = _artifact_directories(run / saved['directory'])
        if len(artifacts) == 1:
            reader = getattr(agent, getattr(method, 'original_reader', 'read_original_case'), None)
            if reader:
                case = reader(next(iter(artifacts)), task_id)
        elif artifacts:
            issues.append('baseline has no unique native call selection')
    # Baseline has no method auxiliary inference; source-specific main error
    # compensation still requires evidence. Do not infer an APIStatusError from exit code.
    if method_name == 'agent_diet':
        case = replace(case, method_data={'source_error': 'APIStatusError' if
                       'APIStatusError' in str(saved.get('agent_error', '')) else None})
    report = _original_report(method, [case], original_supported(method, agent), issues,
                              getattr(agent, 'original_accounting_variant', None))
    if method_name == 'attn_compress':
        # Baseline has no manager prompt-pool evidence for the author's cache estimate.
        report['metrics']['cache_read'].update(sum=None, mean=None, complete=False,
            reasons=['baseline has no original AttnCompress prompt-pool cache estimate'])
    return report, native_evidence(case)


def normalized_native(artifacts, api_usage, original):
    """Join native response IDs to raw HTTP usage; no totals-based guessed joins."""
    ids = set()
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('usage'), dict) and isinstance(value.get('id'), str):
                ids.add(value['id'])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for artifact in artifacts:
        path = artifact / 'original-trajectory.json'
        if not path.is_file():
            path = artifact / 'trajectory.json'
        if path.is_file():
            try:
                visit(json.loads(path.read_text()))
            except (ValueError, OSError):
                pass
    observations = [o for o in api_usage.observations if o.purpose == 'main'
                    and o.observation_id.rsplit('/', 1)[-1] in ids]
    expected = sum(e.get('type') == 'turn.completed' for e in original.trace or [])
    complete = (bool(ids) and len(observations) == len(ids) == expected
                and {o.observation_id.rsplit('/', 1)[-1] for o in observations} == ids)
    calculations = {name: calc('guard', calc('sum', *(o.calculation.get(name) or
        atom(o.metrics.get(name), o.source, '/normalized/' + name) for o in observations)),
        constant(complete and all(o.metrics.get(name) is not None and not o.issues.get(name) for o in observations),
                 'src/research_evidence.py:normalized_native', '原响应 ID 与 HTTP 完整一一对应，且指标无冲突'))
        for name in ('input', 'output', 'total', 'ordinary_input', 'cache_read', 'cache_write', 'reasoning')}
    return {'metrics': {name: node['value'] for name, node in calculations.items()}, 'calculation': calculations,
            'identities': [[o.attempt_id, o.call_id, o.observation_id] for o in observations],
            'sources': [o.source for o in observations],
            'reason': None if complete else 'No complete one-to-one native-response-ID to HTTP-usage mapping; normalization cannot be isolated.'}


def policy_bridge(details, original, corrected, method):
    """Ordered changes; unresolved links remain null, never assigned to a guessed cause."""
    stages = []
    selected = [d for d in details if d.get('original_token_accounting', {}).get('cases_counted', 0)]
    for metric, final in corrected['metrics'].items():
        old = original.get('metrics', {}).get(metric, {})
        count = original.get('cases_counted')
        def summary(name, node, denominator, reason=None, native_rounding=True):
            mean = calc('floor_divide' if method in ('run_free', 'run_free_multilingual') and native_rounding else 'divide',
                node, constant(denominator, 'src/research_evidence.py:policy_bridge', '当前阶段的任务分母'))
            if method == 'turn_control' and native_rounding:
                mean = missing('原 turn_control 未定义均值')
            return dict(stage=name, metric=metric, sum=node['value'], denominator=denominator, mean=mean['value'],
                        complete=node['value'] is not None, reason=reason, calculation={'sum': node, 'mean': mean})
        def native_sum(items):
            return calc('sum', *(d.get('normalized_native', {}).get('calculation', {}).get(metric) or
                missing('无法隔离同一原生响应的字段规范化') for d in items), description='当前纳入任务的同调用规范化结果相加')
        original_node = expression(old) if old.get('complete') else missing('原指标证据不完整')
        rows = [summary('original', original_node, count),
                summary('field_normalization', native_sum(selected), count,
                        'Requires one-to-one native response mapping; includes cache/reasoning normalization.'),
                summary('task_selection', native_sum(details), len(details),
                        'All selected tasks, retaining the native call scope; missing native evidence remains unknown.'),
                summary('call_coverage', expression(final), len(details),
                        'All forwarded main/agent auxiliary/method auxiliary attempts, with response deduplication.'),
                summary('aggregation', expression(final), len(details), native_rounding=False)]
        rows[0]['mean'] = old.get('mean')
        rows[0]['calculation']['mean'] = expression(old, 'mean')
        for index, row in enumerate(rows):
            previous = rows[index - 1]['calculation']['sum'] if index else missing('第一个阶段没有前一阶段')
            delta = calc('subtract', row['calculation']['sum'], previous, description='相邻口径阶段差额；有交互，不代表因果')
            row['delta_from_previous'] = delta['value']
            row['calculation']['delta_from_previous'] = delta
            row['included_tasks'] = [d['case_id'] for d in (selected if index < 2 else details)]
            row['note'] = 'Ordered, interacting policy changes; unknown bridges are not zero effects or causal attribution.'
        stages.extend(rows)
    return stages
