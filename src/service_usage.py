"""Incremental auxiliary service records. Each inference usage has exactly one owner."""

from contextlib import contextmanager
import json
from pathlib import Path
import time
from uuid import uuid4

from .accounting import CaseUsage, METRICS, UsageObservation, UsageOperation
from .overhead import duration, validate_attribution
from .records import write_json
from .usage_protocols import normalize_usage


@contextmanager
def record_service(artifacts, *, purpose='compression_service', model=None,
                   parent_call_id=None, inference=True, identity=None):
    """Record one service attempt, including exceptions and interruption.

    Caller sets result['raw_usage'], ['protocol'], ['provider'] only from actual
    service usage. Text token counts belong in result['effects'], never raw_usage.
    inference=False is only for an orchestration operation whose inference is
    recorded separately through an auxiliary model channel; no token zero inferred.
    """
    attribution = validate_attribution({**(identity or {}), 'purpose': purpose, **(
        {'parent_call_id': parent_call_id} if parent_call_id is not None else {})})
    if type(inference) is not bool:
        raise ValueError('inference must be boolean')
    if model is not None and (not isinstance(model, str) or not model):
        raise ValueError('model must be a nonempty string or None')
    root = Path(artifacts.host) / 'service-records'
    root.mkdir(exist_ok=True)
    path = root / (uuid4().hex + '.json')
    record = {'version': 1, 'attribution': attribution, 'model': model,
              'inference': inference, 'complete': False, 'started_at': time.time()}
    write_json(path, record)
    result = {}
    started = time.monotonic()
    try:
        yield result
        record['complete'] = True
    except BaseException as error:
        record['error_type'] = type(error).__name__
        raise
    finally:
        record.update({key: result[key] for key in ('raw_usage', 'protocol', 'provider', 'effects', 'local_compute') if key in result})
        record['duration_sec'] = time.monotonic() - started
        record['finished_at'] = time.time()
        write_json(path, record)


def read_services(directory, *, case_id, attempt_id, call_id, local_compute=None):
    observations, operations, issues = [], [], []
    for path in sorted(Path(directory).glob('*.json')):
        data, attr = {}, {}
        metrics = dict.fromkeys(METRICS)
        local = []
        try:
            data = json.loads(path.read_text())
            if local_compute is not None and isinstance(data, dict) and (
                    data.get('local_compute') or (data.get('inference') and
                    data.get('attribution', {}).get('purpose') == 'compression_service')):
                local_compute.append({'source': str(path), 'compute': data.get('local_compute'),
                    'duration_sec': data.get('duration_sec'), 'complete': data.get('complete'),
                    'model': data.get('model'), 'effects': data.get('effects'), 'pricing': 'unpriced'})
            if not isinstance(data, dict) or data.get('version') != 1 or type(data.get('inference')) is not bool:
                raise ValueError('invalid service record')
            attr = validate_attribution(data['attribution'])
            if data.get('raw_usage') is not None:
                if not data['inference']:
                    raise ValueError('orchestration record cannot own inference usage')
                metrics = normalize_usage(data['raw_usage'], data['protocol'], data.get('provider'))
            elif data['inference']:
                local.append('service inference usage unknown')
            if not data.get('complete'):
                local.append('service operation incomplete')
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            if local_compute is not None and isinstance(error, (OSError, json.JSONDecodeError, UnicodeError)):
                local_compute.append({'source': str(path), 'compute': None, 'complete': False})
            if not isinstance(data, dict):
                data = {}
            local.append(f'unreadable service record ({type(error).__name__})')
        purpose = attr.get('purpose', 'unknown')
        model, parent = data.get('model'), attr.get('parent_call_id')
        context = {key: data.get(key) for key in ('provider', 'protocol')}
        if data.get('inference', True):
            observations.append(UsageObservation(case_id, attempt_id, call_id, path.stem,
                metrics, str(path), data.get('raw_usage') or {}, {}, purpose, model, parent, path.stem, context))
        operations.append(UsageOperation(case_id, attempt_id, call_id, path.stem,
            purpose, model, parent, 'service', duration(data), bool(data.get('complete')), local,
            data.get('inference', True), context))
        issues.extend(f'{path}: {reason}' for reason in local)
    # Service operations represent actual attempted work even when backend usage is unknown.
    return CaseUsage(case_id, bool(observations), observations, not issues, issues, operations)
