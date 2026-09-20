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
