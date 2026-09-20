"""Offline source-algorithm fixtures; fake token counts and transports are explicit."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from src.interfaces import Task
from src.session import Message, Session, SessionDecision, StepSummary
from src.accounting import OriginalCase
from methods.agent_diet.adapter import AgentDiet
from methods.attn_compress.adapter import AttnCompress
from methods.swe_pruner_pro.adapter import SwePrunerPro, MODEL, SERVICE_VERSION
from methods.eet.adapter import EET
from agents.mini_swe_agent.session_mapping import NativeHistory


class FixtureTemplate:
    def __init__(self, text, **kwargs):
        self.text = text
    def render(self, **kwargs):
        return '\n'.join(exp.issue_description for exp, _ in kwargs.get('experiences', []))


def conversation(rounds=4, text='x' * 2400, command='cat source.py'):
    native = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'task'}]
    for i in range(rounds):
        native += [{'role': 'assistant', 'content': 'CONFIDENCE_SCORE: 81', 'tool_calls': [
            {'id': f'call-{i}', 'function': {'name': 'bash', 'arguments': json.dumps({'command': command})}}]},
            {'role': 'tool', 'tool_call_id': f'call-{i}', 'content': text}]
    mapper = NativeHistory()
    return [Message(**{**m, 'tool_calls': tuple(m['tool_calls'])}) for m in mapper.encode(native)]


class MethodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task = Task('repo__project-1', 'repo/project', 'abc', 'repair parser syntax expression')
        self.workspace = SimpleNamespace(root='/testbed', execute=lambda *a, **k: SimpleNamespace(returncode=0, stdout=' M source.py\n'))
        self.details = {'artifact_host': str(self.root), 'artifact_execution': '/artifacts'}

    def tearDown(self):
        self.temp.cleanup()

    def session(self, callback):
        return Session(callback, self.root / 'session')

    def test_diet_uses_original_trigger_prompt_and_whole_step_replacement(self):
        requests = []
        def helper(*args, **kwargs):
            requests.append(args[2])
            return {'choices': [{'message': {'content': 'retained fact</step>'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 8000, 'completion_tokens': 6, 'total_tokens': 8006}}
        callback = AgentDiet().callback(self.task, self.workspace, {'helper_base_url': 'http://fixture/v1', 'helper_api_key_env': 'FAKE'},
                                       tokenizer=list, transport=helper)
        session = self.session(callback)
        history = conversation(3)
        session.emit('initialize', history[:2], **self.details)
        for length in (4, 6, 8):
            result = session.emit('after_tool', history[:length], **self.details)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['model'], 'gpt-5-mini')
        self.assertIn('Now, compress the step 0.', requests[0]['messages'][1]['content'])
        self.assertTrue(any(m.id.startswith('summary-') and 'retained fact' in m.text for m in result.history))
        self.assertEqual([m.tool_result for m in result.history if m.tool_result], ['call-1', 'call-2'])
        session.emit('before_model', result.history, **self.details)

    def test_diet_short_and_low_saving_skip(self):
        calls = []
        def helper(*args, **kwargs):
            calls.append(1)
            return {'choices': [{'message': {'content': 'y' * 3000 + '</step>'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 4000, 'completion_tokens': 3000, 'total_tokens': 7000}}
        callback = AgentDiet().callback(self.task, self.workspace, {'helper_base_url': 'http://fixture', 'helper_api_key_env': 'KEY'}, tokenizer=list, transport=helper)
        session = self.session(callback)
        session.emit('initialize', conversation(0), **self.details)
        result = session.emit('after_tool', conversation(3), **self.details)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(m.id.startswith('summary-') for m in result.history))

    def test_summary_rejects_partial_pair_and_protected_span(self):
        for ids in [('m000004',), ('m000001', 'm000002')]:
            with self.subTest(ids=ids), tempfile.TemporaryDirectory() as root:
                session = Session(lambda e: SessionDecision(summaries=(StepSummary(ids, 'summary'),)), Path(root) / 's')
                with self.assertRaises(ValueError):
                    session.emit('initialize', conversation(1))

    def test_attn_defaults_protect_tail_and_only_replace_tool_text(self):
        requests = []
        def service(*args, **kwargs):
            payload = args[2]
            requests.append(payload)
            messages = json.loads(json.dumps(payload['messages']))
            for i, message in enumerate(messages):
                if message['role'] == 'tool' and payload['step_indices'][i] < 2:
                    message['content'] = 'compressed'
            return {'compressed_messages': messages, 'stats': {'status': 'success'}}
        callback = AttnCompress().callback(self.task, self.workspace, {'service_base_url': 'http://fixture'}, tokenizer=list, transport=service)
        session = self.session(callback)
        history = conversation()
        session.emit('initialize', history[:2], **self.details)
        result = session.emit('after_tool', history, **self.details)
        self.assertEqual(requests[0]['attn_ratio'], .20)
        self.assertEqual(requests[0]['attn_tail'], 2)
        self.assertEqual([m.text for m in result.history if m.tool_result], ['compressed', 'compressed', 'x' * 2400, 'x' * 2400])
        record = json.loads(next((self.root / 'service-records').glob('*.json')).read_text())
        self.assertNotIn('raw_usage', record)

    def test_attn_failure_retains_raw_and_records_failure(self):
        def fail(*args, **kwargs):
            raise TimeoutError('fixture')
        callback = AttnCompress().callback(self.task, self.workspace, {'service_base_url': 'http://fixture'}, tokenizer=list, transport=fail)
        session = self.session(callback)
        history = conversation()
        session.emit('initialize', history[:2], **self.details)
        result = session.emit('after_tool', history, **self.details)
        self.assertEqual(result.history, history)
        self.assertEqual(result.state['failures'], ['TimeoutError'])

    def test_swe_length_keep_all_and_backend_usage(self):
        calls = []
        def service(*args, **kwargs):
            calls.append(args[2])
            return {'pruned_code': 'kept', 'original_chars': 2400, 'pruned_chars': 4,
                    'original_lines': 1, 'kept_line_count': 1,
                    'tokenana': {'version': SERVICE_VERSION, 'backbone': MODEL, 'head_model': MODEL, 'backend_base_url': 'http://backend'},
                    'backend_requests': [{'complete': True, 'duration_sec': .7,
                        'usage': {'prompt_tokens': 100, 'completion_tokens': 1, 'total_tokens': 101}}]}
        callback = SwePrunerPro().callback(self.task, self.workspace,
            {'service_base_url': 'http://fixture', 'backend_base_url': 'http://backend'}, transport=service)
        session = self.session(callback)
        history = conversation(1)
        session.emit('initialize', history[:2], **self.details)
        result = session.emit('after_tool', history, **self.details)
        self.assertEqual(result.history[-1].text, 'kept')
        self.assertEqual(calls[0]['threshold'], .5)
        records = [json.loads(p.read_text()) for p in (self.root / 'service-records').glob('*.json')]
        self.assertEqual(sum(r.get('raw_usage', {}).get('total_tokens', 0) for r in records), 101)
        self.assertEqual(sum(r['inference'] for r in records), 1)

    def test_swe_model_backend_and_head_mismatch_block(self):
        method = SwePrunerPro()
        for model, backend in [('other', 'http://backend'), (MODEL, 'http://wrong')]:
            with self.assertRaises(ValueError):
                method.validate_agent_options({'backend_base_url': 'http://backend'},
                    {'model': model, 'model_protocol': 'chat_completions', 'model_base_url': backend})
        with self.assertRaises(ValueError):
            method.validate_options({'service_base_url': 'http://fixture', 'backend_base_url': 'http://backend',
                                     'backbone': MODEL, 'head_model': MODEL, 'head': str(self.root)})

    def library(self):
        path = self.root / 'experience.jsonl'
        path.write_text(json.dumps({'issue_id': 'repo__project-2', 'issue_description': self.task.problem_statement,
            'task_summary': 'inspect parser grammar', 'confidence': 90}) + '\n')
        return str(path)

    def test_eet_80_81_boundary_and_no_forced_termination(self):
        for score in (80, 81):
            with self.subTest(score=score):
                callback = EET().callback(self.task, self.workspace, {'experience_library': self.library()}, template=FixtureTemplate)
                # One-document TF-IDF can score zero; this is the source retrieval behavior.
                callback_source = self.task
                history = conversation(1)
                history[2] = replace(history[2], text=f'CONFIDENCE_SCORE: {score}')
                # Add a contrasting document so source IDF has a nonzero discriminant.
                path = Path(self.library())
                with path.open('a') as out:
                    out.write(json.dumps({'issue_id':'repo__project-3','issue_description':'unrelated network socket','task_summary':'network','confidence':70})+'\n')
                callback = EET().callback(self.task, self.workspace, {'experience_library': str(path)}, template=FixtureTemplate)
                from src.session import SessionEvent
                decision = callback(SessionEvent('after_tool', history, {}, self.details))
                self.assertEqual(decision.state['submission_prompts'], int(score >= 81))
                self.assertIsNone(decision.terminate)

    def test_eet_no_hit_is_explicit(self):
        callback = EET().callback(self.task, self.workspace, {'experience_library': self.library()}, template=FixtureTemplate)
        from src.session import SessionEvent
        result = callback(SessionEvent('initialize', conversation(0), {}, self.details))
        self.assertFalse(result.state['experience_hit'])
        self.assertIsNone(result.reminder)

    def test_eet_trae_has_no_confidence_stopping(self):
        callback = EET().callback(self.task, self.workspace, {'experience_library': self.library()},
                                  template=FixtureTemplate, agent_kind='trae')
        from src.session import SessionEvent
        result = callback(SessionEvent('after_tool', conversation(1), {}, self.details))
        self.assertEqual(result.state['submission_prompts'], 0)
        self.assertIsNone(result.terminate)

    def test_diet_author_cache_and_compensation_are_separate(self):
        case = OriginalCase('one', [{'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 3}}], '',
            method_data={'source_error': 'APIStatusError', 'metrics': {'analysis_count': 2, 'analysis_prompt_tokens': 2000, 'analysis_completion_tokens': 9}})
        report = AgentDiet().original_accounting([case])
        self.assertEqual(report['metrics']['input']['sum'], 200100)
        self.assertEqual(report['overhead']['metrics']['input']['sum'], 1016)


if __name__ == '__main__':
    unittest.main()


class FailureAndMappingTests(unittest.TestCase):
    setUp = MethodTests.setUp
    tearDown = MethodTests.tearDown
    session = MethodTests.session
    def test_swe_missing_service_capability_is_not_plain_run(self):
        options = {'service_base_url': 'http://fixture', 'backend_base_url': 'http://backend'}
        callback = SwePrunerPro().callback(self.task, self.workspace, options, transport=lambda *a, **kw: {})
        session = self.session(callback)
        history = conversation(1)
        session.emit('initialize', history[:2], **self.details)
        with self.assertRaisesRegex(RuntimeError, 'matching recorded service'):
            session.emit('after_tool', history, **self.details)
        records = [json.loads(p.read_text()) for p in (self.root / 'service-records').glob('*.json')]
        self.assertTrue(any(r['inference'] and not r.get('raw_usage') for r in records))

    def test_swe_transport_failure_retains_raw_text_and_unknown_usage(self):
        def failure(*a, **kw): raise TimeoutError('fixture')
        callback = SwePrunerPro().callback(self.task, self.workspace,
            {'service_base_url': 'http://fixture', 'backend_base_url': 'http://backend'}, transport=failure)
        session = self.session(callback)
        history = conversation(1)
        session.emit('initialize', history[:2], **self.details)
        self.assertEqual(session.emit('after_tool', history, **self.details).history[-1].text, history[-1].text)
        records = [json.loads(p.read_text()) for p in (self.root / 'service-records').glob('*.json')]
        self.assertTrue(any(r['inference'] and not r.get('raw_usage') for r in records))

    def test_single_user_prefix_does_not_protect_first_assistant(self):
        mapping = NativeHistory()
        native = [{'role': 'user', 'content': 'task'}, {'role': 'assistant', 'content': 'step'}]
        result = mapping.encode(native)
        self.assertFalse(result[0]['editable'])
        self.assertTrue(result[1]['editable'])
