"""Shared tabular cost exports for ordinary runs, pilots and comparisons."""
from .pricing import decimal, money, render_cost


def token_ratios(accounting):
    metrics = (accounting or {}).get('metrics', {})
    result = {}
    for name, top, bottom in (('cache_hit_rate', 'cache_read', 'input'),
                              ('reasoning_share', 'reasoning', 'output')):
        a, b = metrics.get(top, {}), metrics.get(bottom, {})
        complete = bool(a.get('complete') and b.get('complete') and b.get('sum'))
        result[name] = dict(value=a['sum'] / b['sum'] if complete else None,
                            complete=complete, reasons=[] if complete else ['missing/incomplete counts or zero denominator'])
    return result


def cost_rows(cost, identity=None):
    requests, lines = [], []
    for request in (cost or {}).get('requests', []):
        row = {**(identity or {}), **{key: request.get(key) for key in (
            'case_id', 'attempt_id', 'call_id', 'operation_id', 'observation_id', 'purpose', 'provider',
            'model', 'price_model', 'price_region', 'price_source', 'context_tier', 'source',
            'total_usd', 'known_subtotal_usd', 'complete', 'reasons')}}
        row.update({key: request.get('billing_context', {}).get(key) for key in ('status', 'started_at', 'finished_at')})
        row.update(request_complete=request['complete'], request_reasons=request['reasons'])
        requests.append({**row, **{'tokens_' + k: v for k, v in request['metrics'].items()},
                         'raw_usage': request['raw_usage']})
        lines.extend({**row, **{k: v for k, v in line.items() if k != 'calculation'}} for line in request['line_items'])
    return requests, lines


def export_cost(cost, output, identity=None):
    from .tabular import legacy_csv as _csv
    requests, lines = cost_rows(cost, identity)
    for name, rows in (('requests', requests), ('costs', lines)):
        fields = list(dict.fromkeys(key for row in rows for key in row)) or ['case_id', 'total_usd', 'complete', 'reasons']
        _csv(output / (name + '.csv'), rows, fields)


def cost_markdown(cost):
    from .tabular import markdown as _markdown
    if not cost:
        return render_cost(None)
    rows = []
    for group, values in cost.get('groups', {}).items():
        for name, value in values.items():
            rows.append({'group': group, 'name': name, **{key: value.get(key) for key in (
                'total_usd', 'known_subtotal_usd', 'mean_usd', 'complete')}})
    return render_cost(cost) + ('\n\n' + _markdown(rows, list(rows[0])) if rows else '')


def compare_costs(reports, case_maps):
    base = reports[0].get('corrected_v2_cost')
    rows = []
    for index, report in enumerate(reports):
        cost = report.get('corrected_v2_cost')
        reasons = []
        if not cost or not base:
            reasons.append('legacy report without cost accounting')
        else:
            if cost['pricing'] != base['pricing'] or cost['rule'] != base['rule']:
                reasons.append('pricing configuration or cost rules differ')
            if not cost['complete'] or not base['complete']:
                reasons.append('cost incomplete')
        if set(case_maps[index]) != set(case_maps[0]):
            reasons.append('selected task sets differ')
        if any(r['run_status'] != 'submission_prepared' for r in (report, reports[0])):
            reasons.append('generation is not complete in both runs')
        value, reference = (cost or {}).get('total_usd'), (base or {}).get('total_usd')
        delta = decimal(value) - decimal(reference) if not reasons and value is not None and reference is not None else None
        rows.append(dict(run_index=index, total_usd=value, known_subtotal_usd=(cost or {}).get('known_subtotal_usd'),
            complete=bool(cost and cost['complete']), delta_usd=money(delta),
            change_pct=money(delta / decimal(reference) * 100) if delta is not None and decimal(reference) else None,
            comparison_reasons=reasons + (['zero baseline; percentage unavailable'] if delta is not None and not decimal(reference) else [])))
    return rows
