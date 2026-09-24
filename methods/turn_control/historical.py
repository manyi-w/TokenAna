"""Read the author's final-summary files with its unchanged original reader."""
import json
import os
import ast
import re
from pathlib import Path

from src.source_declarations import declarations


def replay(directory, task_ids, config):
    source = Path(__file__).parent / 'upstream/extract_function_calls.py'
    ns = declarations(source, ['get_last_function_calls', 'get_all_turns',
                              'process_directory', 'calculate_cost', 'calculate_gemini_token', 'calculate_gemini_cost'],
                      {'json': json, 'os': os, 're': re, 'ast': ast, 'selected_instances': set(task_ids)})
    gemini = 'gemini' in directory.name
    calls, output, incoming = ns['process_directory'](str(directory / 'log'), verbose=False)
    latest, attempts = {}, {}
    for path in sorted((directory / 'log').glob('*.txt')):
        case, attempt = path.stem.rsplit('_', 1)
        attempt = int(attempt)
        attempts.setdefault(case, []).append(attempt)
        if case not in latest or attempt > latest[case][0]:
            latest[case] = (attempt, path)
    if set(latest) != set(task_ids):
        raise ValueError('Historical turn_control logs differ from the frozen source population')
    evaluation = json.loads((directory / 'result.json').read_text())
    if set(evaluation['submitted_ids']) != set(task_ids):
        raise ValueError('Historical evaluation population differs from logs')
    rows = []
    for case in task_ids:
        attempt, path = latest[case]
        summary = ns['get_last_function_calls'](str(path))
        counted = bool(summary and summary.get('function_calls') is not None)
        rows.append(dict(task_id=case, source=str(path), attempts=sorted(attempts[case]),
                         selected_attempt=attempt, original_counted=counted, summary_index=len(json.loads(path.read_text())) - 1,
                         input=summary.get('prompt_tokens', 0) if counted else None,
                         output=summary.get('completion_tokens', 0) if counted else None,
                         cache=None, resolved=case in evaluation['resolved_ids']))
        if gemini:
            usage_path = directory / 'output' / ('task_' + case + '.log')
            usages = usage_records(usage_path)
            rows[-1].update(usage=usages, usage_source=str(usage_path))
            if counted:
                rows[-1]['input'], rows[-1]['output'] = ns['calculate_gemini_token'](str(directory / 'log'), case, verbose=False)
    traced = traced_original(rows, config)
    cost = ns['calculate_gemini_cost'](str(directory / 'log'), verbose=False) if gemini else ns['calculate_cost']('gpt', sum(incoming), sum(output))
    if traced["input"] != sum(incoming) or traced["output"] != sum(output) or abs(traced["cost"] - cost) > 1e-8:
        raise ValueError("Traced turn_control arithmetic differs from the original reader")
    return dict(**{k: traced[k] for k in ("original", "calculation")}, rows=rows, counted=len(calls), input=sum(incoming), output=sum(output),
                cache=None, cost=cost,
                resolved=len(set(evaluation['resolved_ids'])),
                original_success_rate=len(set(evaluation['resolved_ids'])) / len(latest),
                original_mean_defined=False,
                pass_at_1=None, pass_at_1_reason='Multiple saved tries exist; evaluation is for selected latest patches, not certified first attempts.',
                diagnostics={'multiple_try_tasks': sum(len(v) > 1 for v in attempts.values()),
                             'saved_try_files': sum(map(len, attempts.values()))})


def usage_records(path):
    """Same last-try/brace parser as the author, retaining operand offsets."""
    content = Path(path).read_text(errors='replace')
    start = content.rfind('current_try:')
    if start < 0:
        raise ValueError('Gemini log lacks current_try')
    records = []
    while match := re.search(r'usage:\s*\{', content[start:]):
        begin = start + match.end() - 1
        end, depth = begin + 1, 1
        while end < len(content) and depth:
            depth += (content[end] == '{') - (content[end] == '}')
            end += 1
        if depth:
            raise ValueError('Truncated historical Gemini usage')
        raw = ast.literal_eval(content[begin:end].replace('null', 'None'))
        records.append({'raw': raw, 'offset': begin})
        start = end
    return records


