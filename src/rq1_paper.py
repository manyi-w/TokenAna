"""Published operands are independent of archive-derived reference tables."""
from decimal import Decimal
from pathlib import Path
import tomllib

from .accounting_trace import atom, calc, constant

ROOT = Path(__file__).resolve().parents[1]


def published(method):
    values = tomllib.loads((ROOT / 'RQ1/paper-values.toml').read_text())[method]
    calculation = {}
    for key, value in values.items():
        if isinstance(value, str):
            try:
                numeric = Decimal(value)
            except Exception:
                continue
            locator = values.get('success_locator', values['locator']) if key == 'success_percent' else values['locator']
            calculation[key] = atom(numeric, values['source'], locator + ' / ' + key,
                                    kind='paper_reported', description='论文印刷原值及精度；不以归档复算值覆盖')
    return {**values, 'calculation': calculation}


def comparison_cost(method, *, scope='all'):
    reference = published(method)
    nodes = reference['calculation']
    if method == 'run_free':
        return None
    if method == 'turn_control':
        return nodes['cost_total_usd'] if scope == 'main' else None
    key = 'cost_main_mean_usd' if method == 'attn_compress' and scope == 'main' else 'cost_total_mean_usd'
    if method == 'attn_compress' and scope != 'main':
        return None  # full API scope does not price the local forward
    return calc('multiply', nodes[key], constant(reference['tasks'], reference['source'],
                '论文固定题数；从已舍入单题费用推导总费用'), description='论文报告单题费用 × 论文题数')


def paper_deltas(method, original, corrected, *, scope='all'):
    reference = comparison_cost(method, scope=scope)
    if reference is None:
        return dict(paper_cost=None, reason='论文未报告或费用范围不同，不能直接比较')
    def differences(value):
        if value.get('number_type') != 'decimal':
            value = calc('decimal', value, description='与论文十进制印刷费用比较，保留原计算结果的显示精度')
        delta = calc('subtract', value, reference, description='新运行费用 − 论文报告费用')
        pct = calc('multiply', calc('divide', delta, reference), constant(100, __file__, '百分比'))
        return {'difference_usd': delta, 'difference_percent': pct}
    return {'paper_cost': reference, 'original_vs_paper': differences(original),
            'corrected_vs_paper': differences(corrected),
            'interpretation': '包含轨迹、环境与模型版本差异，不等于纯统计校正效应。'}
