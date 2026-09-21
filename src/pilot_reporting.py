"""Detailed pilot summaries and offline refresh; never execute an experiment."""
from collections import defaultdict
import fcntl
import json
from pathlib import Path
import shutil

from .tabular import legacy_csv as _csv, markdown as _markdown, write_text as _text
from .cost_reporting import cost_rows, token_ratios
from .pricing import aggregate, load_pricing, saved_pricing
from .records import write_json
from .telemetry import totals


def run_directory(rows, root, *, create=False):
    """Reserve the first available human-readable name atomically on execution."""
    def label(key):
        values = sorted({row[key] for row in rows})
        return values[0].replace('_', '-') if len(values) == 1 else f'{len(values)}-{key}s'
    datasets = {r.get('dataset', 'deepswe') for r in rows}
    dataset = next(iter(datasets)) if len(datasets) == 1 else 'study'
    stem = '__'.join([label('model'), label('agent'), label('method'), dataset])
    cases = len({r['case'] for r in rows})
    if cases > 1:
        stem += f'__{cases}-cases'
    if create:
        root.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        path = root / f'{stem}__{index:02d}'
        if create:
            try:
                path.mkdir()
                return path
            except FileExistsError:
                pass
        elif not path.exists() and not path.is_symlink():
            return path
        index += 1


def _report(parent, result):
    path = Path(result['analysis_directory']) / 'accounting.json' if result.get('analysis_directory') else parent / 'run/accounting.json'
    return json.loads(path.read_text()) if path.exists() else {}


def summarize(output, rows, reports=None):
    records, flat_rows, requests, lines = [], [], [], []
    for row in rows:
        parent = output / 'combinations' / row['id']
        result = json.loads((parent / 'result.json').read_text()) if (parent / 'result.json').exists() else {'status': 'pending'}
        report = reports[row['id']] if reports and row['id'] in reports else _report(parent, result)
        record = {**row, **result, 'directory': str(parent),
                  'original': report.get('original_token_accounting'),
                  'corrected': report.get('corrected_token_accounting'),
                  'overhead': report.get('overhead'), 'timing_seconds': totals(parent),
                  'cost_accounting': report.get('cost_accounting'),
                  'token_ratios': token_ratios(report.get('corrected_token_accounting'))}
        if report.get('analysis', {}).get('output'):
            record['analysis_directory'] = report['analysis']['output']
        records.append(record)
        flat = {key: record.get(key) for key in ('id', 'dataset', 'method', 'agent', 'model', 'case', 'status', 'resolved', 'directory', 'controller_cleanup_error')}
        for policy in ('original', 'corrected'):
            account = record[policy] or {}
            flat[policy + '_cases_counted'] = account.get('cases_counted')
            for metric in dict.fromkeys(['input', 'output', 'total', 'cache_read', 'cache_write', 'reasoning', *account.get('metrics', {})]):
                value = account.get('metrics', {}).get(metric, {})
                flat[f'{policy}_{metric}'] = value.get('sum')
                for key in ('mean', 'complete', 'reasons'):
                    flat[f'{policy}_{metric}_{key}'] = value.get(key)
        cost = record['cost_accounting'] or {}
        for key in ('total_usd', 'known_subtotal_usd', 'mean_usd', 'complete', 'reasons'):
            flat['cost_' + key] = cost.get(key)
        for purpose, group in cost.get('groups', {}).get('purpose', {}).items():
            flat[f'cost_{purpose}_usd'] = group['total_usd']
            flat[f'cost_{purpose}_known_usd'] = group['known_subtotal_usd']
        for name, value in record['token_ratios'].items():
            flat[name] = value['value']
            flat[name + '_complete'] = value['complete']
            flat[name + '_reasons'] = value['reasons']
        flat.update({'seconds_' + key: value for key, value in record['timing_seconds'].items()})
        for purpose, group in (record['overhead'] or {}).get('corrected', {}).items():
            for metric, value in group['metrics'].items():
                flat[f'overhead_{purpose}_{metric}'] = value.get('sum')
                flat[f'overhead_{purpose}_{metric}_complete'] = value.get('complete')
        # Preserve the previous CSV columns for existing consumers as well.
        flat.update(timing_seconds=record['timing_seconds'], overhead=record['overhead'])
        flat_rows.append(flat)
        req, components = cost_rows(cost, {key: row[key] for key in ('id', 'method', 'agent', 'case')})
        requests.extend(req)
        lines.extend(components)
    summaries = [{**(r['cost_accounting'] or dict(known_subtotal_usd=None, total_usd=None,
                      complete=False, reasons=['cost accounting unavailable'])), 'case_id': r['id']} for r in records]
    denominator = sum(s.get('cases_counted', 0) for s in summaries)
    overall = aggregate(summaries, denominator)
    groups = {}
    for dimension in ('model', 'agent', 'method'):
        grouped = defaultdict(list)
        for record, cost in zip(records, summaries):
            grouped[record[dimension]].append(cost)
        groups[dimension] = {name: aggregate(values, sum(v.get('cases_counted', 0) for v in values))
                             for name, values in grouped.items()}
    timing_note = 'Nested and overlapping spans are not additive; cost overhead is already included in total.'
    write_json(output / 'summary.json', dict(version=2, cases=records, cost_accounting=overall,
        cost_groups=groups, timing_note=timing_note,
        mean_note='Pilot denominator counts called combination/case pairs, including failures.'))
    for name, values in (('summary', flat_rows), ('requests', requests), ('costs', lines)):
        fields = list(dict.fromkeys(key for value in values for key in value)) or ['id', 'total_usd', 'complete']
        _csv(output / (name + '.csv'), values, fields)
    headline = ['model', 'agent', 'method', 'case', 'status', 'resolved', 'cost_total_usd', 'cost_known_subtotal_usd', 'cost_complete']
    token_fields = ['model', 'agent', 'method', 'case', 'corrected_input', 'corrected_output', 'corrected_total',
                    'corrected_cache_read', 'corrected_cache_miss', 'corrected_cache_write',
                    'corrected_reasoning', 'corrected_ordinary_output', 'cache_hit_rate', 'reasoning_share']
    _text(output / 'summary.md', '# Run summary\n\n'
          + f"Estimated API token cost: **USD {overall['total_usd']}**; known subtotal: {overall['known_subtotal_usd']}; "
          + f"complete={overall['complete']}.\n\n" + _markdown(flat_rows, headline)
          + '\n\n## Token details\n\n' + _markdown(flat_rows, token_fields)
          + '\n\n' + timing_note + '\n\n[All metrics and reasons](summary.csv) · [Requests](requests.csv) · [Cost components](costs.csv) · [Full data](summary.json)\n')
    return records


