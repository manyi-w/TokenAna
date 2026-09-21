"""Append-only wall-clock spans. Nested spans are not additive."""
from contextlib import contextmanager
import json
from pathlib import Path
import time
from uuid import uuid4


@contextmanager
def span(directory, phase, **identity):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'timing.jsonl'
    record = dict(version=1, id=uuid4().hex, phase=phase, **identity)
    started = time.monotonic_ns()
    def append(values):
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({**record, **values}, ensure_ascii=False) + '\n')
            stream.flush()
    append(dict(event='start', utc_seconds=time.time(), monotonic_ns=started))
    status = 'completed'
    try:
        yield
    except BaseException:
        status = 'failed'
        raise
    finally:
        end = time.monotonic_ns()
        append(dict(event='end', utc_seconds=time.time(), monotonic_ns=end,
                    seconds=(end-started)/1e9, status=status))


def totals(root):
    result = {}
    for path in Path(root).rglob('timing.jsonl'):
        for line in path.read_text().splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue  # The start event remains evidence of a hard interruption.
            if item.get('event') == 'end':
                key = item['phase']
                result[key] = result.get(key, 0) + item['seconds']
    return result


def intervals(root):
    """Union wall intervals per phase; open/truncated spans remain incomplete."""
    phases, pending, reasons = {}, {}, []
    for path in Path(root).rglob('timing.jsonl'):
        for number, line in enumerate(path.read_text().splitlines()):
            try:
                item = json.loads(line)
                phase = item['phase']
                group = phases.setdefault(phase, {'intervals': [], 'incomplete': []})
                key = (str(path), item.get('id'))
                if item['event'] == 'start':
                    pending[key] = item
                elif item['event'] == 'end':
                    start = pending.pop(key, None)
                    end = item['utc_seconds']
                    begin = start['utc_seconds'] if start else end - item['seconds']
                    if begin > end:
                        group['incomplete'].append('wall clock moved backwards')
                    else:
                        group['intervals'].append((begin, end))
                    if item.get('status') == 'failed':
                        group.setdefault('failed_spans', 0)
                        group['failed_spans'] += 1
            except (ValueError, KeyError, TypeError):
                reasons.append(f'{path}:{number + 1}: unreadable timing event')
    for item in pending.values():
        phases[item['phase']]['incomplete'].append('span has no end event')
    # Requests and services have explicit observation intervals independent of
    # execution spans. They may nest inside callbacks and must not be added to wall time.
    for pattern, phase in (('metadata.json', 'model_request'), ('service-records/*.json', 'method_service')):
        for path in Path(root).rglob(pattern):
            if phase == 'model_request' and not path.with_name('request.body').exists():
                continue
            try:
                item = json.loads(path.read_text())
                if phase == 'model_request' and (item.get('rejected') or ('forwarded_at' in item and item['forwarded_at'] is None)):
                    continue
                begin, end = item.get('forwarded_at', item.get('started_at')), item.get('finished_at')
                group = phases.setdefault(phase, {'intervals': [], 'incomplete': []})
                if type(begin) in (int, float) and type(end) in (int, float) and begin <= end:
                    group['intervals'].append((begin, end))
                else:
                    group['incomplete'].append(f'{path}: request interval unavailable')
            except (OSError, ValueError, TypeError):
                reasons.append(f'{path}: unreadable observed interval')
    for group in phases.values():
        merged = []
        for begin, end in sorted(group['intervals']):
            if merged and begin <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([begin, end])
        subtotal = sum(end - begin for begin, end in merged)
        group.update(intervals=merged, known_seconds=subtotal, complete=not group['incomplete'] and not reasons,
                     seconds=subtotal if not group['incomplete'] and not reasons else None)
    for phase in ('tool_execution',):
        phases.setdefault(phase, {'seconds': None, 'known_seconds': None, 'complete': False,
            'intervals': [], 'incomplete': ['Native tool start/end intervals are not exposed by this recording path']})
    return {'phases': phases, 'issues': reasons,
            'note': 'Intervals union within each phase. Nested phases are not additive; '
                    'generation wall time already includes blocking method and recording overhead.'}
