"""Serve the unchanged AttnCompress endpoint with independent forward observation."""
import importlib.util
from pathlib import Path
import sys
import threading
import time

from src.local_compute import observe_forwards, summary

SOURCE = Path(__file__).parent / 'upstream/code/attn_compress/attn_compress_service.py'


def create_app():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    spec = importlib.util.spec_from_file_location('tokenana_attn_original', SOURCE)
    original = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = original
    spec.loader.exec_module(original)
    resource = original.AttnResource.instance()
    models = [instance['model'] for instance in resource.instances]
    lock = threading.Lock()  # Original endpoint writes shared log files.
    app = FastAPI(title='TokenAna observed AttnCompress')

    @app.post('/compress')
    def compress(payload: dict):
        request = original.CompressRequest(**payload)
        started = time.monotonic()
        with lock, observe_forwards(models, model=resource.model_path,
                                    tokenizer=resource.tokenizer.name_or_path) as forwards:
            try:
                result = original.compress_messages_endpoint(request).model_dump()
            except Exception as error:
                return JSONResponse(status_code=getattr(error, 'status_code', 500), content={
                    'error_type': type(error).__name__, 'local_compute': summary(forwards, complete=False),
                    'service_seconds': time.monotonic() - started})
            return {**result, 'local_compute': summary(forwards, complete=True),
                    'service_seconds': time.monotonic() - started,
                    'tokenana': {'version': 'tokenana-attn-forward-v1'}}

    @app.get('/health')
    def health():
        return {'version': 'tokenana-attn-forward-v1', 'model': resource.model_path}
    return app
