"""Published values, native author rules and same-run API corrections."""
from dataclasses import replace
from decimal import Decimal
import importlib
import json
from pathlib import Path

from .accounting_trace import Trace, atom, calc, constant, expression, missing, export_trace
from .records import write_json
from .rq1_paper import published, paper_deltas
from .tabular import csv_file, markdown, write_text


def selected_google(directory):
    path = directory / 'selected-google-main.jsonl'
    return [(i, json.loads(line)) for i, line in enumerate(path.read_text().splitlines())] if path.exists() else []


def selected_turn_usage(directory, document):
    records = selected_google(directory)
    values = {'input': [], 'output': []}
    if len(records) != len(document['llm_interactions']):
        return {k: [missing('原生 Trae 响应与已选 Google 响应数量不同，不能猜测配对')] for k in values}
    path = directory / 'selected-google-main.jsonl'
    for index, item in records:
        usage = item.get('usageMetadata', item.get('usage_metadata')) or {}
        def read(camel, snake):
            key = camel if camel in usage else snake
            return atom(usage.get(key), str(path), f'line[{index}]/usageMetadata/' + key)
        values['input'].append(read('promptTokenCount', 'prompt_token_count'))
        thought = read('thoughtsTokenCount', 'thoughts_token_count')
        if thought['value'] is None:
            thought = constant(0, 'https://ai.google.dev/api/generate-content#UsageMetadata', '原 Gemini reader 缺少 reasoning 时不额外加')
        visible = read('candidatesTokenCount', 'candidates_token_count')
        values['output'].append(calc('sum', visible, thought, description='原 Gemini reader 输出加独立 thinking'))
    return values


def _selected_observations(method, directory, original, usage):
    """Exact response IDs and logical-call IDs; never timestamp matching."""
    if original.trace is None or (method == 'run_free' and not (original.patch and original.patch.strip())):
        return [], []
    if method == 'run_free':
        targets = [{'response_id': e.get('message', {}).get('id'), 'purpose': 'main'} for e in original.trace]
    elif method == 'turn_control':
        targets = [{'response_id': d.get('responseId', d.get('response_id')), 'purpose': 'main'} for _, d in selected_google(directory)]
        if len(targets) != len(original.trace):
            return [], ['原生步骤与 Google 选中响应不能一一关联']
    else:
        path = directory / 'native-calls.jsonl'
        if not path.exists():
            return [], ['缺少原生调用与 HTTP 响应关联记录']
        targets = [json.loads(line) for line in path.read_text().splitlines()]
    result, issues = [], []
    for target in targets:
        identity = target.get('response_id')
        matches = [o for o in usage.observations if identity and o.observation_id.rsplit('/', 1)[-1] == identity
                   and o.purpose == target['purpose'] and
                   (not target.get('call_key') or o.parent_call_id == target['call_key'])]
        if len(matches) == 1:
            result.append(matches[0])
        else:
            issues.append('原生响应缺少唯一 HTTP usage 对应：' + str(identity))
    return result, issues


def _original_cost(method, original, document, spec, directory):
    from .rq1_native import NativeMethod
    policy = NativeMethod().original_accounting([original])
    if original.trace is None:
        from .accounting_trace import metric
        policy = {**policy, 'metrics': {k: metric(missing('原生轨迹缺失'), complete=False)
                                      for k in ('input', 'output', 'total', 'cache_read', 'cache_write')}}
        if method == 'agent_diet':
            policy['overhead'] = {'metrics': dict(policy['metrics'])}
        return policy, missing('原生轨迹缺失'), {}
    if method in ('agent_diet', 'attn_compress'):
        result = importlib.import_module('methods.' + method + '.historical').replay(
            {original.case_id: document}, [original], spec)
        return result['original'], result['calculation']['cost'], result['calculation']
    values = original.method_data.get('native_calculation', {})
    if method == 'turn_control':
        from methods.turn_control.historical import gemini_original_cost
        cost = calc('sum', *(gemini_original_cost(i, o, spec) for i, o in zip(values.get('input', []), values.get('output', []))))
    else:
        cost = calc('divide', calc('sum', calc('multiply', expression(policy['metrics']['input']), constant(Decimal('3'),
            'src/rq1_run.py:paper_pricing', '补充价表；论文未报告美元费用')),
            calc('multiply', expression(policy['metrics']['output']), constant(Decimal('15'), 'src/rq1_run.py:paper_pricing'))),
            constant(1000000, __file__, '每百万 token'))
    return policy, cost, {'main_cost': cost}


