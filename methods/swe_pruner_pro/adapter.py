"""SWE-Pruner Pro client plus native-history mapping and recorded backend evidence."""
from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from src.method_sessions import SessionMethod, artifacts, save_state, tool_call, source_metrics
from src.method_transport import post_json, validate_endpoint
from src.records import write_json
from src.service_usage import record_service
from src.source_declarations import declarations
from src.session import SessionDecision

ROOT = Path(__file__).parent / 'upstream/src/swe_pruner_pro'
MODEL = 'Qwen3-Coder-Next'
SERVICE_VERSION = 'tokenana-swe-pruner-recorded-v1'


class ServiceCapabilityError(RuntimeError):
    pass


class SwePrunerPro(SessionMethod):
    version = 'swe-pruner-pro-source-compatible-v1'

    def validate_options(self, options):
        allowed = {'service_base_url', 'backend_base_url', 'backbone', 'head', 'head_model', 'timeout'}
        if set(options) - allowed:
            raise ValueError('unsupported SWE-Pruner Pro options')
        validate_endpoint(options.get('service_base_url', ''))
        validate_endpoint(options.get('backend_base_url', ''))
        if options.get('backbone') != MODEL or options.get('head_model') != MODEL:
            raise ValueError('SWE-Pruner Pro requires Qwen3-Coder-Next and its matching existing head')
        head = Path(options.get('head', ''))
        if not head.is_dir() or not (head / 'best_model.pt').is_file() or not (head / 'model_config.json').is_file():
            raise ValueError('existing SWE-Pruner head directory with best_model.pt and model_config.json required')
        config = json.loads((head / 'model_config.json').read_text())
        # An explicit matching declaration is required even for older head configs
        # lacking a backbone field; conflicting native metadata always blocks.
        declared = config.get('backbone', config.get('model_name_or_path'))
        if declared and str(declared).rstrip('/').split('/')[-1] != MODEL:
            raise ValueError('head metadata does not match Qwen3-Coder-Next')

    def validate_agent_options(self, options, agent_options):
        model = str(agent_options.get('model', '')).split('/')[-1]
        if model != MODEL or agent_options.get('model_protocol', agent_options.get('wire_api')) != 'chat_completions':
            raise ValueError('SWE-Pruner Pro main model must be Qwen3-Coder-Next with native Chat Completions')
        main = str(agent_options.get('model_base_url', agent_options.get('base_url', ''))).rstrip('/').removesuffix('/v1')
        backend = options['backend_base_url'].rstrip('/').removesuffix('/v1')
        if main != backend:
            raise ValueError('main model and hidden-state backend must use the same explicitly configured SGLang service')

    def callback(self, task, workspace, options, *, transport=post_json):
        state = {'version': self.version, 'results': [], 'threshold': .5, 'min_chars': 2000}
        seen, current = set(), None
        client_path = ROOT / 'eval/swebench/mini-swe-agent/src/minisweagent/utils/pruner.py'

        class Response:
            def __init__(self, **data):
                defaults = dict(kept_lines=[], original_lines=0, kept_line_count=0,
                                original_chars=0, pruned_chars=0, latency_ms=0, error_msg=None, backend='ours')
                self.__dict__.update({**defaults, **data})

        class HTTP:
            def post(self, url, json, timeout):
                output = artifacts(current)
                with record_service(output, model=MODEL, parent_call_id=current.details.get('usage_identity', {}).get('call_id', 'main'), inference=False, identity=current.details.get('usage_identity', {})) as operation:
                    data = transport(options['service_base_url'], '/prune', json, artifacts=output,
                                     channel='swe-pruner', timeout=timeout)
                    descriptor = data.get('tokenana', {})
                    if (descriptor.get('version') != SERVICE_VERSION or descriptor.get('backbone') != MODEL
                            or descriptor.get('backend_base_url', '').rstrip('/') != options['backend_base_url'].rstrip('/')
                            or descriptor.get('head_model') != MODEL):
                        raise ServiceCapabilityError('SWE-Pruner requires matching recorded service wrapper')
                    records = data.get('backend_requests')
                    if not isinstance(records, list):
                        raise ServiceCapabilityError('SWE-Pruner response lacks backend request evidence')
                    for record in records:
                        write_json(output.host / 'service-records' / (uuid4().hex + '.json'), {
                            'version': 1, 'attribution': {**current.details.get('usage_identity', {}), 'purpose': 'compression_service', 'parent_call_id': current.details.get('usage_identity', {}).get('call_id', 'main')},
                            'model': MODEL, 'inference': True, 'complete': bool(record.get('complete')),
                            'duration_sec': record.get('duration_sec'), 'protocol': 'chat_completions',
                            'provider': 'openai', 'raw_usage': record.get('usage'),
                            'effects': {'backend_meta': record.get('meta_info'), 'error_type': record.get('error_type')}})
                    operation['effects'] = {key: data.get(key) for key in ('original_tokens', 'pruned_tokens', 'original_chars', 'pruned_chars')}
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

        # Reuse the original client's length check, payload and response handling.
        namespace = declarations(client_path, ['PrunerClient'], {'PruneResponse': Response})
        client = namespace['PrunerClient'].__new__(namespace['PrunerClient'])
        client.config = SimpleNamespace(url=options['service_base_url'] + '/prune', threshold=.5,
                                       min_chars=2000, timeout=options.get('timeout', 120), backend='')
        client.session = HTTP()
        source_agent = declarations(client_path.parent.parent / 'agents/default.py', ['_CFQ_RE'], {'re': re})
        sanitize = declarations(ROOT / 'eval/_runtime/pruner_client.py', ['sanitize_history_for_prune'])['sanitize_history_for_prune']
        post = declarations(client_path.with_name('prune_hooks.py'),
            ['PrunePostContext', '_FULLY_PRUNED_HINT', 'hook_fully_pruned_hint', 'run_post_hooks'], {'dataclass': dataclass})
        post['DEFAULT_POST_HOOKS'] = [post['hook_fully_pruned_hint']]

        def callback(event):
            nonlocal current
            current = event
            history = list(event.history)
            if event.kind == 'after_tool':
                native_history, calls = [], {}
                for index, message in enumerate(history):
                    converted = {'role': message.role, 'content': message.text}
                    if message.tool_calls:
                        converted['tool_calls'] = tool_call(message)
                        calls.update({call['id']: call for call in converted['tool_calls']})
                    if message.tool_result:
                        converted['tool_call_id'] = message.tool_result
                    if message.tool_result and message.editable and message.id not in seen:
                        seen.add(message.id)
                        call = calls.get(message.tool_result)
                        if not call:
                            raise ValueError('missing native tool arguments for pruning')
                        arguments = call['function'].get('arguments', '{}')
                        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                        command = arguments.get('command', arguments.get('cmd', ''))
                        if 'KEEP_ALL_IN_THIS_COMMAND' in str(command) or not message.text:
                            state['results'].append({'id': message.id, 'skipped': 'keep_all_or_empty'})
                        else:
                            try:
                                assistant = next((m.text for m in reversed(history[:index]) if m.role == 'assistant'), '')
                                match = source_agent['_CFQ_RE'].search(assistant)
                                focus = match.group(1).strip() if match else ''
                                if focus:
                                    arguments = {**arguments, 'context_focus_question': focus}
                                result = client.prune(sanitize(native_history),
                                    {'name': call['function']['name'], 'arguments': arguments}, message.text, context_focus_question=focus)
                                if result.pruned_chars < result.original_chars:
                                    text = re.sub(r'\(filtered (\d+) lines:\s*\d+-\d+\)', r'(filtered \1 lines)', result.pruned_code)
                                    text = post['run_post_hooks'](post['PrunePostContext'](None, text,
                                        result.original_chars, result.pruned_chars, result.original_lines, result.kept_line_count))
                                    history[index] = replace(message, text=text)
                                state['results'].append({'id': message.id, **result.__dict__})
                            except ServiceCapabilityError as error:
                                with record_service(artifacts(event), model=MODEL, identity=event.details.get('usage_identity', {})) as missing:
                                    missing['effects'] = {'error_type': type(error).__name__, 'backend_request_status': 'unknown'}
                                save_state(event, 'swe_pruner_pro', state)
                                raise
                            except Exception as error:
                                state['results'].append({'id': message.id, 'error': type(error).__name__})
                                with record_service(artifacts(event), model=MODEL, identity=event.details.get('usage_identity', {})) as missing:
                                    missing['effects'] = {'error_type': type(error).__name__, 'backend_request_status': 'unknown'}
                                # Preserve the author's raw-output failure fallback.
                    converted['content'] = history[index].text
                    native_history.append(converted)
            save_state(event, 'swe_pruner_pro', state)
            return SessionDecision(history=history, state=state)
        return callback

    def original_accounting(self, cases):
        return source_metrics(cases, rule='swe-pruner-native-usage-compatible-v1')
