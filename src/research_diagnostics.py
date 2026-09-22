"""Descriptive resource, outcome and author-policy comparisons with explicit gaps."""
from collections import defaultdict
import json
from pathlib import Path

from .accounting_v2 import corrected_v2, restore_cases
from .components import load_component
from .loading import load_adapter
from .research_evidence import baseline_projection, restore_original, policy_bridge
from .study_statistics import quantile, paired_records, pair_outcome


def metric_sum(values):
    known = [v for v in values if v is not None]
    return dict(sum=sum(known) if len(known) == len(values) else None,
                known_subtotal=sum(known) if known else None,
                complete=len(known) == len(values), selected=len(values))


def enrich_report(study, root, records, details, aggregates, requests):
    detail_index = {(d['configuration_id'], d['case_id']): d for d in details}
    baselines = {(r['configuration_id'], r['case']): base for r, base in paired_records(records)}
    configurations = {c['id']: c for c in study['configurations']}
    aggregate_index = {r['id']: r for r in aggregates}
    by_config, calls = defaultdict(list), defaultdict(list)
    for row in records:
        by_config[row['configuration_id']].append(row)
    for row in requests or []:
        calls[row['configuration_id']].append(row)
    policy, groups, resources, cases, bridges, effects = [], [], [], [], [], []
    root = Path(root)
    for config in study['configurations']:
        rows = by_config[config['id']]
        def detail(case):
            value = detail_index.get((config['id'], case))
            if value and value.get('report_path'):
                report = json.loads(Path(value['report_path']).read_text())
                return next(d for d in report['cases'] if d['case_id'] == case)
            return value
        selected = [detail(r['case']) for r in rows]
        available = [d for d in selected if d is not None]
        source = Path(__file__).resolve().parents[1] / 'methods' / config['method']
        method = load_adapter(load_component(source, 'method', {}))
        native = [restore_original(d['original_evidence']) for d in available if d.get('original_evidence')]
        complete_native = len(native) == len(rows) and all(
            d['original_token_accounting']['metrics'].get('total', {}).get('complete') for d in available)
        original = method.original_accounting(native) if complete_native else None
        baseline_original, baseline_evidence, failures = None, [], []
        if config['baseline_id']:
            for row in rows:
                base = baselines[(config['id'], row['case'])]
                try:
                    if not base or not base.get('reconstructed'):
                        raise ValueError('baseline evidence unavailable')
                    projection, evidence = baseline_projection(base['evidence'], config['method'], row['case'])
                    if not projection['metrics']['total']['complete']:
                        raise ValueError('baseline original projection incomplete')
                    baseline_evidence.append(restore_original(evidence))
                except (OSError, ValueError, KeyError) as error:
                    failures.append({'case': row['case'], 'reason': str(error)})
            if not failures:
                baseline_original = method.original_accounting(baseline_evidence)
        for metric in ('input', 'output', 'total', 'cache_read', 'reasoning'):
            left = original.get('metrics', {}).get(metric, {}) if original else {}
            right = baseline_original.get('metrics', {}).get(metric, {}) if baseline_original else {}
            # Cache estimates and different meanings never enter a common ranking.
            comparable = (metric in ('input', 'output', 'total') and left.get('complete') and right.get('complete')
                and aggregate_index[config['id']].get('comparable', True)
                and aggregate_index.get(config['baseline_id'], {}).get('comparable', False))
            value, reference = left.get('sum'), right.get('sum')
            policy.append(dict(configuration_id=config['id'], metric=metric, original=value,
                baseline_original=reference, original_denominator=original['cases_counted'] if original else None,
                baseline_original_denominator=baseline_original['cases_counted'] if baseline_original else None,
                original_mean=left.get('mean'), baseline_original_mean=right.get('mean'),
                original_rule=original.get('rule') if original else None,
                savings=1 - value / reference if comparable and reference not in (None, 0) else None,
                comparable=bool(comparable), issues=failures,
                note='Baseline replay uses this method original rule; no shared baseline-native surrogate.'))
        if original:
            usages = restore_cases([d['api_usage'] for d in available])
            bridges.extend({'configuration_id': config['id'], **v} for v in
                           policy_bridge(available, original, corrected_v2(usages), config['method']))
        if requests is None:
            from .accounting_v2 import request_rows
            reqs = request_rows(restore_cases([d['api_usage'] for d in available]))
        else:
            reqs = calls[config['id']]
        requests_by_case, requests_by_purpose = defaultdict(list), defaultdict(list)
        for request in reqs:
            if request['request_state'] == 'forwarded':
                requests_by_case[request['case_id']].append(request)
                requests_by_purpose[request.get('purpose')].append(request)
        for row in rows:
            selected_requests = requests_by_case[row['case']]
            main = [r for r in selected_requests if r['purpose'] == 'main']
            inp, out = (metric_sum([r.get(k) for r in main]) for k in ('input', 'output'))
            effects.append(dict(configuration_id=config['id'], case=row['case'],
                main_requests=len(main), all_requests=len(selected_requests), native_function_calls=row.get('function_calls'),
                main_input=inp, main_output=out,
                mean_input_per_main_request=inp['sum']/len(main) if inp['sum'] is not None and main else None,
                mean_output_per_main_request=out['sum']/len(main) if out['sum'] is not None and main else None,
                successful_http_only=metric_sum([r.get('total') for r in selected_requests if r.get('outcome') == 'success']),
                failed_http=metric_sum([r.get('total') for r in selected_requests if r.get('outcome') != 'success']),
                solved_task_only= row['tokens'] if row['resolved'] is True else None,
                excluded_by_solved_filter=row['resolved'] is not True,
                note='HTTP attempts are not agent rounds. Conditional totals use explicit subsets; full-task result remains primary.'))
        for purpose in ('main', 'agent_auxiliary', 'method_auxiliary', 'unknown'):
            chosen = requests_by_purpose[purpose]
            resources.append(dict(configuration_id=config['id'], purpose=purpose, requests=len(chosen),
                metrics={k: metric_sum([r.get(k) for r in chosen]) for k in ('input', 'output', 'total', 'cache_read', 'reasoning')},
                coverage_complete=len(available) == len(rows) and all(d['api_usage']['coverage_complete'] for d in available),
                unknown_purpose_present=any(r.get('purpose') == 'unknown' for r in reqs)))
        baseline_values = [base['tokens'] for r in rows
            if (base := baselines[(config['id'], r['case'])]) is not None and base['tokens'] is not None]
        thresholds = [quantile(baseline_values, q) for q in (.25, .5, .75)]
        strata = defaultdict(list)
        for row in rows:
            base = baselines[(config['id'], row['case'])]
            outcome = pair_outcome(row, base)
            demand = ('Q' + str(1 + sum(base['tokens'] > t for t in thresholds)) if base and base['tokens'] is not None
                      and all(t is not None for t in thresholds) else 'unknown')
            for dimension, key in (('language', row['language']), ('task_category', row['task_category'] or 'unknown'),
                                   ('outcome', outcome), ('baseline_demand', demand),
                                   ('trigger', str(row['method_triggered']))):
                strata[(dimension, key)].append((row, base))
            if base:
                token_saving = 1 - row['tokens'] / base['tokens'] if row['tokens'] is not None and base['tokens'] not in (None, 0) else None
                cost_saving = 1 - row['cost_usd'] / base['cost_usd'] if row['cost_usd'] is not None and base['cost_usd'] not in (None, 0) else None
                cases.append(dict(configuration_id=config['id'], case=row['case'], outcome=outcome,
                    apparent_saving_with_failure=bool(token_saving is not None and token_saving > 0 and row['resolved'] is False),
                    opposite_token_cost_direction=token_saving * cost_saving < 0 if token_saving is not None and cost_saving is not None else None,
                    token_savings=token_saving, cost_savings=cost_saving,
                    method_evidence=row.get('reconstructed'), baseline_evidence=base.get('reconstructed'),
                    interpretation='Descriptive trajectory difference; earlier failure and changed tool/model behavior may confound savings.'))
        for (dimension, key), pairs in sorted(strata.items()):
            groups.append(dict(configuration_id=config['id'], dimension=dimension, value=key, selected=len(pairs),
                method={m: metric_sum([r[m] for r, _ in pairs]) for m in ('tokens', 'cost_usd', 'seconds')},
                baseline={m: metric_sum([b[m] if b else None for _, b in pairs]) for m in ('tokens', 'cost_usd', 'seconds')}))
    # Rankings of original relative savings are allowed only inside one comparable
    # metric meaning and agent/model cell. Each denominator is method-specific.
    for row in policy:
        config = configurations[row['configuration_id']]
        scope = [p for p in policy if p['metric'] == row['metric'] and p['savings'] is not None and
                 all(configurations[p['configuration_id']][k] == config[k] for k in ('group', 'agent', 'model'))]
        row['relative_savings_rank'] = 1 + sum(p['savings'] > row['savings'] for p in scope) if row['savings'] is not None else None
        row['comparison_scope'] = [p['configuration_id'] for p in scope]
        if row['metric'] == 'total' and row['relative_savings_rank'] is not None:
            candidates = [a for a in aggregates if a['id'] in row['comparison_scope'] and a.get('tokens_savings') is not None and a.get('comparable', True)]
            current = next((a for a in candidates if a['id'] == row['configuration_id']), None)
            if current and len(candidates) == len(scope):
                row['corrected_savings_rank_same_scope'] = 1 + sum(a['tokens_savings'] > current['tokens_savings'] for a in candidates)
                row['rank_change'] = row['corrected_savings_rank_same_scope'] - row['relative_savings_rank']
            else:
                row['corrected_savings_rank_same_scope'] = row['rank_change'] = None
    from .preparation_accounting import preparation_report
    return dict(original_policy=policy, policy_bridge=bridges, strata=groups, resource_groups=resources, cases=cases, effects=effects,
                preparation=preparation_report(root, study),
                interpretation='All diagnostic strata accompany full fixed-task results; no causal claims.')
