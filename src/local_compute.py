"""Observe real model forwards through public PyTorch hooks; never alter tensors."""
from contextlib import contextmanager
from contextvars import ContextVar
import time
from uuid import uuid4

_active = ContextVar('tokenana_local_forwards', default=None)


@contextmanager
def observe_forwards(models, *, model, tokenizer, input_kind='input_ids'):
    records = []
    token = _active.set(records)
    handles = []
    pending = {}

    def before(module, args, kwargs):
        if _active.get() is not records:
            return
        ids = kwargs.get(input_kind, args[0] if args else None)
        count = None
        if ids is not None:
            if input_kind == 'input_ids':
                count = int(ids.numel())
            else:
                from math import prod
                count = prod(ids.shape[:-1])
        item = {'id': uuid4().hex, 'model': model, 'tokenizer': tokenizer,
                'input_tokens': count, 'input_kind': input_kind,
                'shape': list(ids.shape) if ids is not None else None,
                'started_at': time.time(), 'status': 'started'}
        records.append(item)
        pending[id(module)] = (item, time.monotonic())

    def after(module, args, kwargs, output):
        value = pending.pop(id(module), None)
        if value is not None:
            item, started = value
            item.update(status='completed' if output is not None else 'failed',
                        host_duration_sec=time.monotonic() - started, ended_at=time.time())

    try:
        for instance in models:
            handles.append(instance.register_forward_pre_hook(before, with_kwargs=True))
            handles.append(instance.register_forward_hook(after, with_kwargs=True, always_call=True))
        yield records
    finally:
        for handle in handles:
            handle.remove()
        _active.reset(token)


def summary(records, *, complete):
    known = [r['input_tokens'] for r in records if r.get('input_tokens') is not None]
    return {'version': 'local-forward-v1', 'forwards': records, 'forward_count': len(records),
            'input_tokens': sum(known) if len(known) == len(records) else None,
            'complete': complete and all(r['status'] == 'completed' for r in records),
            'generated_output_tokens': None, 'pricing': 'unpriced',
            'note': 'Actual input_ids per forward; cached context is not counted again. '
                    'Host hook duration is not GPU kernel time; retained text is not generated output.'}


def validate_backend_telemetry(value, *, model):
    """Consume backend-owned per-forward evidence, not HTTP-wrapper estimates."""
    if value.get('version') != 'local-forward-v1' or value.get('model') != model or not value.get('request_id'):
        raise ValueError('Backend forward telemetry must identify its request, model and schema')
    forwards = value.get('forwards')
    if not isinstance(forwards, list) or not forwards:
        raise ValueError('Backend telemetry requires actual forward records')
    ids = set()
    for record in forwards:
        if not record.get('id') or record['id'] in ids or type(record.get('input_tokens')) is not int or record['input_tokens'] < 0:
            raise ValueError('Invalid or duplicate backend forward evidence')
        ids.add(record['id'])
    return {**summary(forwards, complete=bool(value.get('complete'))),
            'model': model, 'tokenizer': value.get('tokenizer'), 'request_id': value['request_id']}
