"""Offline, reproducible list-price token accounting. Money is decimal text."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import tomllib

DEFAULT_PRICING = Path(__file__).resolve().parents[1] / 'config/pricing.toml'
NOTE = '按配置官网价估算的 API token 费用；不是网关账单，不包含机器、工具服务费或优惠额度。'
RATE_KEYS = {'input', 'output', 'cache_read', 'cache_write', 'cache_write_5m',
             'cache_write_1h', 'cache_read_implicit', 'cache_read_explicit',
             'long_input_multiplier', 'long_output_multiplier'}


def decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid pricing number') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Prices must be finite and non-negative')
    return result


def money(value):
    return format(value, 'f') if value is not None else None


def validate_pricing(value):
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('currency') != 'USD':
        raise ValueError('Unsupported pricing configuration')
    if decimal(value.get('cny_per_usd')) <= 0:
        raise ValueError('cny_per_usd must be positive')
    if not isinstance(value.get('models'), list):
        raise ValueError('pricing.models must be an array of tables')
    seen = set()
    for profile in value['models']:
        if profile.get('currency') not in ('USD', 'CNY') or profile.get('rule') not in (
                'cache_read', 'cache_write', 'cache_ttl', 'qwen_cache'):
            raise ValueError('Unsupported pricing currency or rule')
        for key in ('name', 'provider', 'source', 'region'):
            if not isinstance(profile.get(key), str) or not profile[key]:
                raise ValueError(f'Price profile requires {key}')
        aliases = profile.get('aliases')
        if not isinstance(aliases, list) or not aliases or any(not isinstance(a, str) or not a for a in aliases):
            raise ValueError('Price aliases must be nonempty strings')
        for alias in aliases:
            identity = profile['provider'], alias
            if identity in seen:
                raise ValueError('Ambiguous provider/model price alias')
            seen.add(identity)
        for key in RATE_KEYS.intersection(profile):
            decimal(profile[key])
        threshold = profile.get('long_context_threshold')
        if threshold is not None and (type(threshold) is not int or threshold < 0):
            raise ValueError('Invalid long-context threshold')
    return value


def load_pricing(path=DEFAULT_PRICING):
    with Path(path).open('rb') as stream:
        return validate_pricing(tomllib.load(stream))


def saved_pricing(run, override=None):
    if isinstance(override, dict):
        return validate_pricing(override)
    if override is not None:
        return load_pricing(override)
    path = Path(run) / 'pricing.json'
    return validate_pricing(json.loads(path.read_text())) if path.exists() else None


def billing_context(meta, request_path=None):
    """Only billing metadata, never prompts, headers or credentials in reports."""
    if not isinstance(meta, dict):
        meta = {}
    result = {key: meta.get(key) for key in ('provider', 'protocol', 'status', 'started_at',
                                           'finished_at', 'cache_control_observed')}
    if type(result['cache_control_observed']) is not bool:
        result['cache_control_observed'] = None
    if request_path is not None:
        try:
            request = json.loads(Path(request_path).read_text())
            ttls = []
            def visit(node):
                if isinstance(node, dict):
                    control = node.get('cache_control')
                    if isinstance(control, dict) and control.get('type') == 'ephemeral':
                        ttls.append(control.get('ttl', '5m'))
                    for child in node.values():
                        visit(child)
                elif isinstance(node, list):
                    for child in node:
                        visit(child)
            visit(request)
            result['cache_ttls'] = sorted({ttl for ttl in ttls if isinstance(ttl, str)})
        except (OSError, ValueError, TypeError):
            result['cache_ttls'] = []
    return result


def _request_cost(item, pricing):
    metrics, context = item.get('metrics', {}), item.get('billing_context', {})
    record = {key: item.get(key) for key in ('case_id', 'attempt_id', 'call_id',
              'operation_id', 'observation_id', 'model', 'purpose', 'source')}
    record.update(provider=context.get('provider'), billing_context=context, metrics=metrics,
                  raw_usage=item.get('raw_usage', {}), line_items=[])
    reasons = list(item.get('cost_issues', []))
    profiles = pricing['models'] if pricing else []
    profile = next((p for p in profiles if p['provider'] == context.get('provider')
                    and item.get('model') in p['aliases']), None)
    if not profile:
        reasons.append('pricing snapshot unavailable' if pricing is None else 'provider/model price unavailable')
    else:
        record.update(price_model=profile['name'], source_currency=profile['currency'],
                      price_source=profile['source'], price_region=profile['region'])
        fx = decimal(pricing['cny_per_usd']) if profile['currency'] == 'CNY' else Decimal(1)
        inp, read, write, out = (metrics.get(k) for k in ('input', 'cache_read', 'cache_write', 'output'))
        long = profile.get('long_context_threshold')
        tier_known = long is None or inp is not None
        is_long = tier_known and long is not None and inp > long
        record['context_tier'] = ('long' if is_long else 'short') if tier_known else 'unknown'
        def line(name, count, rate_key, *, additive=True, output=False):
            rate = profile.get(rate_key)
            multiplier = profile.get('long_output_multiplier' if output else 'long_input_multiplier') if is_long else '1'
            rate = decimal(rate) * decimal(multiplier) if rate is not None and multiplier is not None and tier_known else None
            amount = Decimal(count) * rate / 1_000_000 if count is not None and rate is not None else None
            missing = []
            if count is None:
                missing.append(f'{name}: tokens unknown')
            if rate is None:
                missing.append(f'{name}: price or context tier unknown')
            if additive:
                reasons.extend(missing)
            record['line_items'].append(dict(component=name, tokens=count, rate_per_million=money(rate),
                currency=profile['currency'], amount=money(amount), usd_per_million=money(rate / fx) if rate is not None else None,
                amount_usd=money(amount / fx) if amount is not None else None,
                additive=additive, complete=not missing, reasons=missing))

        rule = profile['rule']
        if rule == 'cache_read':
            ordinary = inp - read if inp is not None and read is not None else None
            if metrics.get('cache_miss') is not None:
                ordinary = metrics['cache_miss']
            line('input_uncached', ordinary, 'input')
            line('cache_read', read, 'cache_read')
        elif rule in ('cache_write', 'cache_ttl'):
            ordinary = metrics.get('ordinary_input')
            if ordinary is None and all(v is not None for v in (inp, read, write)):
                ordinary = inp - read - write
            line('input_ordinary', ordinary, 'input')
            line('cache_read', read, 'cache_read')
            if rule == 'cache_write':
                line('cache_write', write, 'cache_write')
            else:
                parts = {ttl: metrics.get('cache_write_' + ttl) for ttl in ('5m', '1h')}
                ttls = context.get('cache_ttls', [])
                if write == 0:
                    parts = dict.fromkeys(parts, 0)
                elif all(v is None for v in parts.values()) and len(ttls) == 1 and ttls[0] in parts:
                    parts = {ttl: write if ttl == ttls[0] else 0 for ttl in parts}
                for ttl, count in parts.items():
                    line('cache_write_' + ttl, count, 'cache_write_' + ttl)
        else:
            explicit = context.get('cache_control_observed')
            if explicit is None:
                reasons.append('Qwen explicit/implicit cache mode unknown')
                line('input_ordinary', None, 'input')
            elif explicit:
                ordinary = inp - read - write if all(v is not None for v in (inp, read, write)) else None
                line('input_ordinary', ordinary, 'input')
                line('cache_read_explicit', read, 'cache_read_explicit')
                line('cache_write_explicit', write, 'cache_write')
            else:
                line('input_uncached', inp - read if inp is not None and read is not None else None, 'input')
                line('cache_read_implicit', read, 'cache_read_implicit')
                if write not in (None, 0):
                    reasons.append('Qwen cache write conflicts with implicit cache mode')
        line('output', out, 'output', output=True)
        # These are children of output, never additional charges.
        reasoning = metrics.get('reasoning')
        line('reasoning', reasoning, 'output', additive=False, output=True)
        line('ordinary_output', out - reasoning if out is not None and reasoning is not None else None,
             'output', additive=False, output=True)
        if any(line['tokens'] is not None and line['tokens'] < 0 for line in record['line_items']):
            raise ValueError('Negative disjoint billing token count')
    amounts = [decimal(line['amount_usd']) for line in record['line_items']
               if line['additive'] and line['amount_usd'] is not None]
    known = sum(amounts, Decimal(0)) if amounts else None
    record.update(total_usd=money(known) if not reasons else None, known_subtotal_usd=money(known),
                  complete=not reasons, reasons=list(dict.fromkeys(reasons)))
    return record


def aggregate(records, denominator, reasons=()):
    gaps = list(reasons) + [f"{r.get('case_id')}/{r.get('operation_id')}: {why}"
                           for r in records for why in r['reasons']]
    values = [decimal(r['known_subtotal_usd']) for r in records if r['known_subtotal_usd'] is not None]
    subtotal = sum(values, Decimal(0)) if values or not gaps else None
    total = subtotal if not gaps else None
    return dict(total_usd=money(total), known_subtotal_usd=money(subtotal),
                mean_usd=money(total / denominator) if total is not None and denominator else None,
                cases_counted=denominator, complete=not gaps, reasons=list(dict.fromkeys(gaps)))


def cost_accounting(usages, pricing):
    if pricing is not None:
        validate_pricing(pricing)
    records, gaps, unique, operations = [], [], {}, {}
    denominator = sum(u['llm_called'] is True for u in usages)
    for case in usages:
        case_id = case['case_id']
        if case['llm_called'] is None or not case.get('coverage_complete', True) or case.get('issues'):
            gaps += [f'{case_id}: incomplete usage coverage', *case.get('issues', [])]
        if case['llm_called'] is True and not case.get('observations'):
            gaps.append(f'{case_id}: no measured usage')
        for item in case.get('observations', []):
            key = tuple(item[k] for k in ('case_id', 'attempt_id', 'call_id', 'observation_id'))
            previous = unique.get(key)
            if previous and {k: v for k, v in previous.items() if k != 'source'} != {k: v for k, v in item.items() if k != 'source'}:
                raise ValueError('Conflicting cost observation identity')
            unique.setdefault(key, item)
        for op in case.get('operations', []):
            key = tuple(op[k] for k in ('case_id', 'attempt_id', 'call_id', 'operation_id'))
            if key in operations and operations[key] != op:
                raise ValueError('Conflicting cost operation identity')
            operations[key] = op
    linked = set()
    for item in unique.values():
        key = tuple(item.get(k) for k in ('case_id', 'attempt_id', 'call_id', 'operation_id'))
        linked.add(key)
        op = operations.get(key, {})
        issues = list(item.get('issues', {}).values()) + list(op.get('issues', []))
        if op.get('complete') is False:
            issues.append('incomplete operation')
        records.append(_request_cost({**item, 'cost_issues': issues}, pricing))
    for key, op in operations.items():
        if key not in linked and op.get('inference', True):
            records.append(_request_cost({**op, 'cost_issues': ['response usage missing']}, pricing))
    result = {'rule': 'official-token-cost-v1', 'currency': 'USD', 'note': NOTE,
              'pricing': pricing, **aggregate(records, denominator, gaps), 'requests': records}
    groups = {}
    for name in ('model', 'purpose', 'case_id', 'attempt_id'):
        buckets = defaultdict(list)
        for row in records:
            # Attempts from different cases must not be merged.
            key = f"{row['case_id']}/{row[name]}" if name == 'attempt_id' else row.get(name) or 'unknown'
            buckets[key].append(row)
        groups[name] = {key: aggregate(rows, denominator, gaps) for key, rows in sorted(buckets.items())}
    result['groups'] = groups
    return result


def attach_cost(report, pricing):
    from .cost_reporting import token_ratios
    usages = [case['corrected_usage'] for case in report['cases']]
    report['cost_accounting'] = cost_accounting(usages, pricing)
    for case in report['cases']:
        case['cost_accounting'] = cost_accounting([case['corrected_usage']], pricing)
    for section in [report, *report['cases']]:
        section['token_ratios'] = token_ratios(section['corrected_token_accounting'])
    if report.get('run_status') == 'running':
        for section in [report, *report['cases']]:
            cost = section['cost_accounting']
            for value in [cost, *(v for group in cost['groups'].values() for v in group.values())]:
                value.update(total_usd=None, mean_usd=None, complete=False)
                value['reasons'].append('run is marked running; snapshot may change')


def render_cost(cost):
    if not cost:
        return 'Cost: unavailable in legacy report; re-analyze with a pricing configuration.'
    lines = [NOTE, f"Cost USD: {cost['total_usd']}; known subtotal: {cost['known_subtotal_usd']}; "
             f"mean/case: {cost['mean_usd']}; complete={cost['complete']}"]
    for purpose, group in cost['groups']['purpose'].items():
        lines.append(f"  {purpose}: USD {group['total_usd']} (known {group['known_subtotal_usd']})")
    if cost['reasons']:
        lines.append('Cost incomplete: ' + '; '.join(cost['reasons'][:5]) +
                     (f"; {len(cost['reasons'])} reasons in JSON/CSV" if len(cost['reasons']) > 5 else ''))
    return '\n'.join(lines)