def traced_original(rows, config):
    """The author's selected-final-summary arithmetic, also used per case."""
    from src.accounting_trace import atom, calc, constant, metric, missing
    selected = [r for r in rows if r['original_counted']]
    denominator = calc('count', *(atom(r['task_id'], config['task_source'], 'instance_id=' + r['task_id'], kind='selection') for r in selected))
    metrics = {}
    for key, field in (('input', 'prompt_tokens'), ('output', 'completion_tokens')):
        values = []
        for r in selected:
            if 'usage' not in r:
                values.append(atom(r[key], r['source'], f"/{r['summary_index']}/{field}",
                    kind='native_aggregate', description='最新 try 的最后一个摘要字段'))
            else:
                for item in r['usage']:
                    node = atom(item['raw'].get(field) or 0, r['usage_source'], f"offset[{item['offset']}]/{field}")
                    if key == 'output' and item['raw'].get('reasoning_tokens') is not None:
                        node = calc('sum', node, atom(item['raw']['reasoning_tokens'], r['usage_source'],
                            f"offset[{item['offset']}]/reasoning_tokens"))
                    values.append(node)
        metrics[key] = metric(calc('sum', *values, description='原最后 try 摘要合计'), denominator, mean_defined=False)
    metrics['total'] = metric(calc('sum', metrics['input']['calculation']['sum'], metrics['output']['calculation']['sum']), denominator, mean_defined=False)
    rule = config['original_reader'] + ':calculate_cost'
    unit = constant(1_000_000, rule, '每百万 token 计价换算')
    cost = calc('sum', *(calc('multiply', calc('divide', metrics[key]['calculation']['sum'], unit),
        atom(float(config['original_price_usd_per_million'][key]), 'RQ1/turn_control/experiment.toml', 'original_price_usd_per_million.' + key,
             kind='frozen_price', description='原函数价格：GPT 输入 2、输出 8 USD/百万')) for key in ('input', 'output')),
        description='原 GPT 费用函数，保留先除后乘的浮点顺序', rule=rule)
    if any('usage' in row for row in rows):
        costs = []
        for row in rows:  # original cost scans ALL output files, independent of final-summary filter
            for item in row.get('usage', []):
                raw = item['raw']
                inp = atom(raw.get('prompt_tokens') or 0, row['usage_source'], f"offset[{item['offset']}]/prompt_tokens")
                out = calc('sum', atom(raw.get('completion_tokens') or 0, row['usage_source'], f"offset[{item['offset']}]/completion_tokens"),
                           atom(raw.get('reasoning_tokens') or 0, row['usage_source'], f"offset[{item['offset']}]/reasoning_tokens"))
                costs.append(gemini_original_cost(inp, out, config))
        cost = calc('sum', *costs, description='原 Gemini 逐请求费用合计；忽略缓存折扣')
    resolved = calc('sum', *(atom(int(r['resolved']), config['source'] + '/result.json', '/resolved_ids contains ' + r['task_id'],
                        kind='evaluation', description='作者已保存评测名单中的成员计 1') for r in rows))
    rate = calc('divide', resolved, calc('count', *(atom(r['task_id'], config['task_source'], 'instance_id=' + r['task_id'], kind='selection') for r in rows)))
    return dict(original=dict(rule='turn-control-historical-final-summary', cases_counted=len(selected), metrics=metrics),
                counted=len(selected), input=metrics['input']['sum'], output=metrics['output']['sum'], cache=None,
                cost=cost['value'], resolved=resolved['value'], original_success_rate=rate['value'],
                pass_at_1=None, pass_at_1_reason='原记录含多次 try，无法认证严格单次 pass@1。',
                calculation=dict(cost=cost, resolved=resolved, original_success_rate=rate, pass_at_1=missing('多次 try；严格 pass@1 未知')),
                diagnostics={}, original_mean_defined=False)


def gemini_original_cost(inp, out, config):
    """Retain original float arithmetic and its output-based output tier."""
    from src.accounting_trace import atom, calc, constant
    source = config['original_reader'] + ':calculate_gemini_cost'
    threshold = constant(200000, source, '原上下文分档阈值')
    unit = constant(1000000, source, '每百万 token')
    terms = []
    for key, node, multiplier in (('input', inp, 2), ('output', out, 1.5)):
        rate = atom(float(config['original_price_usd_per_million'][key]), 'RQ1/turn_control/experiment.toml',
                    '/original_price_usd_per_million/' + key, kind='frozen_price')
        tier = calc('select', calc('greater', node, threshold), calc('multiply', rate, constant(multiplier, source)), rate)
        terms.append(calc('multiply', calc('divide', node, unit), tier))
    return calc('sum', *terms, description='原 Gemini 输入/输出各自判断分档，无缓存折扣')
