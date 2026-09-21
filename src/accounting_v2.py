"""Fixed-denominator API generation accounting, independent of original/v1."""
from dataclasses import asdict, replace
from decimal import Decimal
import json
from pathlib import Path

from .accounting import METRICS, _validated_observations
from .pricing import cost_accounting, money


def canonical_cases(cases):
    """Use the same response identity for tokens, tariffs and request exports."""
    _, by_case = _validated_observations(cases, full_evidence=True)
    result = []
    for case in cases:
        operations = {}
        for op in case.operations:
            key = (op.attempt_id, op.call_id, op.operation_id)
            if op.case_id != case.case_id or (key in operations and operations[key] != op):
                raise ValueError('Conflicting operation evidence')
            operations[key] = op
        result.append(replace(case, observations=list(by_case[case.case_id].values()), operations=list(operations.values())))
    return result


def corrected_v2(cases):
    cases = canonical_cases(cases)
    selected = len(cases)
    observations = [o for case in cases for o in case.observations]
    result = {}
    names = list(dict.fromkeys([*METRICS, *(k for o in observations for k in o.metrics)]))
    for metric in names:
        values, reasons = [], []
        for case in cases:
            items = case.observations
            if case.llm_called is None:
                reasons.append(f'{case.case_id}: call coverage unknown')
            if not case.coverage_complete:
                reasons.extend(case.issues or [f'{case.case_id}: incomplete coverage'])
            reasons.extend(case.issues)
            if case.llm_called is True and not items:
                reasons.append(f'{case.case_id}: forwarded request has no usage')
            linked = {(o.attempt_id, o.call_id, o.operation_id) for o in items}
            for op in case.operations:
                if not op.inference:
                    continue
                if (op.attempt_id, op.call_id, op.operation_id) not in linked:
                    reasons.append(f'{case.case_id}/{op.operation_id}: forwarded request has no usage')
                if not op.complete or op.issues:
                    reasons.extend(op.issues or [f'{case.case_id}/{op.operation_id}: incomplete operation'])
            for item in items:
                value = item.metrics.get(metric)
                if value is None or item.issues.get(metric):
                    reasons.append(f'{case.case_id}/{item.observation_id}: {metric} unknown')
                else:
                    values.append(value)
        complete = not reasons
        subtotal = sum(values) if values or complete else None
        total = subtotal if complete else None
        result[metric] = dict(sum=total, known_subtotal=subtotal, complete=complete,
                              mean=total / selected if total is not None and selected else None,
                              reasons=list(dict.fromkeys(reasons)))
    return dict(rule='corrected-v2-api', cases_counted=selected, denominator='selected',
                model_called=sum(c.llm_called is True for c in cases), metrics=result,
                note='Actual generation API usage, all attempts; local compression/pruning is separate. '
                     'Cache and reasoning are subsets, not additional total tokens.',
                observations=[asdict(o) for o in observations])


def api_cost(cases, pricing):
    cases = canonical_cases(cases)
    result = cost_accounting([asdict(c) for c in cases], pricing)
    result.update(rule='corrected-v2-api-list-price', cases_counted=len(cases), denominator='selected')
    result['mean_usd'] = money(Decimal(result['total_usd']) / len(cases)) if result['total_usd'] is not None and cases else None
    # Counterfactual tariff only: cached requests/latencies remain the observed ones.
    if pricing is not None:
        import copy
        uncached = copy.deepcopy(pricing)
        for profile in uncached['models']:
            profile['rule'] = 'cache_read'
            for key in list(profile):
                if key.startswith('cache_'):
                    profile[key] = profile['input']
        usages = []
        for case in cases:
            item = asdict(case)
            for obs in item['observations']:
                obs['metrics'].update(cache_read=0, cache_write=0)
                obs['metrics'].pop('cache_miss', None)
                obs['metrics'].pop('ordinary_input', None)
            usages.append(item)
        sensitivity = cost_accounting(usages, uncached)
        result['no_cache_discount'] = dict(total_usd=sensitivity['total_usd'], complete=sensitivity['complete'],
            reasons=sensitivity['reasons'], note='Offline tariff sensitivity; not an actual cold-cache run.')
    return result


def restore_cases(values):
    from .accounting import CaseUsage, UsageObservation, UsageOperation
    return [CaseUsage(**{**v, 'observations': [UsageObservation(**o) for o in v.get('observations', [])],
                        'operations': [UsageOperation(**o) for o in v.get('operations', [])]}) for v in values]


def request_rows(cases):
    """Include prepared requests and failed HTTP attempts without inventing usage."""
    rows = []
    for case in canonical_cases(cases):
        linked = set()
        ops = {(o.attempt_id, o.call_id, o.operation_id): o for o in case.operations}
        for item in case.observations:
            key = (item.attempt_id, item.call_id, item.operation_id)
            linked.add(key)
            op = ops.get(key)
            status = item.billing_context.get('status')
            parameters = None
            request = Path(item.source).with_name('request.body')
            try:
                payload = json.loads(request.read_text())
                parameters = {k: payload[k] for k in ('model', 'temperature', 'top_p', 'top_k', 'seed',
                    'max_tokens', 'max_output_tokens', 'max_completion_tokens', 'reasoning_effort',
                    'reasoning', 'stream', 'parallel_tool_calls') if k in payload}
            except (OSError, ValueError):
                pass
            rows.append({**asdict(item), 'http_status': status,
                'generation_parameters': parameters,
                'outcome': 'success' if isinstance(status, int) and 200 <= status < 300 else
                           'http_error' if isinstance(status, int) else 'unknown',
                'duration_sec': op.duration_sec if op else None,
                'request_state': 'forwarded', **item.metrics})
        for key, op in ops.items():
            if key not in linked:
                rows.append({**asdict(op), 'observation_id': None, 'source': op.billing_context.get('metadata_source'),
                    'request_state': 'forwarded' if op.inference else 'prepared',
                    'outcome': 'usage_unknown' if op.inference else 'not_forwarded',
                    **dict.fromkeys(METRICS)})
    return rows
