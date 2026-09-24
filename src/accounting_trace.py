"""Small arithmetic expressions used by accounting and its evidence exports.

Expressions compute the actual values; reports serialize the same expressions.
No source payload, prompt, credential, or inferred token count is copied here.
"""
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_path(path):
    text = str(path)
    if text.startswith(str(ROOT) + '/'):
        return text[len(str(ROOT)) + 1:]
    return text


def atom(value, source, locator, *, kind='native_aggregate', description='', **identity):
    return dict(operation='read', value=str(value) if isinstance(value, Decimal) else value,
                number_type='decimal' if isinstance(value, Decimal) else 'native',
                source=source_path(source), locator=locator, evidence_kind=kind,
                description=description, inputs=[], **identity)


def number(expression):
    value = expression['value']
    return Decimal(value) if value is not None and expression.get('number_type') == 'decimal' else value


def evaluate(operation, values):
    if operation == 'decimal':
        return Decimal(str(values[0])) if values[0] is not None else None
    if operation == 'is_known':
        return values[0] is not None
    if operation == 'contains':
        return values[1] in values[0]
    if operation == 'count':
        return len(values)
    if operation == 'guard':
        return values[0] if values[1] else None
    if operation == 'select':
        return values[1] if values[0] else values[2]
    if operation == 'identity':
        return values[0]
    if operation == 'round3':
        return round(float(values[0]), 3) if values[0] is not None else None
    if operation == 'format3':
        return f'{values[0]: .3f}' if values[0] is not None else None
    if operation == 'equal':
        return values[0] == values[1]
    if operation == 'all':
        return all(values)
    if any(v is None for v in values):
        return None
    if operation == 'sum':
        return sum(values)
    if operation == 'greater':
        return values[0] > values[1]
    if operation == 'subtract':
        return values[0] - values[1]
    if operation == 'multiply':
        result = 1
        for value in values:
            result *= value
        return result
    if operation in ('divide', 'floor_divide'):
        if not values[1]:
            return None
        return values[0] / values[1] if operation == 'divide' else values[0] // values[1]
    raise ValueError(f'Unsupported accounting operation: {operation}')


def calc(operation, *inputs, description='', rule='src/accounting_trace.py:evaluate'):
    value = evaluate(operation, [number(v) for v in inputs])
    return dict(operation=operation, inputs=list(inputs),
                value=str(value) if isinstance(value, Decimal) else value,
                number_type='decimal' if isinstance(value, Decimal) else 'native',
                description=description, rule=rule, evidence_kind='derived')


def constant(value, rule, description='规则常量'):
    file, _, locator = rule.partition(':')
    if file.endswith('.ipynb') and (ROOT / file).is_file():
        notebook = json.loads((ROOT / file).read_text())
        indices = [i for i, c in enumerate(notebook.get('cells', []))
                   if locator in ''.join(c.get('source', []))]
        if indices:
            locator = ','.join(f'/cells/{i}/source' for i in indices) + ' (contains ' + locator + ')'
    return atom(value, file, locator or 'rule', kind='author_assumption' if '假设' in description or '假定' in description else 'rule_constant', description=description)


def missing(description, source='src/accounting_trace.py', locator='missing'):
    return atom(None, source, locator, kind='missing', description=description)


def metric(expression, denominator=None, *, complete=True, reasons=(), floor=False,
           mean_defined=True, strict=False):
    subtotal = expression
    valid = constant(bool(complete), 'src/accounting_trace.py:metric', '逐指标完整性：' + '; '.join(reasons))
    total = calc('guard', subtotal, valid, description='完整总量；证据不完整时未知') if strict else subtotal
    mean = (calc('floor_divide' if floor else 'divide', total, denominator, description='总量除以该口径的任务分母')
            if denominator is not None and mean_defined else missing('原规则未定义均值'))
    return dict(sum=total['value'], mean=mean['value'], complete=bool(complete), reasons=list(reasons),
                **({'known_subtotal': subtotal['value']} if strict else {}),
                calculation={'sum': total, 'mean': mean, **({'known_subtotal': subtotal} if strict else {})})


