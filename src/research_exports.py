"""Original and full v2 exports."""
from .accounting_v2 import request_rows, restore_cases
from .records import write_json


def export_v2(report, output):
    from .tabular import legacy_csv as _csv, markdown as _markdown
    rows = []
    for section in [report, *report['cases']]:
        identity = section.get('case_id', '__all__')
        for name, key in (('original', 'original_token_accounting'),
                          ('corrected-v2-api', 'corrected_v2_api')):
            policy = section.get(key, {})
            for metric, value in policy.get('metrics', {}).items():
                rows.append(dict(case_id=identity, policy=name, rule=policy.get('rule'), metric=metric,
                    denominator=policy.get('cases_counted'), **value))
    fields = list(dict.fromkeys(k for row in rows for k in row))
    _csv(output / 'accounting-v2.csv', rows, fields)
    (output / 'accounting-v2.md').write_text('# Accounting policies\n\n'
        'Original completeness means reproduction of that policy, not complete API coverage.\n\n'
        + _markdown([r for r in rows if r['case_id'] == '__all__'], fields) + '\n')
    usages = restore_cases([c['api_usage'] for c in report['cases'] if c.get('api_usage')])
    requests = request_rows(usages)
    _csv(output / 'requests-v2.csv', requests, list(dict.fromkeys(k for row in requests for k in row)) or ['case_id'])
    write_json(output / 'cost-v2.json', report.get('corrected_v2_cost'))
    if report.get('corrected_v2_cost'):
        from .cost_reporting import export_cost
        target = output / 'v2'
        target.mkdir(exist_ok=True)
        export_cost(report['corrected_v2_cost'], target)
    bridge = [{k: v for k, v in row.items() if k != 'calculation'} for row in report.get('policy_bridge', [])]
    _csv(output / 'policy-bridge.csv', bridge, list(bridge[0]) if bridge else ['stage'])
