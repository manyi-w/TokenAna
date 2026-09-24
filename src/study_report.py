"""Offline research aggregation over the fixed study matrix and saved evidence."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from .records import write_json
from .tabular import csv_file, append_csv, markdown
from .study_statistics import paired_bootstrap, pareto, paired_records, pair_outcome

METRICS = ('tokens', 'cost_usd', 'seconds')


def read_case(row, root, tasks, report=None):
    parent = root / 'combinations' / row['id']
    run = parent / 'run'
    task = tasks[row['dataset']][row['case']]
    record = {**row, 'repo': task['repo'], 'language': task['language'], 'task_category': None,
              'attempted': False, 'model_called': None, 'evaluated': False, 'resolved': None,
              'tokens': None, 'cost_usd': None, 'seconds': None, 'original_tokens': None,
              'method_triggered': None, 'evidence': str(run), 'issues': []}
    state_path = run / 'state.json'
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
            saved = state.get('tasks', {}).get(row['case'], {})
            record.update(attempted=bool(saved.get('attempts')), resolved=saved.get('resolved'),
                          evaluated=saved.get('resolved') is not None, agent_error=saved.get('agent_error'))
        except (OSError, ValueError):
            record['issues'].append('saved task state unreadable')
    path = run / 'accounting.json'
    if report is None and not path.is_file():
        record['issues'].append('saved accounting unavailable')
        return record, None
    report = report if report is not None else json.loads(path.read_text())
    detail = next((c for c in report['cases'] if c['case_id'] == row['case']), None)
    if detail is None:
        record['issues'].append('selected task missing from saved accounting')
        return record, None
    if not detail.get('corrected_v2_api'):
        # Legacy records require explicit offline reconstruction; never relabel v1.
        record['issues'].append('corrected-v2-api unavailable; run tokenAna analyze on saved evidence')
    metrics = detail.get('corrected_v2_api', {}).get('metrics', {})
    tokens = metrics.get('total', {})
    cost = detail.get('corrected_v2_cost', {})
    timing = detail.get('timing', report.get('timing', {})).get('phases', {}).get('generation', {})
    original = detail.get('original_token_accounting', {})
    original_total = original.get('metrics', {}).get('total', {})
    record.update(attempted=detail.get('attempted', detail.get('stage') != 'not_started'),
        model_called=detail.get('model_called'), evaluated=detail.get('resolved') is not None,
        resolved=detail.get('resolved'), tokens=tokens.get('sum') if tokens.get('complete') else None,
        cost_usd=float(cost['total_usd']) if cost.get('complete') and cost.get('total_usd') is not None else None,
        seconds=timing.get('seconds') if timing.get('complete') else None,
        original_tokens=original_total.get('sum') if original_total.get('complete') else None,
        cache_only_tokens=(detail.get('cache_only', {}).get('total') or {}).get('sum'),
        original_denominator=original.get('cases_counted'), original_rule=original.get('rule'),
        token_reasons=tokens.get('reasons', []), cost_reasons=cost.get('reasons', []),
        local_compute=detail.get('local_compute', []), timing=report.get('timing'))
    record['no_cache_cost_usd'] = (float(cost['no_cache_discount']['total_usd']) if
        cost.get('no_cache_discount', {}).get('total_usd') is not None else None)
    record['agent_error'] = detail.get('agent_error')
    record['function_calls'] = (detail.get('native_final_summary') or {}).get('function_calls')
    state = detail.get('method_state') or {}
    if row['method'] == 'baseline':
        record['method_triggered'] = False
    elif 'experience_hit' in state:
        record['method_triggered'] = bool(state['experience_hit'])
    elif 'no_trigger' in state:
        record['method_triggered'] = not state['no_trigger']
    elif row['method'] == 'agent_diet' and 'metrics' in state:
        record['method_triggered'] = state['metrics'].get('analysis_count', 0) > 0
    elif row['method'] == 'attn_compress' and 'metrics' in state:
        record['method_triggered'] = state['metrics'].get('analysis_count', 0) > 0
    elif 'results' in state:
        record['method_triggered'] = any('skipped' not in r and 'error' not in r for r in state['results'])
    elif detail.get('control') is not None:
        control = detail['control']
        record['method_triggered'] = bool(control.get('extensions') or control.get('termination_reason') == 'budget_exhausted')
    if report.get('run_status') == 'running':
        record.update(tokens=None, cost_usd=None, seconds=None)
        record['issues'].append('run is still active')
    return record, detail


def aggregate(study, records):
    from .accounting_trace import atom, calc, constant, number, missing
    indexed = defaultdict(list)
    for record in records:
        indexed[record['configuration_id']].append(record)
    result = []
    for config in study['configurations']:
        rows = indexed[config['id']]
        count = config['selected']
        entry = {**config, 'attempted': sum(r['attempted'] for r in rows),
                 'model_called': sum(r['model_called'] is True for r in rows),
                 'evaluated': sum(r['evaluated'] for r in rows),
                 'resolved': sum(r['resolved'] is True for r in rows),
                 'resolution_complete': len(rows) == count and all(r['resolved'] is not None for r in rows)}
        denominator = atom(count, 'pilot.json', '/study/configurations/id=' + config['id'] + '/selected',
                           kind='selection', description='冻结配置选中任务数')
        entry['calculation'] = {}
        entry['comparison_issues'] = list(dict.fromkeys(issue for r in rows for issue in r.get('comparison_issues', [])))
        entry['comparable'] = not entry['comparison_issues']
        entry['resolved_rate'] = entry['resolved'] / count if entry['resolution_complete'] else None
        triggers = [r['method_triggered'] for r in rows]
        entry['trigger_rate'] = sum(v is True for v in triggers) / count if all(v is not None for v in triggers) else None
        for metric in (*METRICS, 'original_tokens', 'cache_only_tokens', 'no_cache_cost_usd'):
            known = [r.get(metric) for r in rows if r.get(metric) is not None]
            complete = len(known) == count
            entry[metric] = sum(known) if complete else None
            entry[metric + '_known_subtotal'] = sum(known) if known else None
            entry[metric + '_complete'] = complete
            entry[metric + '_mean'] = sum(known) / count if complete else None
            entry[metric + '_missing'] = [r['case'] for r in rows if r.get(metric) is None]
            if metric != 'seconds':
                fields = {'tokens': '/corrected_v2_api/metrics/total/sum',
                          'original_tokens': '/original_token_accounting/metrics/total/sum',
                          'cache_only_tokens': '/cache_only/total/sum',
                          'cost_usd': '/corrected_v2_cost/total_usd',
                          'no_cache_cost_usd': '/corrected_v2_cost/no_cache_discount/total_usd'}
                terms = [atom(r[metric], r.get('reconstructed') or r['evidence'] + '/accounting.json',
                              '/cases/case_id=' + r['case'] + fields[metric],
                              description='引用逐题已重建结果；费用研究投影保留 float 数值')
                         for r in rows if r.get(metric) is not None]
                subtotal = calc('sum', *terms, description='配置内逐题已知贡献相加') if terms else missing('无已知题目贡献')
                total = calc('guard', subtotal, constant(complete, 'src/study_report.py:aggregate', '固定选中任务逐项完整性'))
                mean = calc('divide', total, denominator, description='配置总量除以固定任务数')
                entry.update({metric: number(total), metric + '_known_subtotal': number(subtotal), metric + '_mean': number(mean)})
                entry['calculation'][metric] = dict(sum=total, known_subtotal=subtotal, mean=mean)
        result.append(entry)
    by_id = {r['id']: r for r in result}
    for entry in result:
        baseline = by_id.get(entry['baseline_id'])
        for metric in (*METRICS, 'no_cache_cost_usd'):
            value, reference = entry[metric], baseline[metric] if baseline else None
            entry[metric + '_savings'] = (1 - value / reference if entry['comparable'] and
                baseline and baseline['comparable'] and value is not None and reference not in (None, 0) else None)
    return result


def comparisons(study, aggregates, records):
    units = defaultdict(list)
    by_config = defaultdict(list)
    for row in records:
        by_config[row['configuration_id']].append(row)
    for entry in aggregates:
        if entry['dataset'] == 'deepswe':
            units[(entry['group'], entry['agent'], entry['model'])].append(entry)
    ranking, bootstrap, frontier = [], {}, {}
    for unit, entries in units.items():
        for metric in METRICS:
            complete = [e for e in entries if e[metric] is not None and e.get('comparable', True)]
            for entry in entries:
                ranking.append(dict(configuration_id=entry['id'], unit=list(unit), metric=metric,
                    rank=1 + sum(other[metric] < entry[metric] for other in complete) if entry in complete else None,
                    value=entry[metric], resolved_rate=entry['resolved_rate'], complete=entry[metric] is not None,
                    missing_tasks=entry[metric + '_missing'], comparison_scope=[e['method'] for e in complete]))
            baseline = next((e for e in complete if e['method'] == 'baseline'), None)
            key = '/'.join((*unit, metric))
            if baseline:
                values = {e['method']: {r['case']: {'repo': r['repo'], 'value': r[metric]}
                          for r in by_config[e['id']]} for e in complete}
                bootstrap[key] = paired_bootstrap(values, 'baseline', samples=study['analysis'].get('bootstrap_samples', 10000),
                                                 seed=study['analysis'].get('bootstrap_seed', 20260921))
            points = [dict(id=e['id'], resource=e[metric], resolved=e['resolved_rate']) for e in complete
                      if e['resolved_rate'] is not None]
            frontier[key] = {'points': points, 'frontier': pareto(points)}
    return ranking, bootstrap, frontier


def diagnostics(aggregates, records):
    pairs = []
    for row, base in paired_records(records):
        if base is None:
            continue
        pairs.append(dict(configuration_id=row['configuration_id'], case=row['case'], repo=row['repo'],
            language=row['language'], task_category=row['task_category'], outcome=pair_outcome(row, base),
            **{metric + '_difference': row[metric] - base[metric] if row[metric] is not None and base[metric] is not None else None
               for metric in METRICS}, baseline_tokens=base['tokens'], method_triggered=row['method_triggered']))
    # Compare methods only over the same complete cells, separately for each group.
    macro = []
    groups = defaultdict(list)
    for entry in aggregates:
        if entry['dataset'] == 'deepswe' and entry['method'] != 'baseline':
            family = 'common-model' if 'common' in entry['group'] else 'general'
            groups[family].append(entry)
    for family, entries in groups.items():
        methods = sorted({e['method'] for e in entries})
        for metric in METRICS:
            cells = {method: {(e['agent'], e['model']) for e in entries if e['method'] == method
                             and e[metric + '_savings'] is not None} for method in methods}
            common = set.intersection(*cells.values()) if cells else set()
            for method in methods:
                values = [e[metric + '_savings'] for e in entries if e['method'] == method and (e['agent'], e['model']) in common]
                macro.append(dict(group=family, method=method, metric=metric, cells=sorted(common),
                                  mean_relative_savings=sum(values) / len(values) if values else None))
    return pairs, macro


def build_report(study, rows, root, output, *, jobs=4, figures=True):
    root, output = Path(root), Path(output)
    manifest_path = root / 'pilot.json'
    if not manifest_path.is_file():
        raise ValueError('Saved study run manifest not found')
    manifest = json.loads(manifest_path.read_text())
    frozen = manifest.get('study')
    if frozen is None or any(study[k] != frozen[k] for k in ('datasets', 'configurations', 'pricing', 'analysis', 'execution')):
        raise ValueError('Study differs from the executed selection/prices/analysis settings')
    tasks = {dataset: {t['instance_id']: t for t in values} for dataset, values in study['datasets'].items()}
    expected = {r['id'] for r in rows}
    saved_rows = manifest.get('rows', [])
    saved_by_id = {r['id']: r for r in saved_rows}
    if len(saved_rows) != len(expected) or {r['id'] for r in saved_rows} != expected:
        raise ValueError('Saved execution rows differ from the fixed matrix')
    from concurrent.futures import ThreadPoolExecutor
    from .analysis import analyze_run
    from .content_diagnostics import inspect_requests, RULES
    from .accounting_v2 import request_rows, restore_cases
    from .research_diagnostics import enrich_report
    records, details, context, bridges, errors, times, execution_order = [], [], [], [], [], [], []
    trace_refs = []
    content_totals = Counter()
    context_cases = set()
    evidence = output / 'reconstructed'
    evidence.mkdir()
    def reconstruct(row):
        run = root / 'combinations' / row['id'] / 'run'
        if not (run / 'state.json').is_file():
            return row, None, None, None
        try:
            config = json.loads((run / 'config.json').read_text())
            saved_row = saved_by_id[row['id']]
            profile = manifest.get('profiles', {}).get(saved_row.get('profile_id'))
            if profile is None:
                raise ValueError('Frozen execution profile missing; comparability cannot be verified')
            import copy
            expected_config = copy.deepcopy(profile)
            expected_config['dataset']['options']['task_ids'] = [row['case']]
            actual = {k: v for k, v in config.items() if k != 'path'}
            if actual != expected_config:
                raise ValueError('Actual configuration differs from frozen execution profile')
            saved_prices = json.loads((run / 'pricing.json').read_text())
            if saved_prices != study['pricing']:
                raise ValueError('Actual price snapshot differs from study')
            report = analyze_run(run, evidence / row['id'])['report']
            return row, report, inspect_requests(run, seed=study['analysis'].get('bootstrap_seed', 20260921)), None
        except (OSError, ValueError, KeyError, TypeError) as error:
            return row, None, None, f'{type(error).__name__}: {error}'
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        # Bound outstanding results as well as workers; one slow task must not
        # retain the entire study's decoded trajectories in completed futures.
        def batches():
            for start in range(0, len(rows), jobs):
                yield from pool.map(reconstruct, rows[start:start + jobs])
        rebuilt = batches()
        for row, report, diagnostic, error in rebuilt:
            if error:
                errors.append(dict(configuration_id=row['configuration_id'], case=row['case'], reason=error))
            # A failed reconstruction cannot silently fall back to stale accounting.
            record, detail = read_case(row, root, tasks, report=report or {'cases': []})
            record['comparison_issues'] = []
            if row['baseline_id']:
                profiles = manifest.get('profiles', {})
                current_profile = profiles.get(saved_by_id[row['id']].get('profile_id'))
                baseline_row = saved_by_id.get(row['baseline_id'] + '__' + row['case'], {})
                baseline_profile = profiles.get(baseline_row.get('profile_id'))
                if current_profile is None or baseline_profile is None or any(current_profile[k] != baseline_profile[k] for k in ('agent', 'model', 'dataset')):
                    record['comparison_issues'].append('Method/baseline foundation settings differ or are unavailable')
            if manifest.get('measurement_jobs') != study['execution'].get('measurement_jobs', 1):
                record['seconds'] = None
                record['issues'].append('Measurement concurrency not frozen to the study setting')
            if report:
                record['reconstructed'] = str(evidence / row['id'] / 'accounting.json')
                usage = restore_cases([d['api_usage'] for d in report['cases']])
                append_csv(output / 'requests.csv', [{**r, 'configuration_id': row['configuration_id']} for r in request_rows(usage)])
                append_csv(output / 'policy-bridge-cases.csv', [{**{k: v for k, v in b.items() if k != 'calculation'}, 'configuration_id': row['configuration_id'], 'case_id': row['case']} for b in report.get('policy_bridge', [])])
            trace_refs.append((row['id'], record.get('reconstructed'), row['case'], error))
            if diagnostic:
                for filename, key in (('content-structure', 'segments'), ('context-by-request', 'requests'), ('review-samples', 'review_samples'), ('method-changes', 'method_changes')):
                    values = [{**r, 'configuration_id': row['configuration_id'], 'case_id': row['case']} for r in diagnostic[key]]
                    append_csv(output / (filename + '.csv'), values)
                    if key == 'segments':
                        for value in values:
                            content_totals[value['category']] += value['characters']
                    if key == 'requests' and (len(context_cases) < 12 or row['id'] in context_cases):
                        context_cases.add(row['id'])
                        context.extend(values)
                errors.extend({'case': row['case'], **e} for e in diagnostic['issues'])
            record.pop('local_compute', None)
            record.pop('timing', None)
            records.append(record)
            if detail:
                details.append({'configuration_id': row['configuration_id'], 'case_id': row['case'], 'report_path': record['reconstructed']})
                for begin, end in detail.get('timing', {}).get('phases', {}).get('generation', {}).get('intervals', []):
                    execution_order.append({'configuration_id': row['configuration_id'], 'case_id': row['case'],
                        'started_at': begin, 'finished_at': end})
                times.extend({'configuration_id': row['configuration_id'], 'case_id': row['case'], 'phase': phase,
                    **{k: v for k, v in value.items() if k != 'intervals'}}
                    for phase, value in detail.get('timing', {}).get('phases', {}).items())
                append_csv(output / 'local-compute.csv', [{'configuration_id': row['configuration_id'], 'case_id': row['case'], **v}
                    for v in detail.get('local_compute', [])])
    totals = aggregate(study, records)
    ranks, intervals, frontier = comparisons(study, totals, records)
    pairs, macro = diagnostics(totals, records)
    result = dict(version=2, status='offline_report', selected=len(rows), aggregates=totals,
                  rankings=ranks, bootstrap=intervals, pareto=frontier, macro=macro,
                  notes=['Fixed selected denominator; missing values are not zero.',
                         'Verified has no paper baseline; savings and rankings come from DeepSWE.',
                         'Outcome strata are descriptive diagnostics, not causal effects.'])
    result['diagnostics'] = enrich_report(study, root, records, details, totals, None)
    result['diagnostics']['time'] = times
    result['diagnostics']['local_compute_file'] = 'local-compute.csv'
    result['diagnostics']['method_changes_file'] = 'method-changes.csv'
    result['reconstruction_errors'] = errors
    result['rules'] = RULES
    result['source_run'] = str(root.resolve())
    result['execution'] = {k: manifest.get(k) for k in ('measurement_jobs', 'platform', 'retention_mode')}
    execution_order.sort(key=lambda r: (r['started_at'], r['configuration_id'], r['case_id']))
    csv_file(output / 'execution-order.csv', execution_order)
    write_json(output / 'research.json', result)
    for name, values in (('cases', records), ('aggregates', totals), ('rankings', ranks), ('paired-diagnostics', pairs), ('macro', macro)):
        csv_file(output / (name + '.csv'), values)
    from .accounting_trace import export_trace_bundle, unavailable_trace, Trace, missing
    def trace_items():
        for identity, path, case, error in trace_refs:
            value = json.loads(Path(path).read_text()).get('accounting_trace') if path else None
            yield identity, value or unavailable_trace(case, error or '选中任务尚无已重建的统计证据')
        for entry in totals:
            trace = Trace()
            values = entry['calculation']
            trace.compare(values['original_tokens']['sum'], values['cache_only_tokens']['sum'], values['tokens']['sum'],
                          case_id='__all__', method=entry['method'], model=entry['model'], scope='reported', metric='total',
                          reason='配置内逐题贡献汇总；同名任务按配置分开，未知不填零。')
            trace.compare(missing('通用 original 未定义作者费用'), missing('缺少同范围作者价表'), values['cost_usd']['sum'],
                          case_id='__all__', method=entry['method'], model=entry['model'], scope='all', metric='cost_usd',
                          reason='配置内逐题费用汇总。')
            for metric, nodes in values.items():
                for key, node in nodes.items():
                    trace.add(node, case_id='__all__', method=entry['method'], model=entry['model'], scope='aggregate', policy=metric, metric=key)
            for step in trace.steps:
                if step.get('source') == 'pilot.json':
                    step['source'] = str(manifest_path)
            indexed = {s['step_id']: s for s in trace.steps}
            for step in trace.steps:
                for operand in step['operands']:
                    operand['source'] = indexed[operand['step_id']].get('source')
            yield entry['id'] + '/aggregate', trace.payload()
    export_trace_bundle(trace_items(), output)
    write_json(output / 'cases.json', records)
    write_json(output / 'evidence.json', details)
    csv_file(output / 'reconstruction-errors.csv', errors)
    for name in ('requests', 'content-structure', 'context-by-request', 'review-samples', 'policy-bridge-cases', 'local-compute', 'method-changes'):
        if not (output / (name + '.csv')).exists():
            csv_file(output / (name + '.csv'), [])
    for name, values in result['diagnostics'].items():
        if isinstance(values, list):
            filename = 'traceable-cases' if name == 'cases' else name.replace('_', '-')
            csv_file(output / (filename + '.csv'), [{k: v for k, v in r.items() if k != 'calculation'} for r in values] if name == 'policy_bridge' else values)
    write_json(output / 'classification-rules.json', RULES)
    write_json(output / 'diagnostics.json', result['diagnostics'])
    if figures:
        from .research_figures import render
        result['figures'] = render(output, result,
            [{'category': k, 'characters': v} for k, v in content_totals.items()], context, bridges)
    else:
        result['figures'] = {'status': 'omitted', 'reason': '--no-figures'}
    write_json(output / 'research.json', result)
    fields = ('id', 'selected', 'attempted', 'model_called', 'evaluated', 'resolved', 'tokens', 'cost_usd', 'seconds')
    text = '# Research results\n\n' + '\n\n'.join(result['notes']) + '\n\n' + markdown(totals, fields)
    text += '\n\nFigures: ' + result['figures']['status'] + '. ' + result['figures'].get('reason', '')
    text += ('\n\nEvidence and diagnostics:\n\n'
        '- [Per-task completeness](cases.csv), [rankings and scope](rankings.csv), [equal-weight savings](macro.csv).\n'
        '- [Original-policy replay](original-policy.csv), [ordered policy bridge](policy-bridge.csv).\n'
        '- [API requests](requests.csv), [resource groups](resource-groups.csv), [trajectory effects](effects.csv).\n'
        '- [Outcome and other strata](strata.csv), [paired diagnostics](paired-diagnostics.csv), [traceable cases](traceable-cases.csv).\n'
        '- [Structural content](content-structure.csv), [review samples](review-samples.csv), [classification rules](classification-rules.json).\n'
        '- [Time phases](time.csv), [execution order](execution-order.csv), [local inference](local-compute.csv).\n'
        '- [Reconstruction errors](reconstruction-errors.csv), [evidence index](evidence.json), [complete results and bootstrap](research.json).\n')
    (output / 'research.md').write_text(text + '\n')
    with (output / 'paper-table.tex').open('w') as stream:
        stream.write('\\begin{tabular}{lrrrrr}\nConfiguration & Selected & Resolved & Tokens & Cost & Seconds \\\\\n')
        for row in totals:
            values = [str(row[k]) if row[k] is not None else '--' for k in ('id', 'selected', 'resolved_rate', 'tokens', 'cost_usd', 'seconds')]
            stream.write(' & '.join(v.replace('_', '\\_') for v in values) + ' \\\\\n')
        stream.write('\\end{tabular}\n')
    return {'status': result['status'], 'selected': len(rows), 'complete_token_configurations': sum(t['tokens_complete'] for t in totals)}
