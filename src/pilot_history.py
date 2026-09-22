"""Read saved pilot identities without loading credentials or invoking agents."""
import json
import platform
from pathlib import Path


def identity(row, settings):
    from .pilot import agent_options, BUDGET
    model = settings.get('models', {}).get(row['model'])
    if not model or not all(model.get(k) for k in ('provider', 'model_id', 'protocol', 'base_url')):
        return None
    model = {k: model[k] for k in ('provider', 'model_id', 'protocol', 'base_url')}
    options = agent_options(row['agent'], settings['models'][row['model']], settings)
    helper = settings.get('agent_diet_auxiliary', {}) if row['method'] == 'agent_diet' else {}
    return dict(selection={**{k: row[k] for k in ('case', 'model', 'agent', 'method')},
                           'dataset': row.get('dataset', 'deepswe')},
                model=model, agent_options=options,
                helper={k: v for k, v in helper.items() if k != 'api_key_env'},
                budget=BUDGET.get(row['model']) if row['method'] == 'turn_control' else None,
                images=settings.get('images', {}), runtime_paths=settings.get('runtime_paths', {}))


def filter_history(rows, settings, roots):
    """Return pending rows and references; never count reused work as new cost."""
    histories, seen, warnings = [], set(), []
    for root in roots:
        manifests = list(Path(root).glob('*/pilot.json'))
        for pointer in Path(root).glob('.pilot-history/*.json'):
            try:
                manifests.append(Path(json.loads(pointer.read_text())['manifest']))
            except (OSError, ValueError, KeyError, TypeError):
                warnings.append(f'Cannot inspect history pointer {pointer}')
        for manifest in sorted(manifests):
            resolved = manifest.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                data = json.loads(manifest.read_text())
                if data.get('host_system', platform.system()) != platform.system():
                    continue
                for row in data['rows']:
                    parent = resolved.parent / 'combinations' / row['id']
                    result, state = parent / 'result.json', parent / 'run/state.json'
                    status = None
                    if result.exists():
                        status = json.loads(result.read_text()).get('status')
                    elif state.exists():
                        status = json.loads(state.read_text()).get('status')
                    elif (parent / 'controller.json').exists():
                        status = 'started'
                    elif data.get('dedup_reservation'):
                        status = 'reserved; use --resume to continue'
                    if status and status != 'pending':
                        key = identity(row, data['settings'])
                        if key is not None:
                            histories.append((key, str(resolved.parent), status))
            except (OSError, ValueError, KeyError, TypeError) as error:
                warnings.append(f'Cannot inspect history {manifest}: {type(error).__name__}')
    pending, skipped = [], []
    for row in rows:
        key = identity(row, settings)
        match = next((h for h in histories if key is not None and h[0] == key), None)
        if match:
            skipped.append(dict(row=row, source=match[1], status=match[2]))
        else:
            pending.append(row)
    return pending, skipped, warnings


def register_output(root, output):
    """Keep explicit outputs discoverable on subsequent default launches."""
    from hashlib import sha256
    from .records import write_json
    manifest = str(output.resolve() / 'pilot.json')
    directory = Path(root) / '.pilot-history'
    directory.mkdir(exist_ok=True)
    write_json(directory / (sha256(manifest.encode()).hexdigest() + '.json'), {'manifest': manifest})