def expression(metric, key='sum', *, source='accounting.json', locator='metric'):
    node = metric.get('calculation', {}).get(key)
    if node is not None:
        if node['value'] != metric.get(key) and metric.get(key) is None:
            return calc('guard', node, constant(False, 'src/accounting_trace.py:expression', '报告已标记不完整/运行中'))
        return node
    return atom(metric.get(key), source, locator + '/' + key,
        kind='missing' if metric.get(key) is None else 'native_aggregate',
        description='保留报告字段；源计算未留存' if metric.get(key) is not None else '该口径未提供此项')


def case_source(case, field, value, *, kind='native_aggregate', description=''):
    refs = case.method_data.get('_sources', {})
    ref = refs.get(field, {})
    return atom(value, ref.get('source', case.method_data.get('_source', 'original-evidence')),
                ref.get('locator', '/' + field), kind=ref.get('kind', kind), description=description,
                case_id=case.case_id)


def event_source(case, index, event, field):
    node = event.get('_calculation', {}).get(field)
    if node:
        return node
    usage = event.get('usage', {})
    ref = event.get('_source', {})
    present = field in usage
    return atom(usage.get(field, 0), ref.get('source', case.method_data.get('_source', 'original-evidence')),
                ref.get('locator', f'/trace/{index}') + '/usage/' + field,
                kind='native_aggregate' if present else 'rule_default',
                description='原生 usage 字段' if present else '原算法缺失字段默认零；不是实测零',
                case_id=case.case_id)


def flatten(trace):
    """One CSV row per operand; even leaves and missing steps remain visible."""
    for step in trace['steps']:
        operands = step.get('operands') or [None]
        for index, operand in enumerate(operands):
            yield {**{k: v for k, v in step.items() if k != 'operands'},
                   'operand_index': index if operand else None,
                   **{'operand_' + k: v for k, v in (operand or {}).items()}}


class Trace:
    def __init__(self):
        self.steps, self.comparisons = [], []
        self._seen = {}

    def add(self, node, **context):
        key = id(node)
        if key in self._seen:
            return self._seen[key]
        operands = []
        for child in node.get('inputs', []):
            ref = self.add(child, **context)
            operands.append(dict(step_id=ref, value=child['value'], source=child.get('source'),
                                 locator=child.get('locator'), evidence_kind=child.get('evidence_kind')))
        identity = f'step-{len(self.steps) + 1:08d}'
        self._seen[key] = identity
        fields = {k: v for k, v in node.items() if k != 'inputs'}
        for key in fields.keys() & context.keys():
            if fields[key] != context[key]:
                fields['operand_' + key] = fields[key]
        values = [str(o['value']) if o['value'] is not None else '未知' for o in operands]
        op = node['operation']
        symbols = {'sum': ' + ', 'subtract': ' − ', 'multiply': ' × ', 'divide': ' ÷ ', 'floor_divide': ' // ', 'equal': ' == '}
        formula = symbols[op].join(values) if op in symbols else op + '(' + ', '.join(values) + ')' if operands else str(node.get('locator', op))
        self.steps.append({'step_id': identity, **fields, **context, 'formula': formula,
                           'complete': node.get('metric_complete', node['value'] is not None), 'operands': operands})
        # Keep expression objects alive so Python cannot reuse their IDs.
        self._seen[(identity,)] = node
        return identity

    def compare(self, old, cache, corrected, *, reason='', **context):
        nodes = {'original': old, 'cache_only': cache, 'corrected_v2': corrected}
        def comparable(node):
            return calc('guard', node, constant(False, 'src/accounting_trace.py:compare', '该指标不完整，不能计算完整差额')) if node.get('metric_complete') is False else node
        old, cache, corrected = map(comparable, (old, cache, corrected))
        if any(node.get('number_type') == 'decimal' for node in (old, cache, corrected)):
            old, cache, corrected = (calc('decimal', node, description='跨口径比较时将原浮点显示值转十进制；保留原列计算')
                                     if isinstance(number(node), float) else node for node in (old, cache, corrected))
        nodes.update(cache_delta=calc('subtract', cache, old, description='仅缓存校正减 original'),
                     total_delta=calc('subtract', corrected, old, description='完整 v2 减 original'),
                     remaining_delta=calc('subtract', corrected, cache,
                         description='完整 v2 减仅缓存校正；不是独立因果归因'))
        row = {**context, 'explanation': reason}
        for key, node in nodes.items():
            row[key] = node['value']
            row[key + '_step'] = self.add(node, policy=key, **context)
            row[key + '_known'] = node['value'] is not None
            row[key + '_complete'] = node.get('metric_complete', node['value'] is not None)
        self.comparisons.append(row)

    def payload(self):
        return dict(schema_version=2, steps=self.steps, comparisons=self.comparisons)


