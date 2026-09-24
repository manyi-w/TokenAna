"""Three-way case comparisons composed from the calculators' own expressions."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from .accounting_trace import Trace, atom, calc, constant, missing, expression, number
from .accounting_v2 import corrected_v2, restore_cases

TOKEN_NAMES = ('input', 'output', 'total', 'ordinary_input', 'cache_read', 'cache_write', 'reasoning')


def scoped_usage(usage, purpose):
    return replace(usage, observations=[o for o in usage.observations if o.purpose == purpose],
                   operations=[o for o in usage.operations if o.purpose == purpose],
                   # Unknown attribution might belong to any group.
                   coverage_complete=usage.coverage_complete and not any(o.purpose == 'unknown' for o in usage.observations),
                   issues=[*usage.issues, *(['调用用途未确定，分组可能缺项'] if any(o.purpose == 'unknown' for o in usage.observations) else [])],
                   llm_called=(True if any(o.purpose == purpose for o in usage.observations) or any(o.purpose == purpose and o.inference for o in usage.operations)
                               else False if usage.coverage_complete and usage.llm_called is not None else None))


def nodes(policy):
    return {k: {**expression(v), 'metric_complete': v.get('complete', False)} for k, v in policy.get('metrics', {}).items()}


def cache_projection(detail, original, usage):
    old = nodes(original)
    result = {k: missing('缺少相同原调用范围的实际缓存证据') for k in TOKEN_NAMES}
    result['output'] = old.get('output', missing('原输出未提供'))
    result['reasoning'] = old.get('reasoning', missing('原 reasoning 未提供；缓存专项不调整输出'))
    mapping = detail.get('normalized_native', {})
    selected = [o for o in usage.observations if [o.attempt_id, o.call_id, o.observation_id] in mapping.get('identities', [])
                and o.purpose == 'main']
    if original.get('cases_counted') == 0:
        for name in ('input', 'output', 'total'):
            result[name] = old.get(name, missing('原规则未提供'))
    elif mapping.get('reason') is None and selected:
        native = detail.get('original_evidence', {}).get('trace') or []
        from .accounting_trace import event_source
        from .research_evidence import restore_original
        case = restore_original(detail['original_evidence'])
        native_input = calc('sum', *(event_source(case, i, event, 'input_tokens') for i, event in enumerate(native)
                                    if event.get('type') == 'turn.completed'), description='已映射原生调用的输入合计')
        for name in ('input', 'cache_read', 'cache_write', 'ordinary_input'):
            result[name] = mapping.get('calculation', {}).get(name) or missing('对应 API 字段缺失或有冲突')
        result['input'] = calc('sum', old.get('input', missing('原输入未知')),
                              calc('subtract', result['input'], native_input),
                              description='原输入 + 同调用缓存规范化差额；保留其他原规则补偿')
    for name in ('input', 'output'):
        if old.get(name, {}).get('metric_complete') is False:
            result[name] = calc('guard', result[name], constant(False, 'src/accounting_explain.py:cache_projection',
                                '原范围此项证据不完整，缓存专项不能补齐'))
    result['total'] = calc('sum', result['input'], result['output'], description='仅缓存校正 input + 原 output')
    result['cache'] = calc('sum', result['cache_read'], result['cache_write'], description='缓存读取 + 写入，已含于 input')
    return result


def add_metrics(trace, old, cache, full, *, context, reason):
    names = dict.fromkeys([*TOKEN_NAMES, *old, *cache, *full.get('metrics', {})])
    for name in names:
        value = full.get('metrics', {}).get(name, {})
        old_node = old.get(name, missing('原规则未报告该指标'))
        cache_node = cache.get(name, missing('仅缓存校正缺少必要证据'))
        full_node = expression(value)
        trace.compare(old_node, cache_node, full_node, metric=name, **context, reason=reason)
        if 'known_subtotal' in value:
            trace.add(expression(value, 'known_subtotal'), policy='corrected_v2_known_subtotal', metric=name, **context)
        for key in ('original_complete', 'cache_only_complete', 'corrected_v2_complete'):
            trace.comparisons[-1][key] = (value.get('complete', False) if key == 'corrected_v2_complete'
                                         else old_node.get('metric_complete', old_node['value'] is not None) if key == 'original_complete' else cache_node.get('metric_complete', cache_node['value'] is not None))
        trace.comparisons[-1]['known_subtotal'] = value.get('known_subtotal')
        trace.comparisons[-1]['missing_reasons'] = value.get('reasons', [])


def explain_run(report):
    trace = Trace()
    method = report.get('experiment', {}).get('method', {}).get('name') or report.get('method_name', 'unknown')
    caches, outcomes = [], []
    for detail in report['cases']:
        usage = restore_cases([detail['api_usage']])[0]
        for observation in usage.observations:
            for name, node in observation.calculation.items():
                trace.add(node, method=method, case_id=detail['case_id'], scope=observation.purpose,
                          model=observation.model, call_id=observation.call_id,
                          attempt_id=observation.attempt_id, policy='api_normalization', metric=name)
        old_policy = detail['original_token_accounting']
        cache = cache_projection(detail, old_policy, usage)
        detail['cache_only'] = {k: {'sum': v['value'], 'complete': v['value'] is not None,
                                  'calculation': {'sum': v}} for k, v in cache.items()}
        caches.append(cache)
        context = dict(method=method, case_id=detail['case_id'], scope='reported', model='all')
        add_metrics(trace, nodes(old_policy), cache, detail['corrected_v2_api'], context=context,
            reason='原主列与全部实际生成调用对照；仅缓存列保持原选择与输出。无法建立逐响应一一映射的差额不作缓存归因。')
        for scope in ('main', 'agent_auxiliary', 'method_auxiliary'):
            old = nodes(old_policy) if scope == 'main' else (nodes(old_policy.get('overhead', {})) if scope == 'method_auxiliary' else {})
            subset = corrected_v2([scoped_usage(usage, scope)], selection_source=report['run'] + '/tasks.json')
            if report.get('run_status') == 'running':
                for value in subset['metrics'].values():
                    value.update(sum=None, mean=None, complete=False)
                    value['reasons'].append('active run; evidence may change')
            scoped_cache = dict(cache) if scope == 'main' else {}
            if scope == 'method_auxiliary' and method == 'agent_diet':
                helper = old_policy.get('helper_calculation', {})
                scoped_cache = dict(input=helper.get('before', missing('原辅助输入缺失')),
                                    output=helper.get('output', missing('原辅助输出缺失')))
                scoped_cache['total'] = calc('sum', scoped_cache['input'], scoped_cache['output'])
            models = {o.model for o in usage.observations if o.purpose == scope and o.model}
            scope_model = next(iter(models)) if len(models) == 1 else 'multiple' if models else None
            add_metrics(trace, old, scoped_cache, subset, context={**context, 'scope': scope, 'model': scope_model},
                        reason='按调用用途分栏；原未提供的指标不补造数值。')
        cost = detail['corrected_v2_cost']
        trace.compare(missing('该通用 original reader 未定义作者费用'), missing('缺少同范围作者费用/价表定义'),
                      expression(cost, 'total_usd'),
                      method=method, case_id=detail['case_id'], scope='all', metric='cost_usd',
                      reason='完整 v2 费用引用逐调用冻结价表；不将统一计价冒充作者费用。')
        for scope, group in cost.get('groups', {}).get('purpose', {}).items():
            trace.compare(missing('原规则未定义该用途费用'), missing('缺少同范围作者价表'),
                          expression(group, 'total_usd'), method=method, case_id=detail['case_id'],
                          scope=scope, metric='cost_usd', reason='按用途合计实际调用费用；缺少 usage 或价格时保留未知。')
        trace.add(expression(cost, 'known_subtotal_usd'), method=method, case_id=detail['case_id'],
                  scope='all', policy='corrected_v2_known_subtotal', metric='cost_usd')
        for request in cost.get('requests', []):
            for line in request.get('line_items', []):
                for key, node in line.get('calculation', {}).items():
                    trace.add(node, method=method, case_id=detail['case_id'], scope=request.get('purpose') or 'unknown',
                              policy='corrected_v2_cost', metric=line['component'] + '/' + key,
                              call_id=request.get('call_id') or '', model=request.get('model') or '')
        outcome = detail.get('resolved')
        raw_outcome = atom(outcome, report['run'] + '/state.json', '/tasks/' + detail['case_id'] + '/resolved',
                           kind='evaluation', description='保存的评测结果，未重新评测')
        resolved = (calc('equal', raw_outcome, constant(True, 'src/accounting_explain.py:explain_run'))
                    if outcome is not None else missing('尚无评测结果'))
        outcomes.append(resolved)
        trace.compare(resolved, resolved, resolved, method=method, case_id=detail['case_id'], scope='evaluation', metric='resolved',
                      reason='同一候选与同一评测，统计校正不改变结果；不据此认证多次尝试的 pass@1。')
        from .research_evidence import policy_bridge
        detail['policy_bridge'] = policy_bridge([detail], old_policy, detail['corrected_v2_api'], method)
        for row in detail['policy_bridge']:
            for key, node in row['calculation'].items():
                trace.add(node, method=method, case_id=detail['case_id'], scope='policy_bridge',
                          policy=row['stage'], metric=row['metric'] + '/' + key)
        for policy, selection in (('original', old_policy.get('selection', [])), ('corrected_v2', detail['corrected_v2_api'].get('selection', []))):
            for index, item in enumerate(selection):
                trace.add(atom(item, report['run'] + '/state.json', '/tasks/' + detail['case_id'], kind='selection',
                               description='纳入/排除及去重决定，见数值中的原因与调用身份'),
                          method=method, case_id=detail['case_id'], scope='selection', policy=policy, metric='selection')
    aggregated_cache = {k: calc('sum', *(c.get(k, missing('该题缺少缓存证据')) for c in caches),
                               description='逐题缓存专项结果相加；任一未知则总量未知') for k in TOKEN_NAMES}
    add_metrics(trace, nodes(report['original_token_accounting']), aggregated_cache, report['corrected_v2_api'],
                context=dict(method=method, case_id='__all__', scope='reported'), reason='固定集合汇总，逐指标保留未知。')
    for policy, section in (('original', report['original_token_accounting']), ('corrected_v2', report['corrected_v2_api'])):
        for name, value in section.get('metrics', {}).items():
            trace.add(expression(value, 'mean'), method=method, case_id='__all__', scope='reported', policy=policy, metric=name + '/mean')
    trace.compare(missing('原规则未定义作者费用'), missing('缺少同范围作者价表'),
                  expression(report['corrected_v2_cost'], 'total_usd'), method=method,
                  case_id='__all__', scope='all', metric='cost_usd', reason='逐题实际 API 费用汇总，未知不填零。')
    for key in ('known_subtotal_usd', 'mean_usd'):
        trace.add(expression(report['corrected_v2_cost'], key), method=method,
                  case_id='__all__', scope='all', policy='corrected_v2_cost', metric=key)
    resolved_sum = calc('sum', *outcomes, description='逐题评测成功指示相加；未评测不填零')
    denominator = calc('count', *(atom(c['case_id'], report['run'] + '/tasks.json', 'instance_id=' + c['case_id'], kind='selection') for c in report['cases']))
    rate = calc('divide', resolved_sum, denominator, description='成功题数除以固定选中任务数')
    for key, node in (('resolved', resolved_sum), ('resolved_rate', rate)):
        trace.compare(node, node, node, method=method, case_id='__all__', scope='evaluation', metric=key,
                      reason='同一候选的同一评测；不以统计变化推断补丁质量变化。')
    for step in trace.steps:
        if step.get('source') in ('pricing.json', 'tasks.json'):
            step['source'] = str(Path(report['run']) / step['source'])
    indexed = {s['step_id']: s for s in trace.steps}
    for step in trace.steps:
        for operand in step['operands']:
            operand['source'] = indexed[operand['step_id']].get('source')
    report['accounting_trace'] = trace.payload()
    return trace
