"""Usage attribution and disjoint overhead projections; no text-token estimates."""

from dataclasses import asdict
import math
from typing import Mapping

from .accounting import METRICS, corrected_accounting

PURPOSES = ('main', 'agent_auxiliary', 'method_auxiliary', 'compression_service', 'unknown')
GROUPS = {**{p: {p} for p in PURPOSES},
          'method_overhead': {'method_auxiliary', 'compression_service'},
          'all': set(PURPOSES)}


def validate_attribution(value):
    if not isinstance(value, Mapping):
        raise ValueError('attribution must be an object')
    allowed = {'purpose', 'case_id', 'attempt_id', 'call_id', 'parent_call_id'}
    if set(value) - allowed:
        raise ValueError('unknown attribution field')
    if value.get('purpose', 'unknown') not in PURPOSES:
        raise ValueError('invalid call purpose')
    if any(not isinstance(v, str) or not v for v in value.values()):
        raise ValueError('attribution fields must be nonempty strings')
    return dict(value)


def duration(metadata):
    value = metadata.get('duration_sec')
    if value is None:
        start, end = metadata.get('started_at'), metadata.get('finished_at')
        if type(start) in (int, float) and type(end) in (int, float):
            value = end - start
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _metric(values, denominator, reasons):
    known = sum(values) if values else (None if reasons else 0)
    return {'sum': known, 'mean': known / denominator if known is not None and denominator else None,
            'complete': not reasons, 'reasons': list(dict.fromkeys(reasons))}


def overhead_accounting(cases, author=None):
    # Reuse all token validation, including conflicting identities and total semantics.
    total = corrected_accounting(cases)
    denominator = total['cases_counted']
    observations, operations = {}, {}
    gaps = []
    for case in cases:
        if not case.coverage_complete or case.llm_called is None or case.issues:
            gaps.extend([f'{case.case_id}: incomplete recording coverage', *case.issues])
        for item in case.observations:
            key = (item.case_id, item.attempt_id, item.call_id, item.observation_id)
            observations.setdefault(key, item)
        for op in case.operations:
            if op.case_id != case.case_id or not all((op.attempt_id, op.call_id, op.operation_id)):
                raise ValueError('invalid operation identity')
            if op.purpose not in PURPOSES or op.kind not in ('llm', 'service'):
                raise ValueError('invalid operation purpose or kind')
            if op.duration_sec is not None and duration({'duration_sec': op.duration_sec}) is None:
                raise ValueError('invalid operation duration')
            key = (op.case_id, op.attempt_id, op.call_id, op.operation_id)
            if key in operations and operations[key] != op:
                raise ValueError('conflicting operation identity')
            operations[key] = op
        if case.llm_called is True and not case.operations:
            gaps.append(f'{case.case_id}: operation records unavailable')
    for item in observations.values():
        if item.purpose not in PURPOSES:
            raise ValueError('invalid observation purpose')
        op = operations.get((item.case_id, item.attempt_id, item.call_id, item.operation_id))
        if op and (item.purpose, item.model, item.parent_call_id) != (op.purpose, op.model, op.parent_call_id):
            raise ValueError('usage and operation attribution disagree')
    unclassified = any(i.purpose == 'unknown' for i in [*observations.values(), *operations.values()])
    groups = {}
    names = list(total['metrics'])
    for group, purposes in GROUPS.items():
        selected = [i for i in observations.values() if i.purpose in purposes]
        ops = [i for i in operations.values() if i.purpose in purposes]
        reasons = list(gaps)
        if unclassified and group not in ('all', 'unknown'):
            reasons.append('unclassified requests may belong to this group')
        metrics = {}
        for name in names:
            missing = [f'{i.source}: {name} unknown' for i in selected if i.metrics.get(name) is None]
            missing += [f'{i.source}: {i.issues[name]}' for i in selected if name in i.issues]
            # An HTTP failure with no usage must not appear as zero consumption.
            linked = {(i.case_id, i.attempt_id, i.call_id, i.operation_id) for i in selected}
            missing += [f'{i.operation_id}: usage unavailable' for i in ops
                        if i.kind == 'llm' and (i.case_id, i.attempt_id, i.call_id, i.operation_id) not in linked]
            metrics[name] = _metric([i.metrics[name] for i in selected if i.metrics.get(name) is not None],
                                    denominator, reasons + missing)
        # For total tokens the canonical corrected projection remains authoritative.
        if group == 'all':
            metrics = {k: dict(v) for k, v in total['metrics'].items()}
        metrics['calls'] = _metric([1 for _ in ops], denominator, reasons)
        metrics['service_seconds'] = _metric([i.duration_sec for i in ops if i.duration_sec is not None],
            denominator, reasons + [f'{i.operation_id}: duration unknown' for i in ops if i.duration_sec is None]
            + [f'{i.operation_id}: incomplete operation' for i in ops if not i.complete]
            + [reason for i in ops for reason in i.issues])
        groups[group] = {'cases_counted': denominator, 'metrics': metrics}
    unknown = {'rule': None, 'cases_counted': None, 'metrics': {
        name: _metric([], 0, ['author overhead not provided by this method'])
        for name in (*METRICS, 'calls', 'service_seconds')}}
    return {'rule': 'overhead-v1', 'cases_counted': denominator,
            'original': author if author is not None else unknown,
            'corrected': groups,
            'operations': [asdict(op) for op in operations.values()],
            'note': 'Groups overlap: method_overhead is method_auxiliary + compression_service; all already includes overhead. Calls count HTTP attempts and service operations, not agent turns. Durations are summed operation elapsed times, not wall-clock runtime. Unknown attribution is not zero overhead.'}


def overhead_rows(overhead):
    if not overhead:
        unknown = {'cases_counted': None, 'metrics': {name: _metric([], 0,
            ['legacy report has no overhead records; analyze saved artifacts'])
            for name in (*METRICS, 'calls', 'service_seconds')}}
        overhead = {'original': unknown, 'corrected': {name: unknown for name in GROUPS}}
    rows = []
    for policy, groups in (('original', {'author_overhead': overhead['original']}),
                           ('corrected', overhead['corrected'])):
        for group, section in groups.items():
            for name, metric in section.get('metrics', {}).items():
                rows.append({'policy': policy, 'group': group, 'metric': name,
                             'cases_counted': section.get('cases_counted'), **metric})
    return rows


def render_overhead(overhead):
    lines = ['Overhead (included in corrected totals)', overhead['note']]
    for row in overhead_rows(overhead):
        lines.append(f"{row['policy']}/{row['group']}/{row['metric']} | sum={row['sum']}, mean={row['mean']}, "
                     f"complete={row['complete']}" + (' (' + '; '.join(row['reasons']) + ')' if row['reasons'] else ''))
    return '\n'.join(lines)
