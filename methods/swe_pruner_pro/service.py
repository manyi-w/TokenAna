"""Optional prepared-GPU service wrapper. Never started by TokenAna run/dry-run.

Loads unchanged pruning declarations into a per-request namespace with a recorded
SGLang client dependency. Original source and imported modules are not patched.
"""
import ast
from collections import defaultdict
import json
import logging
from pathlib import Path
import time

from src.source_declarations import declarations

VERSION = 'tokenana-swe-pruner-recorded-v1'
MODEL = 'Qwen3-Coder-Next'
SOURCE = Path(__file__).parent / 'upstream/src/swe_pruner_pro/serving/pruner_server.py'


def create_app(*, checkpoint, tokenizer_path, backend_base_url, hidden_size, device='cuda:0', max_length=16384):
    # Imports intentionally stay behind this explicit, separately provisioned entry point.
    import numpy as np
    import pybase64
    import requests
    import torch
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    from transformers import AutoTokenizer
    from importlib import import_module
    import sys

    head_path = Path(checkpoint)
    if not (head_path / 'best_model.pt').is_file() or not (head_path / 'model_config.json').is_file():
        raise ValueError('existing matching head required')
    config = json.loads((head_path / 'model_config.json').read_text())
    model = config.get('backbone', config.get('model_name_or_path', MODEL))
    if str(model).rstrip('/').split('/')[-1] != MODEL:
        raise ValueError('head metadata mismatches Qwen3-Coder-Next')
    tokenizer_config = json.loads((Path(tokenizer_path) / 'config.json').read_text())
    if tokenizer_config.get('model_type') != 'qwen3_next' or tokenizer_config.get('hidden_size') != hidden_size:
        raise ValueError('prepared Qwen3-Coder-Next tokenizer/model config and hidden size required')
    upstream_src = str(SOURCE.parents[2])
    sys.path.insert(0, upstream_src)
    try:
        PruningHead = import_module('swe_pruner_pro.model').PruningHead
    finally:
        sys.path.remove(upstream_src)
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    head = PruningHead(checkpoint, hidden_size=hidden_size, device=device)
    names = [node.name for node in ast.parse(SOURCE.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name not in ('create_app', 'main')]
    names.append('_TOOLS')
    app = FastAPI(title='TokenAna recorded SWE-Pruner Pro')
    descriptor = {'version': VERSION, 'backbone': MODEL, 'head_model': MODEL,
                  'backend_base_url': backend_base_url}

    @app.get('/health')
    def health():
        return {**descriptor, 'head_loaded': True}

    @app.post('/prune')
    def prune(payload: dict):
        records = []

        class RecordedClient:
            def post(self, url, **kwargs):
                if url != backend_base_url.rstrip('/') + '/generate':
                    raise ValueError('unexpected hidden-state destination')
                start = time.monotonic()
                record = {'complete': False, 'started_at': time.time(), 'submitted_input_tokens': len(kwargs.get('json', {}).get('input_ids', [])),
                          'input_tokens': None, 'forward_count': None, 'tokenizer': tokenizer_path, 'model': MODEL,
                          'note': 'Backend request input; internal forward count requires backend telemetry'}
                records.append(record)
                try:
                    response = requests.post(url, **kwargs)
                    record['status'] = response.status_code
                    response.raise_for_status()
                    data = response.json()
                    meta = data.get('meta_info', {})
                    telemetry = meta.get('tokenana_local_compute')
                    if isinstance(telemetry, dict):
                        from src.local_compute import validate_backend_telemetry
                        record['local_compute'] = validate_backend_telemetry(telemetry, model=MODEL)
                        record.update(input_tokens=record['local_compute']['input_tokens'],
                                      forward_count=record['local_compute']['forward_count'])
                    # Keep raw counters, exclude large hidden-state tensors from accounting.
                    record['meta_info'] = {key: value for key, value in meta.items() if key != 'hidden_states'}
                    prompt, completion = meta.get('prompt_tokens'), meta.get('completion_tokens')
                    if type(prompt) is int and type(completion) is int:
                        usage = {'prompt_tokens': prompt, 'completion_tokens': completion,
                                 'total_tokens': prompt + completion}
                        if type(meta.get('cached_tokens')) is int:
                            usage['prompt_tokens_details'] = {'cached_tokens': meta['cached_tokens']}
                        record['usage'] = usage
                    record['complete'] = True
                    return response
                except Exception as error:
                    record['error_type'] = type(error).__name__
                    raise
                finally:
                    record['duration_sec'] = time.monotonic() - start
                    record['finished_at'] = time.time()

        namespace = declarations(SOURCE, names, {'BaseModel': BaseModel, 'np': np, 'pybase64': pybase64,
            'torch': torch, '_json': json, 'time': time, 'defaultdict': defaultdict,
            'logger': logging.getLogger('tokenana.pruner'), '_tokenizer': tokenizer, '_pruning_head': head,
            '_sglang_client': RecordedClient(), '_sglang_url': backend_base_url.rstrip('/'), '_max_length': max_length,
            '_tool_response_start_id': tokenizer.convert_tokens_to_ids('<tool_response>'),
            '_tool_response_end_id': tokenizer.convert_tokens_to_ids('</tool_response>'),
            '_newline_token_id': tokenizer.encode('\n', add_special_tokens=False)[0]})
        # Pydantic resolves postponed annotations against the isolated namespace.
        from typing import List, Optional
        for name in ('PruneRequest', 'PruneResponse'):
            namespace[name].model_rebuild(_types_namespace={'List': List, 'Optional': Optional})
        from src.local_compute import observe_forwards, summary
        with observe_forwards([head.compression_head], model='SWE-Pruner-Pro-head',
                              tokenizer=tokenizer_path, input_kind='hidden_states') as forwards:
            try:
                result = namespace['_prune'](namespace['PruneRequest'](**payload)).model_dump()
            except Exception as error:
                return JSONResponse(status_code=500, content={'error_type': type(error).__name__,
                    'tokenana': descriptor, 'backend_requests': records,
                    'head_compute': summary(forwards, complete=False)})
        return {**result, 'tokenana': descriptor, 'backend_requests': records,
                'head_compute': summary(forwards, complete=True)}
    return app