def _cache_values(method, policy, observations, issues, pricing, original):
    """Change only caching; keep original selected responses/output/price tiers."""
    from .pricing import _request_cost
    from dataclasses import asdict
    result = {}
    for scope, purpose in (('main', 'main'), ('method_auxiliary', 'method_auxiliary')):
        if scope == 'method_auxiliary' and method != 'agent_diet':
            continue
        source = policy if scope == 'main' else policy.get('overhead', {})
        selected = [o for o in observations if o.purpose == purpose]
        def total(key):
            return calc('sum', *(o.calculation.get(key, missing('usage 缺少 ' + key)) for o in selected))
        values = {key: total(key) for key in ('input', 'cache_read', 'cache_write')}
        values['output'] = expression(source.get('metrics', {}).get('output', {}))
        if method == 'agent_diet' and scope == 'main' and original.method_data.get('source_error') == 'APIStatusError':
            values['input'] = calc('sum', values['input'], constant(200000,
                'methods/agent_diet/upstream/result/exporter.ipynb:APIStatusError', '缓存专项保留原错误补偿'))
        if method == 'attn_compress':
            values['input'] = expression(source.get('metrics', {}).get('input', {}))
        values['total'] = calc('sum', values['input'], values['output'])
        values['ordinary_input'] = calc('subtract', calc('subtract', values['input'], values['cache_read']), values['cache_write'])
        costs = []
        for obs in selected:
            metrics = dict(obs.metrics)
            nodes = dict(obs.calculation)
            # cache_only preserves output: AgentDiet/Attn returned completion
            # counters may exclude separately reported reasoning. Full v2 keeps it.
            raw = obs.raw_usage
            if method in ('agent_diet', 'attn_compress'):
                output_key = 'candidatesTokenCount' if obs.billing_context.get('protocol') == 'gemini_generate_content' else 'completion_tokens'
                metrics['output'] = raw.get(output_key)
                nodes['output'] = atom(raw.get(output_key), obs.source, '/usage/' + output_key)
                metrics['reasoning'] = None
                nodes['reasoning'] = missing('缓存专项保留原 completion，不重新拆分 reasoning 子项')
            local_prices = pricing
            if method == 'agent_diet':
                local_prices = {**pricing, 'models': [dict(p) for p in pricing['models']]}
                for p in local_prices['models']:
                    p.pop('long_context_threshold', None)
            if method == 'turn_control':
                # Author selects output price tier using output, not input.
                # Correcting that tier belongs to full v2, not cache_only.
                local_prices = {**pricing, 'models': [dict(p) for p in pricing['models']]}
                for p in local_prices['models']:
                    if p['provider'] == obs.billing_context.get('provider') and obs.model in p['aliases']:
                        if metrics.get('input') is not None and metrics.get('output') is not None:
                            p['output'] = str(Decimal(p['output']) * (Decimal('1.5') if metrics['output'] > 200000 else 1))
                            p['long_output_multiplier'] = '1'
            priced = _request_cost({**asdict(obs), 'metrics': metrics, 'calculation': nodes}, local_prices)
            costs.append(priced['calculation']['total_usd'])
        values['cost'] = calc('sum', *costs)
        if method == 'agent_diet' and scope == 'main' and original.method_data.get('source_error') == 'APIStatusError':
            # The compensation is an author assumption, not observed API usage.
            rate = Decimal(pricing['models'][0]['cache_read']) + Decimal('.02') * Decimal(pricing['models'][0]['input'])
            values['cost'] = calc('sum', values['cost'], constant(Decimal(200000) * rate / Decimal(1000000),
                'methods/agent_diet/upstream/result/exporter.ipynb:APIStatusError', '缓存专项保留作者补偿费用'))
        if issues:
            for key in values:
                # Original output is invariant even when actual cache is missing.
                if key != 'output':
                    values[key] = missing('; '.join(issues))
        result[scope] = values
    return result


