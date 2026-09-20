"""Mini hand-written session layer with simulated native loop/model/environment."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agents.mini_swe_agent.adapter import MiniSweAgent
from agents.mini_swe_agent.session_mapping import NativeHistory
from agents.mini_swe_agent.session_runner import SessionMiniMixin
from src.interfaces import ArtifactDirectory, Workspace
from src.session import Message, SessionDecision, validate_replacement
from src.session_channel import SessionClient, session_server


class Stop(Exception):
    pass


class FakeModel:
    config = SimpleNamespace(model_name='fake')
    def __init__(self):
        self.inputs = []
    def format_message(self, **kwargs):
        return kwargs
    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in m.items() if k != 'extra'} for m in messages]
    def query(self, messages):
        self.inputs.append(deepcopy(messages))
        return {'role': 'assistant', 'content': 'use tool',
            'tool_calls': [{'id': f't{len(self.inputs)}', 'function': {'name': 'bash', 'arguments': '{}'}}],
            'extra': {'actions': [{}], 'response': {'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}}}


class FakeNative:
    """Only fake execution; tests do not import or initialize LiteLLM/mini dependencies."""
    def __init__(self, model, output_path, **kwargs):
        self.model, self.messages = model, []
        self.n_calls, self.cost = 0, 0
        self._start_time = time.time()
        self.config = SimpleNamespace(mode='yolo', confirm_exit=False, output_path=output_path,
            step_limit=kwargs.get('step_limit', 0), cost_limit=0, wall_time_limit_seconds=0)
    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)
    def query(self):
        if self.config.step_limit and self.n_calls >= self.config.step_limit:
            raise Stop({'extra': {'exit_status': 'LimitsExceeded'}})
        self.n_calls += 1
        result = self.model.query(self.messages)
        self.add_messages(result)
        return result
    def execute_actions(self, message):
        return self.add_messages({'role': 'tool', 'tool_call_id': message['tool_calls'][0]['id'],
                                  'content': 'very long tool output', 'extra': {'raw_output': 'uncompressed'}})
    def run(self, task='fix'):
        self.add_messages({'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': task})
        status = 'Submitted'
        try:
            for _ in range(2):
                self.execute_actions(self.query())
        except Stop as error:
            status = error.args[0]['extra']['exit_status']
        self.add_messages({'role': 'exit', 'content': status, 'extra': {'exit_status': status, 'submission': ''}})
        return {'exit_status': status}
    def save(self, path, *extra):
        data = {'messages': deepcopy(self.messages), 'info': {'exit_status': self.messages[-1].get('extra', {}).get('exit_status'),
                'model_stats': {'api_calls': self.n_calls}}}
        Path(path).write_text(json.dumps(data))
        return data


class FakeSessionMini(SessionMiniMixin, FakeNative):
    session_stop = Stop


class MiniSessionTests(unittest.TestCase):
    def test_actual_history_reminder_events_and_original_usage_survive(self):
        kinds = []
        def callback(event):
            kinds.append(event.kind)
            if event.kind == 'after_tool':
                return SessionDecision([replace(m, text='short') if m.role == 'tool' else m for m in event.history],
                                       {'compressions': event.state.get('compressions', 0) + 1}, 'budget reminder')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = ArtifactDirectory(root, str(root))
            model = FakeModel()
            with session_server(artifacts, callback) as session:
                worker = FakeSessionMini(model, root / 'trajectory.json', session_path=root / 'session-channel')
                result = worker.run()
            self.assertEqual(result['exit_status'], 'Submitted')
            self.assertEqual(model.inputs[1][3]['content'], 'short')
            self.assertEqual(model.inputs[1][-1]['content'], 'budget reminder')
            original = json.loads((root / 'original-trajectory.json').read_text())
            self.assertEqual(original['messages'][3]['content'], 'very long tool output')
            self.assertEqual(MiniSweAgent().read_original_case(root, 'a').trace[0]['usage']['input_tokens'], 7)
            self.assertEqual(session.state['compressions'], 2)
            self.assertEqual(kinds, ['initialize', 'before_model', 'after_model', 'after_tool',
                                     'before_model', 'after_model', 'after_tool', 'finish'])
            self.assertTrue(json.loads((root / 'session-outcome.json').read_text())['complete'])
            self.assertEqual(len(list((root / 'session').glob('*-model-input.json'))), 2)

    def test_terminate_before_query_sends_no_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = FakeModel()
            with session_server(ArtifactDirectory(root, str(root)),
                lambda e: SessionDecision(terminate='budget') if e.kind == 'before_model' else None):
                worker = FakeSessionMini(model, root / 'trajectory.json', session_path=root / 'session-channel')
                self.assertEqual(worker.run()['exit_status'], 'MethodTerminated')
            self.assertEqual(model.inputs, [])
            self.assertEqual(json.loads((root / 'session-outcome.json').read_text())['termination'], 'budget')

    def test_native_limits_do_not_trigger_extra_callback_or_query(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def callback(event):
                events.append(event.kind)
            with session_server(ArtifactDirectory(root, str(root)), callback):
                worker = FakeSessionMini(FakeModel(), root / 'trajectory.json',
                                         session_path=root / 'session-channel', step_limit=1)
                self.assertEqual(worker.run()['exit_status'], 'LimitsExceeded')
            self.assertEqual(events.count('before_model'), 1)

    def test_chat_and_responses_roundtrip_and_multiple_tool_pairing(self):
        for assistant, outputs in [
            ({'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'a'}, {'id': 'b'}]},
             [{'role': 'tool', 'tool_call_id': key, 'content': 'long'} for key in ['a', 'b']]),
            ({'object': 'response', 'id': 'r', 'usage': {'input_tokens': 10}, 'output': [
                {'type': 'reasoning', 'encrypted_content': 'opaque'},
                {'type': 'function_call', 'call_id': 'a', 'arguments': '{}'},
                {'type': 'function_call', 'call_id': 'b', 'arguments': '{}'}]},
             [{'type': 'function_call_output', 'call_id': key, 'output': 'long'} for key in ['a', 'b']])]:
            history = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'task'}, assistant, *outputs]
            mapper = NativeHistory()
            mapped = mapper.encode(history)
            self.assertEqual(mapper.decode(mapped), history)
            before = [Message(**{**m, 'tool_calls': tuple(m['tool_calls'])}) for m in mapped]
            after = [replace(m, text='short') if m.role == 'tool' else m for m in before]
            validate_replacement(before, after)
            mapped[-1]['text'] = 'short'
            restored = mapper.decode(mapped)
            self.assertEqual(restored[2], assistant)
            self.assertEqual(mapper.encode(restored)[2]['id'], mapped[2]['id'])
            with self.assertRaises(ValueError):
                validate_replacement(before, after[:-1])
            with self.assertRaisesRegex(ValueError, 'in-flight'):
                validate_replacement(before[:3], before[:2], pending_tools=True)

    def test_callback_error_returns_failure_and_missing_server_times_out(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def callback(event):
                raise ValueError('invalid method')
            with session_server(ArtifactDirectory(root, str(root)), callback):
                with self.assertRaisesRegex(RuntimeError, 'callback failed'):
                    SessionClient(root / 'session-channel').request('initialize', history=[])
            self.assertFalse(json.loads((root / 'session-outcome.json').read_text())['complete'])
            isolated = root / 'isolated'
            isolated.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                SessionClient(isolated, timeout=.02).request('initialize', history=[])

    def test_adapter_preflight_and_no_silent_plain_run(self):
        options = {'executable': '/mini', 'model': 'openai/fake', 'model_protocol': 'responses',
                   'model_class': 'litellm_response', 'model_base_url': 'http://fake/v1'}
        with patch('subprocess.run', side_effect=AssertionError('must not execute')):
            with self.assertRaisesRegex(ValueError, 'python_executable'):
                MiniSweAgent().run_session('fix', object(), options, lambda e: None)
            with self.assertRaisesRegex(ValueError, 'record_raw_usage'):
                MiniSweAgent().run_session('fix', object(), {**options, 'python_executable': '/python'}, lambda e: None)

    def test_adapter_missing_worker_session_cannot_submit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = Mock(spec=Workspace)
            workspace.root = '/app'
            workspace.new_artifacts.return_value = ArtifactDirectory(root, '/out')
            workspace.launch_command.side_effect = lambda argv: argv
            options = {'executable': '/mini', 'python_executable': '/python', 'model': 'openai/fake',
                       'model_protocol': 'responses', 'model_class': 'litellm_response',
                       'model_base_url': 'http://fake/v1', 'record_raw_usage': True}
            def process(argv, **kwargs):
                (root / 'trajectory.json').write_text(json.dumps({'info': {'exit_status': 'Submitted'}, 'messages': []}))
                return subprocess.CompletedProcess(argv, 0, '', '')
            with patch('src.model_channel.recording_model_channel', return_value=nullcontext('http://recorded')) as proxy, \
                 patch('agents.mini_swe_agent.adapter.subprocess.run', side_effect=process), \
                 patch('agents.mini_swe_agent.adapter.capture_patch', return_value='diff'):
                result = MiniSweAgent().run_session('fix', workspace, options, lambda e: None)
            self.assertFalse(result.submission_eligible)
            self.assertIn('Session callback failed', result.error)
            self.assertEqual((root / 'patch.diff').read_text(), '')
            self.assertEqual((root / 'diagnostic.diff').read_text(), 'diff')
            self.assertEqual(proxy.call_args.kwargs['attribution'], {'purpose': 'main'})
            self.assertFalse(proxy.call_args.kwargs['request_guard']())
            self.assertIn('tokenana_mini_session.SessionMini', workspace.launch_command.call_args.args[0][-1])

    def test_existing_turn_control_composes_with_history_pruning(self):
        import importlib.util
        import sys
        from agents.mini_swe_agent.session_runner import make_agent_classes
        path = Path(__file__).resolve().parents[1] / 'agents/mini_swe_agent/controlled_runner.py'
        stubs = {'minisweagent.agents.interactive': SimpleNamespace(InteractiveAgent=FakeNative),
                 'minisweagent.exceptions': SimpleNamespace(LimitsExceeded=Stop)}
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location('tokenana_mini_control', path)
            controlled = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(controlled)
            with patch.dict(sys.modules, {'tokenana_mini_control': controlled}):
                _, composed = make_agent_classes()
        for final, expected in [(2, 'completed'), (1, 'budget_exhausted')]:
            with self.subTest(final=final), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                def callback(event):
                    if event.kind == 'after_tool':
                        return SessionDecision([m for m in event.history if not m.tool_calls and not m.tool_result])
                with session_server(ArtifactDirectory(root, str(root)), callback):
                    worker = composed(FakeModel(), root / 'trajectory.json',
                        session_path=root / 'session-channel', control_path=root / 'control.json',
                        control_budget={'initial': 1, 'final': final})
                    worker.run()
                control = json.loads((root / 'control.json').read_text())
                self.assertEqual(control['termination_reason'], expected)
                self.assertEqual(control['used_turns'], final)
                self.assertEqual(control['tool_calls'], final)
                self.assertEqual(len(control['extensions']), int(final > 1))
                original = json.loads((root / 'original-trajectory.json').read_text())
                self.assertEqual(len([m for m in original['messages'] if m.get('role') == 'tool']), final)

    def test_adapter_success_in_both_workspace_roots_retains_original_and_patch(self):
        from src.accounting import corrected_accounting
        for workroot in ('/app', '/testbed'):
            with self.subTest(workroot=workroot), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                workspace = Mock(spec=Workspace)
                workspace.root = workroot
                workspace.new_artifacts.return_value = ArtifactDirectory(root, '/out')
                workspace.launch_command.side_effect = lambda argv: argv
                options = {'executable': '/mini', 'python_executable': '/python', 'model': 'openai/fake',
                           'model_protocol': 'responses', 'model_class': 'litellm_response',
                           'model_base_url': 'http://fake/v1', 'record_raw_usage': True}
                def callback(event):
                    if event.kind == 'after_tool':
                        return SessionDecision([m for m in event.history if not m.tool_calls and not m.tool_result])
                def process(argv, **kwargs):
                    worker = FakeSessionMini(FakeModel(), root / 'trajectory.json', session_path=root / 'session-channel')
                    worker.run()
                    records = root / 'api-records/request'
                    records.mkdir(parents=True)
                    (records / 'metadata.json').write_text(json.dumps({'response_complete': True,
                        'model': 'fake', 'attribution': {'purpose': 'main'}, 'duration_sec': 1}))
                    (records / 'response.body').write_text(json.dumps({'id': 'r', 'usage': {'input_tokens': 14, 'output_tokens': 6}}))
                    return subprocess.CompletedProcess(argv, 0, '', '')
                with patch('src.model_channel.recording_model_channel', return_value=nullcontext('http://recorded')), \
                     patch('agents.mini_swe_agent.adapter.subprocess.run', side_effect=process), \
                     patch('agents.mini_swe_agent.adapter.capture_patch', return_value='diff') as capture:
                    result = MiniSweAgent().run_session('fix', workspace, options, callback)
                self.assertTrue(result.submission_eligible)
                self.assertIsNone(result.error)
                self.assertEqual(result.tokens_used, 20)
                self.assertEqual(result.exec_count, 2)
                capture.assert_called_once()
                self.assertEqual(json.loads((root / 'config.yaml').read_text())['environment']['cwd'], workroot)
                self.assertEqual((root / 'patch.diff').read_text(), 'diff')
                self.assertEqual(MiniSweAgent().read_original_case(root, 'a').trace[0]['usage']['input_tokens'], 7)
                usage = MiniSweAgent().read_case_usage(root, case_id='a', attempt_id='1', call_id='c')
                self.assertEqual(corrected_accounting([usage])['metrics']['total']['sum'], 20)
                self.assertEqual(usage.operations[0].purpose, 'main')
