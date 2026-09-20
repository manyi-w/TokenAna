"""AgentDiet source declarations with explicit recorded helper dependency."""
from collections import defaultdict
from dataclasses import replace
import importlib.util
from pathlib import Path
import re

from src.method_sessions import SessionMethod, artifacts, chat_steps, save_state, source_metrics, author_metrics
from src.method_transport import post_json, validate_endpoint, ServiceHTTPError
from src.session import SessionDecision, StepSummary
from src.source_declarations import declarations

SOURCE = Path(__file__).parent / 'upstream/code/trae_agent/agents'


class AgentDiet(SessionMethod):
    version = 'agent-diet-source-compatible-v1'

    def validate_options(self, options):
        allowed = {'helper_base_url', 'helper_api_key_env', 'helper_model', 'timeout'}
        if set(options) - allowed:
            raise ValueError('unsupported AgentDiet options: ' + ', '.join(sorted(set(options) - allowed)))
        validate_endpoint(options.get('helper_base_url', ''))
        if options.get('helper_model', 'gpt-5-mini') != 'gpt-5-mini':
            raise ValueError('AgentDiet helper model must be gpt-5-mini')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', options.get('helper_api_key_env', '')):
            raise ValueError('AgentDiet requires explicit helper_api_key_env')
        if options.get('timeout', 120) <= 0:
            raise ValueError('positive helper timeout required')
        if importlib.util.find_spec('tiktoken') is None:
            raise ValueError('AgentDiet requires prepared tiktoken; no fallback token estimator')

    def callback(self, task, workspace, options, *, tokenizer=None, transport=post_json):
        if tokenizer is None:
            import tiktoken
            tokenizer = tiktoken.encoding_for_model('gpt-4o').encode
        manager_type = declarations(SOURCE / 'expert.py', ['MessageManager'])['MessageManager']
        manager = manager_type.__new__(manager_type)
        manager.steps, manager.metrics = [], defaultdict(int)
        manager.user_message = {'role': 'user', 'content': task.problem_statement}
        state = {'version': self.version, 'metrics': manager.metrics, 'steps': 0,
                 'helper_model': 'gpt-5-mini', 'source_error': None}
        step_ids, seen, current = [], set(), None

        def helper(model, messages, tools, kwargs):
            # Original gpt-5 wrapper removes temperature/stop and selects low effort.
            wire = []
            for message in messages:
                content = message['content']
                if isinstance(content, list):
                    content = ''.join(block['text'] for block in content)
                wire.append({**message, 'content': content})
            try:
                response = transport(options['helper_base_url'], '/chat/completions',
                    {'model': model, 'messages': wire, 'n': 1, 'reasoning_effort': 'low', 'max_completion_tokens': 8192},
                    artifacts=artifacts(current), channel='agent-diet', protocol='chat_completions', model=model,
                    api_key_env=options['helper_api_key_env'], timeout=options.get('timeout', 120),
                    identity={**current.details.get('usage_identity', {}), 'parent_call_id': current.details.get('usage_identity', {}).get('call_id', 'main')})
            except ServiceHTTPError:
                state['helper_error'] = 'ServiceHTTPError'
                raise
            choices, usage = response['choices'], response.get('usage', {})
            # Missing actual usage remains missing, matching the author's skip rule.
            normalized = {key: usage.get(key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
            if any(value is None for value in normalized.values()):
                normalized['completion_tokens'] = None
            return [choice['message'] for choice in choices], [choice['finish_reason'] for choice in choices], normalized

        ns = declarations(SOURCE / 'traj_analyzer.py', ['SYS_PROMPT', 'should_perform_analysis',
            'perform_analysis_step_ours', 'maybe_perform_analysis_step'], {
                'MODE': 'ours', 'MODEL': 'gpt-5-mini', 'BYPASS_FILTER': True,
                'N_CTX_BEFORE': 1, 'N_CTX_AFTER': 2, 'SHOW_CTX': True, 'USE_LZ4': False,
                'THRESHOLD_TOKENS': 500, 'count_token': lambda text: len(tokenizer(text)),
                'get_llm_response': helper})

        def callback(event):
            nonlocal current
            current = event
            summaries = []
            try:
                if event.kind == 'initialize':
                    users = [m.text for m in event.history if m.role == 'user']
                    if users:
                        manager.user_message = {'role': 'user', 'content': users[0]}
                if event.kind == 'after_model' and event.details.get('error_type') == 'APIStatusError':
                    state['source_error'] = 'APIStatusError'
                if event.kind == 'after_tool':
                    steps, groups = chat_steps(event.history)
                    for step, ids in zip(steps, groups):
                        if not ids or any(identity in seen for identity in ids):
                            continue
                        # Native summaries are already represented by the source manager.
                        if all(identity.startswith('summary-') for identity in ids):
                            seen.update(ids); continue
                        manager.steps.append(step)
                        step_ids.append(ids)
                        seen.update(ids)
                        before = manager.metrics['erase_tot_count']
                        ns['maybe_perform_analysis_step'](manager)
                        if manager.metrics['erase_tot_count'] > before:
                            index = manager.count_turn() - 3
                            summaries.append(StepSummary(tuple(step_ids[index]), manager.steps[index][0]['content']))
                    state['steps'] = manager.count_turn()
                state['status'] = 'finished' if event.kind == 'finish' else 'running'
            finally:
                save_state(event, 'agent_diet', state)
            return SessionDecision(state=dict(state), summaries=tuple(summaries))
        return callback

    def original_accounting(self, cases):
        # exporter.ipynb includes every present trajectory, including errors and empty patches.
        report = source_metrics(cases, rule='agent-diet-exporter-compatible-v1')
        selected = [case for case in cases if case.trace is not None]
        correction = 200_000 * sum(case.method_data.get('source_error') == 'APIStatusError' for case in selected)
        for key in ('input', 'total'):
            report['metrics'][key]['sum'] += correction
            report['metrics'][key]['mean'] = report['metrics'][key]['sum'] / len(selected) if selected else None
        incoming = sum(c.method_data.get('metrics', {}).get('analysis_prompt_tokens', 0) -
            492 * c.method_data.get('metrics', {}).get('analysis_count', 0) for c in selected)
        outgoing = sum(c.method_data.get('metrics', {}).get('analysis_completion_tokens', 0) for c in selected)
        report['overhead'] = author_metrics(len(selected), {'input': incoming, 'output': outgoing,
            'total': incoming + outgoing, 'calls': sum(c.method_data.get('metrics', {}).get('analysis_count', 0) for c in selected)},
            'author-cache-assumption-492-per-analysis')
        report['note'] += ' Author +200000 APIStatusError compensation and 492-token cache assumption are original-only; overhead shown separately.'
        return report