def report_run(run, method):
    from .rq1_run import restore
    from .rq1_native import NativeAgent, NativeMethod
    from .loading import load_adapter
    from .run_accounting import save_accounting, _artifact_directories
    from .accounting_v2 import restore_cases, corrected_v2
    config, runtime, pricing = restore(run, method)
    state = json.loads((run / 'state.json').read_text())
    tasks = load_adapter(config.dataset).tasks(**config.dataset.options)
    agent, method_adapter = NativeAgent(), NativeMethod()
    accounting = save_accounting(run, state, tasks, method_adapter, agent)
    write_json(run / 'state.json', state)
    spec = config.agent.options['spec']
    published_values = published(method)
    output = run / 'rq1-report'
    output.mkdir(exist_ok=True)
    trace, rows, originals = Trace(), [], []
    aggregate_nodes = {}
    original_costs, original_main, cache_costs = [], [], []
    for detail in accounting['cases']:
        case = detail['case_id']
        saved = state['tasks'].get(case, {})
        directory = None
        if saved.get('directory'):
            paths, _ = _artifact_directories(run / saved['directory'])
            if len(paths) == 1:
                directory = next(iter(paths))
        from .accounting import OriginalCase
        original = OriginalCase(case, None, None, method_data={'rq1_method': method})
        document = None
        if directory is not None:
            try:
                original = agent.read_original_case(directory, case)
                if method in ('agent_diet', 'attn_compress'):
                    document = json.loads((directory / 'native-trajectory.json').read_text())
            except (OSError, ValueError, KeyError):
                pass
        policy, old_cost, costs = _original_cost(method, original, document, spec, directory)
        usage = restore_cases([detail['api_usage']])[0]
        selected, issues = _selected_observations(method, directory, original, usage) if directory else ([], ['任务未运行'])
        if original.trace is None:
            issues.append('原生轨迹缺失')
        cache = _cache_values(method, policy, selected, issues, pricing, original)
        for scope, values in cache.items():
            source = policy if scope == 'main' else policy.get('overhead', {})
            scoped_usage = replace(usage, observations=[o for o in usage.observations if o.purpose == scope],
                operations=[o for o in usage.operations if o.purpose == scope])
            if any(o.purpose == 'unknown' for o in [*usage.observations, *usage.operations]):
                scoped_usage = replace(scoped_usage, coverage_complete=False,
                    issues=[*scoped_usage.issues, '存在用途未知请求，不能认证该用途的完整小计'])
            if not scoped_usage.operations and not scoped_usage.observations and scoped_usage.coverage_complete:
                scoped_usage = replace(scoped_usage, llm_called=False)
            full = corrected_v2([scoped_usage])
            for key in ('input', 'output', 'total', 'cache_read', 'cache_write', 'ordinary_input'):
                operands = (expression(source.get('metrics', {}).get(key, {})), values.get(key, missing('原范围未定义')),
                            expression(full['metrics'].get(key, {})))
                aggregate_nodes.setdefault((scope, key), []).append(operands)
                trace.compare(*operands, method=method, case_id=case, scope=scope, metric=key,
                    reason='同次运行；缓存专项保持原选中响应、输出及非缓存规则。')
        cache_cost = calc('sum', *(v['cost'] for v in cache.values()))
        full_cost = detail['corrected_v2_cost']['calculation']['total_usd']
        trace.compare(old_cost, cache_cost, full_cost, method=method, case_id=case, scope='all', metric='cost_usd')
        rows.append(dict(task_id=case, stage=saved.get('stage', 'not_started'), resolved=saved.get('resolved'),
            original_cost_usd=old_cost['value'], cache_only_cost_usd=cache_cost['value'], corrected_cost_usd=full_cost['value'],
            cache_issues=issues))
        originals.append(policy)
        original_costs.append(old_cost)
        original_main.append(costs.get('main_cost', old_cost))
        cache_costs.append(cache_cost)
    old_total = calc('sum', *original_costs)
    cache_total = calc('sum', *cache_costs)
    full_total = accounting['corrected_v2_cost']['calculation']['total_usd']
    trace.compare(old_total, cache_total, full_total, method=method, case_id='__all__', scope='all', metric='cost_usd')
    for scope in ('main', 'method_auxiliary') if method == 'agent_diet' else ('main',):
        for key in ('input', 'output', 'total', 'cache_read', 'cache_write'):
            related = aggregate_nodes[(scope, key)]
            trace.compare(*(calc('sum', *(r[i] for r in related)) for i in range(3)),
                          method=method, case_id='__all__', scope=scope, metric=key)
    for key, node in published_values['calculation'].items():
        trace.add(node, method=method, case_id='__all__', scope='paper', policy='paper_reported', metric=key)
    # Dollar comparisons use literal printed costs and matching scope.
    cost_scope = 'main' if method in ('attn_compress', 'turn_control') else 'all'
    comparable_old = calc('sum', *original_main) if cost_scope == 'main' else old_total
    full_main = accounting['corrected_v2_cost'].get('groups', {}).get('purpose', {}).get('main', {})
    comparable_full = full_main.get('calculation', {}).get('total_usd', missing('主模型费用未知')) if cost_scope == 'main' else full_total
    delta = paper_deltas(method, comparable_old, comparable_full, scope=cost_scope)
    for policy in ('original_vs_paper', 'corrected_vs_paper'):
        for key, node in delta.get(policy, {}).items():
            trace.add(node, method=method, case_id='__all__', scope=cost_scope, policy=policy, metric=key)
    denominator = constant(len(tasks), str(run / 'config.json'), '固定任务数；未运行任务不从分母剔除')
    resolved = calc('sum', *(atom(row['resolved'], str(run / 'state.json'),
        '/tasks/' + row['task_id'] + '/resolved') for row in rows))
    success = calc('multiply', calc('divide', resolved, denominator), constant(100, __file__, '百分比'))
    trace.add(success, method=method, case_id='__all__', scope='evaluation', policy='same_candidates', metric='success_percent')
    aligned = [{'metric': 'success_percent', 'paper_reported': published_values['calculation']['success_percent']['value'],
                'new_original': success['value'], 'new_corrected': success['value']}]
    paper_cost = delta.get('paper_cost')
    if paper_cost:
        aligned.append({'metric': cost_scope + '_cost_total_usd', 'paper_reported': paper_cost['value'],
                        'new_original': comparable_old['value'], 'new_corrected': comparable_full['value']})
    if method in ('run_free', 'attn_compress', 'turn_control'):
        for key in ('input', 'output'):
            old, _, new = (calc('sum', *(r[i] for r in aggregate_nodes[('main', key)])) for i in range(3))
            if method == 'run_free':
                counted = constant(sum(p.get('cases_counted', 0) for p in originals), __file__, '原非空补丁筛选任务数')
                old = calc('floor_divide', old, counted)
            if method != 'turn_control':
                if method != 'run_free':
                    old = calc('divide', old, denominator)
                old = calc('divide', old, constant(1000, __file__, 'K token'))
                new = calc('divide', calc('divide', new, denominator), constant(1000, __file__, 'K token'))
            key_name = key + ('_total' if method == 'turn_control' else '_mean_k')
            aligned.append({'metric': key_name, 'paper_reported': published_values['calculation'][key_name]['value'],
                            'new_original': old['value'], 'new_corrected': new['value']})
            for label, node in (('new_original', old), ('new_corrected', new)):
                trace.add(node, method=method, case_id='__all__', scope='paper_aligned', policy=label, metric=key_name)
    summary = {'method': method, 'paper_reported': published_values,
        'new_original_cost_usd': old_total['value'], 'new_cache_only_cost_usd': cache_total['value'],
        'new_corrected_cost_usd': full_total['value'], 'paper_comparison': delta, 'cases': rows,
        'selected_tasks': len(tasks), 'model': spec['model_id'], 'aligned_comparison': aligned,
        'evaluation': {'resolved': resolved['value'], 'success_percent': success['value'],
                       'known_results': sum(type(r['resolved']) is bool for r in rows), 'tasks': len(tasks)},
        'historical_model': spec.get('historical_model_id'),
        'cost_note': '冻结论文/作者单价；未报告项与本地折算费用单列，paper 与 new-run 差额不等于缓存效应。'}
    write_json(output / 'results.json', summary)
    csv_file(output / 'cases.csv', rows, legacy=True)
    csv_file(output / 'paper-comparison.csv', aligned, legacy=True)
    export_trace(trace, output)
    aggregate = [r for r in trace.comparisons if r['case_id'] == '__all__']
    csv_file(output / 'metrics.csv', aggregate, legacy=True)
    paper_rows = [{'metric': k, 'paper_reported': v['value']} for k, v in published_values['calculation'].items()]
    csv_file(output / 'paper-values.csv', paper_rows, legacy=True)
    text = '# RQ1: ' + method + '\n\n'
    text += '模型：`' + spec['model_id'] + '`；固定题数：' + str(len(tasks)) + '。\n\n'
    text += '## 论文报告值\n\n' + markdown(paper_rows, ['metric', 'paper_reported']) + '\n\n'
    text += '[' + published_values['locator'] + '](' + published_values['source'] + ')。原文精度保留；未报告美元费用时不反填。\n\n'
    text += '## 同单位论文对照\n\n' + markdown(aligned, ['metric', 'paper_reported', 'new_original', 'new_corrected']) + '\n\n'
    text += '## 本次同轨迹统计\n\n' + markdown(aggregate, ['scope', 'metric', 'original', 'cache_only', 'corrected_v2', 'cache_delta', 'total_delta'])
    text += '\n\n完整费用和 token 的分母为固定选中任务；缺失不填零。逐题见 cases.csv，调用、辅助与本地计算见上一级 accounting.json。\n'
    text += '论文对照的费用差额直接使用论文印刷值，计算步骤见 accounting-steps.csv；跨运行差额包含模型、采样与环境变化。\n'
    if method == 'run_free':
        text += '论文未报告美元费用；original 美元列是用补充价表给原 token 口径计价，不是作者已报告费用。\n'
    if method == 'attn_compress':
        text += '论文总费用含本地压缩折算；API 费用对照使用论文主模型 $0.1543/题，本地 $0.0027/题单列。\n'
    if method == 'agent_diet':
        text += '论文 I/O 均除以历史 baseline input；新运行报告保留主/辅助绝对 token，不以不同轨迹的比例冒充同轨迹校正，也不新增 baseline。\n'
    if method == 'turn_control':
        text += '新运行使用 gemini-2.5-pro；历史归档响应为 preview-06-05。轮次为同一会话 29→45，仅扩展一次。\n'
    write_text(output / 'summary.md', text)
    return summary
