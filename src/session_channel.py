"""Synchronous file channel over the existing artifact mount; no network service."""
from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


class SessionClient:
    """Copied with the hand-written worker; contains no host/module dependencies."""
    def __init__(self, directory, timeout=60):
        self.directory = Path(directory)
        self.timeout = timeout
        self.sequence = 0

    def request(self, kind, **payload):
        self.sequence += 1
        name = f'{self.sequence:06d}'
        request = self.directory / f'{name}.request.json'
        response = self.directory / f'{name}.response.json'
        if request.exists() or response.exists():
            raise RuntimeError('session channel cannot reuse an existing exchange')
        _write(request, {'kind': kind, **payload})
        deadline = time.monotonic() + self.timeout
        while not response.exists():
            if (self.directory / 'closed.json').exists() or time.monotonic() >= deadline:
                raise RuntimeError('session callback unavailable or timed out')
            time.sleep(.01)
        result = json.loads(response.read_text())
        if result.get('error'):
            raise RuntimeError('session callback failed: ' + result['error'])
        return result


@contextmanager
def session_server(artifacts, callback, *, identity=None):
    from .session import Message, Session

    root = artifacts.host / 'session-channel'
    root.mkdir(exist_ok=False)
    session = Session(callback, artifacts.host / 'session')
    stop = threading.Event()
    failures = []
    native_mapping = None
    def serve():
        nonlocal native_mapping
        sequence = 1
        while not stop.is_set():
            request = root / f'{sequence:06d}.request.json'
            if not request.exists():
                stop.wait(.01)
                continue
            try:
                payload = json.loads(request.read_text())
                if payload['kind'] == 'model_input':
                    session.record_model_input(payload['request'])
                    result = {}
                else:
                    native = 'native_history' in payload
                    if native:
                        if native_mapping is None:
                            from agents.mini_swe_agent.session_mapping import NativeHistory
                            native_mapping = NativeHistory(mode='responses_items')
                        payload['history'] = native_mapping.encode_wire(payload['native_history'])
                    history = [Message(**{**m, 'tool_calls': tuple(m.get('tool_calls', []))})
                               for m in payload['history']]
                    details = {**payload.get('details', {}),
                               'usage_identity': dict(identity or {}),
                               'artifact_host': str(artifacts.host),
                               'artifact_execution': str(artifacts.execution)}
                    result = asdict(session.emit(payload['kind'], history, **details))
                    if native:
                        result['native_history'] = native_mapping.decode(result.pop('history'))
                # Reject non-JSON callback state promptly rather than strand the worker.
                json.dumps(result)
            except BaseException as error:
                failures.append(type(error).__name__)
                result = {'error': type(error).__name__}
            _write(root / f'{sequence:06d}.response.json', result)
            sequence += 1
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield session
    finally:
        stop.set()
        _write(root / 'closed.json', {'callback_errors': failures, 'finished': session.finished})
        thread.join(timeout=1)
        # Never allow a missing/failed callback to silently turn into a normal run.
        if thread.is_alive() or failures or not session.finished:
            _write(artifacts.host / 'session-outcome.json', {
                'complete': False, 'errors': failures or ['session did not finish']})
        else:
            _write(artifacts.host / 'session-outcome.json', {
                'complete': True, 'termination': session.termination})