def export_trace(trace, output):
    from .tabular import csv_file, write_text
    output = Path(output)
    value = trace.payload() if isinstance(trace, Trace) else trace
    write_text(output / 'accounting-trace.json', json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    csv_file(output / 'accounting-steps.csv', list(flatten(value)), legacy=True)
    csv_file(output / 'accounting-comparison.csv', value['comparisons'], legacy=True)


def merge_traces(items):
    result = dict(schema_version=2, steps=[], comparisons=[])
    for identity, value in items:
        prefix = identity + '/'
        for step in value['steps']:
            result['steps'].append({**step, 'configuration_id': identity, 'step_id': prefix + step['step_id'],
                'operands': [{**o, 'step_id': prefix + o['step_id']} for o in step['operands']]})
        for row in value['comparisons']:
            result['comparisons'].append({**row, 'configuration_id': identity,
                **{k: prefix + v for k, v in row.items() if k.endswith('_step')}})
    return result


def export_trace_bundle(items, output):
    """Stream independent case/configuration graphs into one bounded-memory bundle."""
    import shutil
    from .tabular import append_csv
    output = Path(output)
    steps_path = output / '.accounting-steps.json.tmp'
    comparisons_path = output / '.accounting-comparison.json.tmp'
    target = output / 'accounting-trace.json'
    for name in ('accounting-steps.csv', 'accounting-comparison.csv'):
        (output / name).unlink(missing_ok=True)
    try:
        with steps_path.open('w') as steps, comparisons_path.open('w') as comparisons:
            first_step = first_comparison = True
            for identity, value in items:
                merged = merge_traces([(identity, value)])
                for key, stream in (('steps', steps), ('comparisons', comparisons)):
                    for row in merged[key]:
                        first = first_step if key == 'steps' else first_comparison
                        if not first:
                            stream.write(',\n')
                        json.dump(row, stream, ensure_ascii=False)
                        if key == 'steps':
                            first_step = False
                        else:
                            first_comparison = False
                append_csv(output / 'accounting-steps.csv', list(flatten(merged)))
                append_csv(output / 'accounting-comparison.csv', merged['comparisons'])
        temporary = target.with_suffix('.json.tmp')
        with temporary.open('w') as dest:
            dest.write('{"schema_version":2,"steps":[\n')
            with steps_path.open() as src:
                shutil.copyfileobj(src, dest)
            dest.write('\n],"comparisons":[\n')
            with comparisons_path.open() as src:
                shutil.copyfileobj(src, dest)
            dest.write('\n]}\n')
        temporary.replace(target)
    finally:
        steps_path.unlink(missing_ok=True)
        comparisons_path.unlink(missing_ok=True)


def unavailable_trace(case_id, reason):
    trace = Trace()
    for name in ('input', 'output', 'total', 'cache_read', 'cache_write', 'cost_usd', 'resolved'):
        trace.compare(missing(reason), missing(reason), missing(reason), case_id=case_id,
                      scope='reported', metric=name, reason=reason)
    return trace.payload()
