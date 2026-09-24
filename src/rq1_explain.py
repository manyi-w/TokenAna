"""RQ1 expression export; historical readers own original arithmetic."""
from .accounting_trace import Trace, atom, calc, constant, missing, expression
from .accounting_explain import add_metrics, nodes


def explain(report):
    from .rq1 import cache_correction, incomplete_api_accounting
    trace = Trace()
    method = report['method']
    for case, original in [*report['case_originals'].items(), ('__all__', report['original'])]:
        original = original or {}
        cache = report['cache_only'] if case == '__all__' else cache_correction(original, method)
        full = report['corrected'] if case == '__all__' else incomplete_api_accounting([case])
        for scope, values in cache['scopes'].items():
            policy = original.get('original', {})
            old = nodes(policy) if scope == 'main' else nodes(policy.get('overhead', {}))
            if scope == 'main' and 'cache_read' in old:
                old['cache'] = old['cache_read']
            if scope == 'method_auxiliary':
                old['cache'] = policy.get('helper_calculation', {}).get('assumed_cache', missing('原假定缓存未提供'))
            add_metrics(trace, old, {k: expression(v) for k, v in values.items()}, full,
                context=dict(method=method, case_id=case, scope=scope, model=report.get('helper_model') if scope == 'method_auxiliary' else report['model']),
                reason='完整 v2 缺逐调用 API 证据；缓存专项只保留可证明的不变量或原假定扣除恢复量。')
        originals = original.get('calculation', {})
        trace.compare(originals.get('cost', missing('原费用未知')), expression(cache['cost']), missing('完整调用缓存、usage 或价表证据不足'),
                      method=method, case_id=case, scope='all', metric='cost_usd', reason='作者费用公式逐项展开；实际缓存缺失时不能给出费用校正数值。')
        for metric in ('pass_at_1', 'resolved', 'original_success_rate'):
            node = originals.get(metric, missing('原记录不足以证明此项'))
            trace.compare(node, node, node, method=method, case_id=case, scope='evaluation', metric=metric,
                          reason='复用同一候选的作者评测，不重新评测；严格 pass@1 不从多次 try 推断。')
        if case != '__all__':
            row = next(r for r in report['rows'] if r['task_id'] == case)
            for field in ('original_counted', 'selected_attempt', 'attempts', 'generation_result', 'evaluation_result'):
                if field in row:
                    trace.add(atom(row[field], row['source'], field, kind='selection', description='原筛选/尝试选择证据'),
                              method=method, case_id=case, scope='selection', policy='original', metric=field)
            if 'native_total_residual' in row:
                native = {k: atom(row[key], row['source'], '/metrics/' + k, description='作者原字段') for k, key in
                          (('cost_tokens', 'native_cost_tokens'), ('completion_tokens', 'output'))}
                # input may contain original compensation, so read its original expression instead.
                main = original.get('original', {}).get('metrics', {})
                incoming = expression(main.get('input', {}))
                if method == 'agent_diet':
                    incoming = incoming['inputs'][0]
                residual = calc('subtract', calc('subtract', native['cost_tokens'], incoming), native['completion_tokens'],
                                description='cost_tokens − 原 prompt_tokens − completion_tokens；未分类，不补进 output')
                trace.add(residual, method=method, case_id=case, scope='diagnostic', policy='original', metric='native_total_residual')
        else:
            for name, node in report.get('paper_reported', {}).get('calculation', {}).items():
                trace.add(node, method=method, case_id=case, scope='paper', policy='paper_reported', metric=name)
            for name, node in (report.get('paper_reference') or {}).get('calculation', {}).items():
                trace.add(node, method=method, case_id=case, scope='original_table', policy='original', metric=name)
            for name, value in original.get('original', {}).get('metrics', {}).items():
                trace.add(expression(value, 'mean') if original.get('original_mean_defined') else missing('作者未定义原均值'), method=method, case_id=case, scope='main', policy='original', metric=name + '/mean')
    return trace
