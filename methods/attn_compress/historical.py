"""Original Table 1 arithmetic and its operands, without a second cost formula."""
from decimal import Decimal
from .adapter import AttnCompress
from src.accounting_trace import atom, calc, constant, expression, number, case_source


def replay(documents, cases, config):
    original = AttnCompress().original_accounting(cases)
    incoming, outgoing, cache = (expression(original['metrics'][k]) for k in ('input', 'output', 'cache_read'))
    price = {k: atom(Decimal(v), 'RQ1/attn_compress/experiment.toml', 'original_price_usd_per_million.' + k,
                    kind='frozen_price', description='冻结作者价表，USD/百万 token')
             for k, v in config['original_price_usd_per_million'].items()}
    extra_in, extra_out = (calc('sum', *(case_source(c, 'metrics/' + key, documents[c.case_id]['metrics'][key]) for c in cases),
                                   description='原 analysis 计数累加，不代表本地 forward')
                          for key in ('analysis_prompt_tokens', 'analysis_completion_tokens'))
    cost = calc('divide', calc('sum',
        calc('multiply', calc('sum', calc('subtract', incoming, cache), extra_in), price['input']),
        calc('multiply', calc('sum', outgoing, extra_out), price['output']),
        calc('multiply', cache, price['cache_read'])),
        constant(1_000_000, 'methods/attn_compress/historical.py:replay', '每百万价格单位换算'),
        description='原费用：扣估计缓存后的输入 + 原 analysis 输入、输出、估计缓存各按原价计费')
    resolved = calc('sum', *(calc('equal', case_source(c, 'result/val', documents[c.case_id]['result']['val']),
        constant('pass', config['original_reader'] + ':pass'), description='作者评测结果') for c in cases))
    rate = calc('divide', resolved, calc('count', *(case_source(c, 'case_id', c.case_id, kind='selection') for c in cases)),
                description='原成功题数 / 原任务数')
    return dict(original=original, counted=len(documents), input=number(incoming), output=number(outgoing),
                cache=number(cache), cost=cost['value'], resolved=number(resolved),
                original_success_rate=number(rate), original_mean_defined=True,
                pass_at_1=number(rate), pass_at_1_reason='One archived candidate per task; author evaluation, no fresh evaluation.',
                calculation=dict(cost=cost, resolved=resolved, original_success_rate=rate, pass_at_1=rate),
                diagnostics={'cache_semantics': 'estimated common-prefix characters / 4; not API cache usage',
                             'auxiliary_input': number(extra_in), 'auxiliary_output': number(extra_out),
                             'local_compression_compute': 'not included in generation API tokens or API cost'})
