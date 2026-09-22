"""Freeze the author's linear percentile + ceiling budgets from native summaries."""
import json
import math
from pathlib import Path

from .components import Component
from .loading import load_adapter
from .records import write_json


def percentile(values, q):
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def freeze_budget(output, rows, *, configuration_id):
    """Read every selected baseline outcome; never generate a baseline implicitly."""
    selected = [r for r in rows if r.get('configuration_id') == configuration_id]
    if not selected or any(r['method'] != 'baseline' for r in selected):
        raise ValueError('budget requires an explicit baseline configuration')
    samples, excluded = [], []
    for row in selected:
        run = Path(output) / 'combinations' / row['id'] / 'run'
        if not (run / 'state.json').exists():
            raise ValueError(f"Baseline has not been attempted: {row['case']}")
        state = json.loads((run / 'state.json').read_text())
        saved = state['tasks'].get(row['case'], {})
        if state.get('status') == 'running' or saved.get('stage') in ('preparing', 'generating'):
            raise ValueError(f"Baseline is incomplete: {row['case']}")
        component = json.loads((run / 'config.json').read_text())['agent']
        component['path'] = Path(component['path'])
        reader = load_adapter(Component(**component)).read_final_summary_case
        artifacts = list((run / saved.get('directory', '') / 'artifacts').glob('call-*'))
        summary = reader(artifacts[0], row['case']).final_summary if len(artifacts) == 1 else None
        if summary is None or not summary.finished or summary.function_calls is None:
            excluded.append({'case_id': row['case'], 'reason': 'no finished native function-call summary'})
        else:
            samples.append({'case_id': row['case'], 'function_calls': summary.function_calls,
                            'source': str(artifacts[0]), 'summary_source': summary.source})
    values = [s['function_calls'] for s in samples]
    if not values:
        raise ValueError('No original-rule function-call samples available for budget calibration')
    initial, final = (math.ceil(percentile(values, q)) for q in (.5, .75))
    if initial <= 0:
        raise ValueError('Original percentile yields a zero budget; do not silently change the algorithm')
    budget = dict(version=1, initial=initial, final=final, baseline_id=configuration_id,
                  algorithm='linear-percentile-ceil', selected=[r['case'] for r in selected],
                  samples=samples, excluded=excluded)
    path = Path(output) / 'budgets' / (configuration_id + '.json')
    if path.exists():
        if json.loads(path.read_text()) != budget:
            raise ValueError('Frozen baseline budget differs; keep original data or start another study')
    else:
        path.parent.mkdir(exist_ok=True)
        write_json(path, budget)
    return budget
