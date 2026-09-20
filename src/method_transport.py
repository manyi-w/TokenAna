"""Explicit host auxiliary channel; persist raw bytes before interpreting them."""
from http.client import HTTPConnection, HTTPSConnection
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .records import write_json


class ServiceHTTPError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'auxiliary service HTTP {status}')


def validate_endpoint(value):
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('explicit HTTP(S) service URL without credentials/query required')
    return value.rstrip('/')


def post_json(base_url, route, payload, *, artifacts, channel, protocol=None,
              model=None, api_key_env=None, timeout=120, identity=None):
    endpoint = urlsplit(validate_endpoint(base_url))
    if timeout <= 0:
        raise ValueError('positive service timeout required')
    # Model channels use the same raw-record schema as the main usage recorder.
    root = Path(artifacts.host) / ('auxiliary-records' if protocol else 'service-http') / channel / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    body = json.dumps(payload, ensure_ascii=False).encode()
    (root / 'request.body').write_bytes(body)
    metadata = {'protocol': protocol, 'provider': 'openai' if protocol else None,
                'model': model, 'attribution': {'purpose': 'method_auxiliary', **(identity or {})},
                'response_complete': False, 'started_at': time.time()}
    write_json(root / 'metadata.json', metadata)
    start = time.monotonic()
    connection = (HTTPSConnection if endpoint.scheme == 'https' else HTTPConnection)(
        endpoint.hostname, endpoint.port, timeout=timeout)
    try:
        headers = {'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
        if api_key_env:
            key = os.environ.get(api_key_env)
            if not key:
                raise ValueError(f'missing auxiliary credential environment variable: {api_key_env}')
            headers['Authorization'] = 'Bearer ' + key
        connection.request('POST', endpoint.path.rstrip('/') + route, body, headers)
        response = connection.getresponse()
        metadata.update(status=response.status, response_headers=[(k, v) for k, v in response.getheaders()
            if k.lower() not in ('set-cookie', 'authorization', 'proxy-authorization')])
        with (root / 'response.body').open('wb') as stream:
            while chunk := response.read(65536):
                stream.write(chunk)
                stream.flush()
        metadata['response_complete'] = True
        if not 200 <= response.status < 300:
            raise ServiceHTTPError(response.status)
        return json.loads((root / 'response.body').read_bytes())
    except BaseException as error:
        metadata['error_type'] = type(error).__name__
        raise
    finally:
        connection.close()
        metadata['duration_sec'] = time.monotonic() - start
        write_json(root / 'metadata.json', metadata)
