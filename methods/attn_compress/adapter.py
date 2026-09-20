"""Dynamic full compression using the unchanged author's manager and algorithm."""
from collections import defaultdict
import copy
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import time

from src.method_sessions import SessionMethod, artifacts, chat_steps, save_state, source_metrics, author_metrics
from src.method_transport import post_json, validate_endpoint
from src.service_usage import record_service
from src.session import SessionDecision
from src.source_declarations import declarations

SOURCE = Path(__file__).parent / 'upstream/code/trae_agent/agents'


class AttnCompress(SessionMethod):
    version = 'attn-compress-dynamic-source-compatible-v1'

    def validate_options(self, options):
        if set(options) - {'service_base_url', 'timeout'}:
            raise ValueError('AttnCompress supports only explicit service_base_url and timeout; defaults are source dynamic full')
        validate_endpoint(options.get('service_base_url', ''))
        if options.get('timeout', 600) <= 0:
            raise ValueError('positive service timeout required')
        if importlib.util.find_spec('tiktoken') is None:
            raise ValueError('AttnCompress requires prepared tiktoken for original effect metrics')

    def callback(self, task, workspace, options, *, tokenizer=None, transport=post_json):
        if tokenizer is None:
            import tiktoken
            tokenizer = tiktoken.encoding_for_model('gpt-4o').encode
        ns = declarations(SOURCE / 'expert.py', ['MessageManager', 'SYS_PROMPT', 'Expert'], {'copy': copy, 'json': json, 'os': os})
        manager = ns['MessageManager'].__new__(ns['MessageManager'])
        manager.steps, manager.metrics = [], defaultdict(int)
        manager.user_message = {'role': 'user', 'content': task.problem_statement}
        state = {'version': self.version, 'metrics': manager.metrics, 'failures': [],
                 'inference_usage': 'unknown unless service exposes actual usage'}
        original, current, last_ids = {}, None, None
        cache = ns['Expert'].__new__(ns['Expert'])
        cache.prompt_pool = []
        pending_prompt = None

        def service(payload):
            try:
                with record_service(artifacts(current), parent_call_id=current.details.get('usage_identity', {}).get('call_id', 'main'), identity=current.details.get('usage_identity', {})) as record:
                    result = transport(options['service_base_url'], '/compress', payload,
                        artifacts=artifacts(current), channel='attn-compress', timeout=options.get('timeout', 600))
                    if result.get('usage') is not None:
                        # Require an explicit usage protocol; never interpret text stats as inference.
                        record.update(raw_usage=result['usage'], protocol=result['usage_protocol'], provider=result.get('provider'))
                    record['effects'] = result.get('stats', {})
                    messages = result.get('compressed_messages') or []
                    for index, (before, after) in enumerate(zip(payload['messages'], messages)):
                        step = payload['step_indices'][index]
                        editable = before.get('role') == 'tool' and 0 <= step < len(manager.steps) - 2
                        if not isinstance(after.get('content'), str):
                            raise ValueError('service returned nontext content')
                        if not editable and after.get('content') != before.get('content'):
                            raise ValueError('service changed protected content')
                        for key in ('role', 'tool_call_id', 'tool_calls'):
                            if key in after and after[key] != before.get(key):
                                raise ValueError('service changed tool protocol')
                    return result
            except Exception as error:
                state['failures'].append(type(error).__name__)
                raise  # The unchanged original function handles its documented fallback.

        algorithm = declarations(SOURCE / 'traj_analyzer.py',
            ['perform_analysis_step_attention_compress', 'maybe_perform_analysis_step'], {
                'MODE': 'attn', 'ATTN_ROLLING_M': 0, 'ATTN_RATIO': .20, 'ATTN_TAIL': 2,
                'ATTN_MASK_HISTORY': False, 'ATTN_REFRESH_FIXED': False, 'ATTN_CHUNKING_METHOD': 'token',
                'ATTN_COMPRESS_TOOL_RESPONSE': True, 'ATTN_COMPRESS_TOOL_CALL': False,
                'ATTN_COMPRESS_ASSISTANT_CONTENT': False, 'ATTN_RANDOMIZE': False,
                'BLOCK_SCORE_METHOD': 'mean', 'BLOCK_SPLIT_METHOD': 'double_newline', 'SELECTION_METHOD': 'greedy',
                'PPL_SPIKE_THRESHOLD_K': 1.2, 'PPL_SPIKE_METHOD': 'iqr', 'ATTN_LAYERS': None,
                '_call_attn_service': service, 'count_token': lambda text: len(tokenizer(text)), 'time': time})

        def callback(event):
            nonlocal current, last_ids, pending_prompt
            current = event
            history = list(event.history)
            if event.kind == 'initialize':
                users = [m.text for m in history if m.role == 'user']
                if users:
                    manager.user_message = {'role': 'user', 'content': users[0]}
                systems = [m.text for m in history if m.role in ('system', 'developer')]
                if systems:
                    ns['SYS_PROMPT'] = '\n'.join(systems)
            if event.kind == 'before_model':
                steps, _ = chat_steps(history)
                prefix = [{'role': m.role, 'content': m.text} for m in history if m.role in ('system', 'developer')]
                prefix.append(manager.user_message)
                pending_prompt = cache._messages_to_str(prefix + [m for step in steps for m in step])
            if event.kind == 'after_model' and pending_prompt is not None and not event.details.get('error_type'):
                manager.metrics['cached_tokens'] += int(cache._get_local_cache_hit(pending_prompt) / 4)
                pending_prompt = None
            if event.kind == 'after_tool':
                steps, groups = chat_steps(history)
                identity = tuple(identity for ids in groups for identity in ids)
                if steps and identity != last_ids:
                    for step, ids in zip(steps, groups):
                        for message, message_id in zip(step, ids):
                            original.setdefault(message_id, copy.deepcopy(message))
                            message['agent_original_dict'] = original[message_id]
                    manager.steps = steps
                    algorithm['maybe_perform_analysis_step'](manager)
                    changes = {identity: message['content'] for step, ids in zip(manager.steps, groups)
                               for message, identity in zip(step, ids) if message['role'] == 'tool'}
                    history = [replace(message, text=changes[message.id]) if message.editable and message.id in changes else message
                               for message in history]
                    last_ids = identity
            save_state(event, 'attn_compress', state)
            return SessionDecision(history=history, state=state)
        return callback

    def original_accounting(self, cases):
        report = source_metrics(cases, rule='attn-compress-exporter-compatible-v1')
        selected = [c for c in cases if c.trace is not None]
        # The author's attn branch never increments analysis model token counters.
        # These native zeros reproduce the exporter, not actual service inference.
        report['overhead'] = author_metrics(len(selected), {'input': 0, 'output': 0, 'total': 0,
            'service_seconds': sum(c.method_data.get('metrics', {}).get('analysis_time', 0) for c in selected)},
            'author-analysis-counters-attn-zero-not-inference')
        cached = sum(c.method_data.get('metrics', {}).get('cached_tokens', 0) for c in selected)
        report['metrics']['cache_read'] = {'sum': cached, 'mean': cached / len(selected) if selected else None,
            'complete': True, 'reasons': []}
        report['note'] += ' Original cache_read is the author common-prefix-character / 4 estimate over observed model boundaries; corrected uses service usage only.'
        return report
