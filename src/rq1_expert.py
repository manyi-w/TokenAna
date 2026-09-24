"""One isolated process running the original AgentDiet/AttnCompress Expert."""
from collections import defaultdict
import collections
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from uuid import uuid4

from .records import write_json
from .source_declarations import declarations, configured_module


def run(settings):
    import openai
    import docker
    source, output = Path(settings['source']), Path(settings['output'])
    sys.path.insert(0, str(source))
    options = {**settings['spec']['recorded_method_options'], **settings['spec'].get('native_method_options', {})}
    if settings['method'] == 'attn_compress':
        options['attn_service_url'] = settings['compression_endpoint'].rstrip('/') + '/compress'
    os.environ['TRAJ_ANALYSIS'] = json.dumps(options)
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    call_state = {}
    def observe_response(invoke):
        def call(*args, **kwargs):
            response = invoke(*args, **kwargs)
            call_state['response_id'] = getattr(response, 'id', None) or getattr(response, 'response_id', None)
            return response
        return call
    def openai_client(**kwargs):
        kwargs['default_headers'] = {'X-TokenAna-Call': call_state['call_key']}
        real = openai.OpenAI(**kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=observe_response(real.chat.completions.create))))
    dependencies = dict(json=json, time=time, hashlib=hashlib,
        openai=SimpleNamespace(**{**vars(openai), 'OpenAI': openai_client}),
        random=random, os=os, itertools=itertools, threading=threading, collections=collections)
    names = ['HashKey', 'NullCache', 'send_request_openai', 'get_llm_response']
    if settings['method'] == 'attn_compress':
        from google import genai
        from google.genai import types
        def client(**kwargs):
            # Preserve the author's key routing; both routes pass through their
            # own recorder. No request body, generation option or retry changes.
            if not kwargs.get('http_options', {}).get('base_url'):
                kwargs['http_options'] = {'base_url': settings['official_endpoint']}
            kwargs['http_options']['headers'] = {'X-TokenAna-Call': call_state['call_key']}
            real = genai.Client(**kwargs)
            return SimpleNamespace(models=SimpleNamespace(generate_content=observe_response(real.models.generate_content)))
        dependencies.update(genai=SimpleNamespace(Client=client), types=types)
        names.append('send_request_gemini')
    ns = declarations(source / 'utils/llm_polytool.py', names, dependencies)
    ns['llm_cache_chat'] = ns['NullCache']()
    if settings['method'] == 'agent_diet':
        routes = {settings['spec']['model_id']: ns['send_request_openai'](
            settings['endpoint'], os.environ[settings['api_key_env']]),
            settings['spec']['helper_model_id']: ns['send_request_openai'](
            settings['helper_endpoint'], os.environ[settings['helper_api_key_env']])}
    else:
        official = [key for key in os.environ.get(settings['official_keys_env'], '').split(',') if key]
        routes = {settings['spec']['model_id']: ns['send_request_gemini'](
            settings['endpoint'], [os.environ[settings['api_key_env']]], official_api_keys=official)}
    ns['UPSTREAMS_PER_MODEL'] = routes
    def observed(model, messages, tools, kwargs):
        call_state.clear()
        call_state['call_key'] = uuid4().hex
        result = ns['get_llm_response'](model, messages, tools, kwargs)
        with (output / 'native-calls.jsonl').open('a') as stream:
            stream.write(json.dumps({**call_state, 'model': model, 'usage': result[2],
                'purpose': 'main' if model == settings['spec']['model_id'] else 'method_auxiliary'}) + '\n')
        return result
    module = ModuleType('utils.llm_polytool')
    module.get_llm_response = observed
    sys.modules[module.__name__] = module
    if settings['method'] == 'attn_compress':
        import httpx
        from .service_usage import record_service
        def post(url, **kwargs):
            identity = settings['identity']
            with record_service(SimpleNamespace(host=output), model='Qwen3-4B-Instruct-2507', identity=identity) as record:
                request_id = str(time.time_ns())
                write_json(output / ('compression-' + request_id + '-request.json'), kwargs.get('json'))
                response = httpx.post(url, **kwargs)
                try:
                    payload = response.json()
                except ValueError:
                    (output / ('compression-' + request_id + '-response.txt')).write_text(response.text)
                    # Let the unchanged caller apply its original HTTP/JSON error handling.
                    return response
                write_json(output / ('compression-' + request_id + '-response.json'), payload)
                record['effects'] = payload.get('stats', {})
                if 'local_compute' in payload:
                    record['local_compute'] = payload['local_compute']
                response.raise_for_status()
                return response
        configured_module(source / 'agents/traj_analyzer.py', 'agents.traj_analyzer',
            {'httpx': SimpleNamespace(post=post, HTTPStatusError=httpx.HTTPStatusError,
                                     RequestError=httpx.RequestError)})
    from agents import expert as original
    from utils.sandbox import Sandbox
    class ObservedManager(original.MessageManager):
        def perform_erase_step(self, *args, **kwargs):
            with (output / 'method-changes.jsonl').open('a') as stream:
                stream.write(json.dumps({'operation': 'erase', 'arguments': args, 'options': kwargs}) + '\n')
            return super().perform_erase_step(*args, **kwargs)

        def replace_steps_with_compressed_messages(self, messages):
            with (output / 'method-changes.jsonl').open('a') as stream:
                stream.write(json.dumps({'operation': 'compress', 'before': self.steps, 'after_messages': messages}) + '\n')
            return super().replace_steps_with_compressed_messages(messages)
    Expert = declarations(source / 'agents/expert.py', ['Expert'],
                          {**vars(original), 'MessageManager': ObservedManager})['Expert']
    task = {**settings['task'], 'turn_reminder': True, 'max_turn': 50}
    # Attach the original shell/tools to the container owned by the common
    # executor. Its unchanged Expert never creates or destroys a task container.
    sandbox = Sandbox('swebench', task['instance_id'], 'latest', task)
    sandbox.container = docker.from_env().containers.get(settings['container'])
    trajectory = {'result': {'gen': '', 'val': ''}, 'metrics': defaultdict(int, analysis_args=options),
                  'input': None, 'messages': []}
    expert = None
    try:
        expert = Expert(sandbox)
        _, patch, _ = expert.run(settings['root'], task, trajectory)
        (output / 'patch.diff').write_text(patch)
    except Exception as error:
        trajectory['result']['gen'] = f'err-{type(error)}'
        if expert and expert.mgr:
            trajectory['messages'] = [m for step in expert.mgr.steps for m in step]
        raise
    finally:
        # Original notebook fields exist even if a method never triggers.
        for key in ('tot_step', 'cost_tokens', 'prompt_tokens', 'completion_tokens', 'analysis_cost_tokens',
                    'analysis_prompt_tokens', 'analysis_completion_tokens', 'analysis_count', 'erase_tot_count',
                    'seen_tokens', 'erase_in_tokens', 'erase_out_tokens', 'cached_tokens'):
            trajectory['metrics'].setdefault(key, 0)
        write_json(output / 'native-trajectory.json', trajectory)
        if sandbox.shell and sandbox.shell.isalive():
            sandbox.shell.close(force=True)


if __name__ == '__main__':
    run(json.loads(Path(sys.argv[1]).read_text()))