def print_summary(records):
    for record in records:
        metrics = (record.get('corrected') or {}).get('metrics', {})
        values = ', '.join(f"{key}={value.get('sum')}" for key, value in metrics.items())
        cost = record.get('cost_accounting') or {}
        print(f"{record['model']} / {record['agent']} / {record['method']} / {record['case']}: {values or 'usage unknown'}\n"
              f"  Cost USD={cost.get('total_usd')}; known={cost.get('known_subtotal_usd')}; complete={cost.get('complete', False)}")


def refresh(output, pricing=None):
    """Rebuild reports offline under the pilot lock; do not change execution state."""
    from .analysis import analyze_run
    output = Path(output).resolve()
    manifest = json.loads((output / 'pilot.json').read_text())
    with (output / '.pilot.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('This pilot is active; stop it before refreshing reports') from None
        for row in manifest['rows']:
            state = output / 'combinations' / row['id'] / 'run/state.json'
            if state.exists() and json.loads(state.read_text()).get('status') == 'running':
                raise ValueError('Run is marked running; cannot refresh a changing pilot')
        snapshot = saved_pricing(output, pricing)
        if snapshot is None:
            snapshot = load_pricing()
        reports = {}
        for row in manifest['rows']:
            run = output / 'combinations' / row['id'] / 'run'
            if (run / 'state.json').exists():
                reports[row['id']] = analyze_run(run, pricing=snapshot)['report']
        index = 1
        while True:
            backup = output / f'summary-backup-{index:02d}'
            try:
                backup.mkdir()
                break
            except FileExistsError:
                index += 1
        for name in ('summary.json', 'summary.csv', 'summary.md', 'requests.csv', 'costs.csv'):
            if (output / name).exists():
                shutil.copy2(output / name, backup / name)
        return summarize(output, manifest['rows'], reports)
