"""Preserve exporter arithmetic, with the very same expressions as evidence."""
from decimal import Decimal
from .adapter import AgentDiet
from src.accounting_trace import atom, calc, constant, expression, number, case_source


def replay(documents, cases, config):
    original = AgentDiet().original_accounting(cases)
    incoming, outgoing = (expression(original['metrics'][k]) for k in ('input', 'output'))
    helper = original['helper_calculation']
    price = {k: atom(Decimal(v), 'RQ1/agent_diet/experiment.toml', 'original_price_usd_per_million.' + k,
                    kind='frozen_price', description='冻结作者价表，USD/百万 token')
             for k, v in config['original_price_usd_per_million'].items()}
    unit = constant(1_000_000, 'methods/agent_diet/historical.py:replay', '每百万 token 价格的单位换算')
    main_cost = calc('divide', calc('sum', calc('multiply', incoming, price['cache_read']),
        calc('multiply', incoming, constant(Decimal('.02'), config['original_reader'] + ':0.02', '作者额外 2% 普通输入计价假设'), price['input']),
        calc('multiply', outgoing, price['output'])), unit, description='原主费用：全部输入缓存价 + 2% 普通价 + 输出价')
    helper_cost = calc('divide', calc('sum', calc('subtract',
        calc('multiply', helper['before'], price['helper_input']),
        calc('multiply', helper['assumed_cache'], calc('subtract', price['helper_input'], price['helper_cache_read']))),
        calc('multiply', helper['output'], price['helper_output'])), unit,
        description='原辅助费用：原输入费用减假定缓存折扣，再加输出费用')
    cost = calc('sum', main_cost, helper_cost, description='主模型费用 + 辅助费用')
    results = [calc('equal', case_source(c, 'result/val', documents[c.case_id]['result']['val']),
                    constant('pass', config['original_reader'] + ':pass'), description='复用作者评测结果') for c in cases]
    resolved = calc('sum', *results, description='成功题数')
    denominator = calc('count', *(case_source(c, 'case_id', c.case_id, kind='selection') for c in cases))
    rate = calc('divide', resolved, denominator, description='作者成功题数 / 原任务数；没有重新评测')
    return dict(original=original, counted=len(documents), input=number(incoming), output=number(outgoing),
                cache=None, cost=cost['value'], resolved=number(resolved),
                original_success_rate=number(rate), original_mean_defined=False,
                pass_at_1=number(rate), pass_at_1_reason='One archived candidate per task; author evaluation, no fresh evaluation.',
                calculation=dict(cost=cost, main_cost=main_cost, helper_cost=helper_cost, resolved=resolved, original_success_rate=rate, pass_at_1=rate),
                diagnostics={'main_cost_usd': main_cost['value'], 'helper_cost_usd': helper_cost['value'],
                    'helper_input_before_assumed_cache': number(helper['before']),
                    'helper_input_after_assumed_cache': number(helper['input']),
                    'helper_output': number(helper['output']), 'helper_assumed_cache': number(helper['assumed_cache']),
                    'cache_semantics': 'no measured main cache; helper assumes 492 cached tokens per analysis',
                    'main_cost_formula': 'input * cache_price + input * .02 * normal_price + output * output_price',
                    'token_scope': 'main and helper columns remain separate'})
