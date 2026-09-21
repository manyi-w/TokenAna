"""Exercise adapter failure boundaries with simulated transports, never a model."""
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.components import load_component
from src.interfaces import ArtifactDirectory
from src.loading import load_adapter

ROOT = Path(__file__).resolve().parents[1]


class AdapterArtifactsTests(unittest.TestCase):
    def run_adapter(self, name, *, timeout=False, exit_code=0, session_complete=True,
                    patch_failure=False, patch_text='fixture patch\n'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        host = Path(temporary.name)
        artifacts = ArtifactDirectory(host, '/output')
        workspace = SimpleNamespace(root='/repo', new_artifacts=lambda: artifacts,
            launch_command=lambda argv: argv,
            execute=Mock(return_value=subprocess.CompletedProcess([], 0, patch_text, '')))
        if patch_failure:
            workspace.execute.side_effect = subprocess.CalledProcessError(1, ['git', 'diff'])
        agent = load_adapter(load_component(ROOT/'agents'/name, 'agent', {}))
        options = dict(executable='/prepared/agent', python_executable='/prepared/python',
            model='fixture', model_provider='openai', model_protocol='chat_completions',
            model_base_url='http://fixture.invalid/v1', model_api_key_env='FAKE_KEY',
            model_class='litellm', record_raw_usage=True, config_root='/config',
            context_limit=1000, output_limit=100)

        def process(command, **kwargs):
            if name == 'mini_swe_agent':
                trajectory = {'info': {'exit_status': 'Submitted', 'submission': 'done'}, 'messages': []}
                (host/'original-trajectory.json').write_text(json.dumps(trajectory))
            elif name == 'trae':
                trajectory = dict(end_time='finished', success=True, final_result='done',
                                  llm_interactions=[], agent_steps=[])
                (host/'trajectory.json').write_text(json.dumps(trajectory))
            else:
                event = {'type': 'step_finish', 'part': {'id': 'p', 'sessionID': 's',
                    'type': 'step-finish', 'reason': 'stop', 'tokens': {
                        'input': 10, 'output': 2, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}}}}
                kwargs['stdout'].write(json.dumps(event)+'\n')
            if timeout:
                raise subprocess.TimeoutExpired(command, 1, output=b'partial stdout', stderr=b'partial stderr')
            return subprocess.CompletedProcess(command, exit_code, 'stdout', 'stderr')

        (host/'session-outcome.json').write_text(json.dumps({'complete': session_complete}))
        session = SimpleNamespace(started=True, finished=False, termination=None)
        with ExitStack() as stack:
            stack.enter_context(patch('src.model_channel.recording_model_channel',
                                     side_effect=lambda *a, **k: nullcontext('http://fixture.invalid/v1')))
            stack.enter_context(patch('src.session_channel.session_server',
                                     side_effect=lambda *a, **k: nullcontext(session)))
            # Both transport paths are mocked; an accidental external command fails this test.
            stack.enter_context(patch('subprocess.run', side_effect=process))
            stack.enter_context(patch('src.process_logs.run_logged',
                                     side_effect=lambda command, directory, **kwargs: process(command, **kwargs)))
            if name == 'opencode':
                stack.enter_context(patch.object(agent, '_export_session'))
            result = agent.run_session('fixture task', workspace, options, lambda event: None)
        return result, host, workspace

    def test_success_keeps_submission(self):
        for name in ('mini_swe_agent', 'trae', 'opencode'):
            with self.subTest(agent=name):
                result, host, workspace = self.run_adapter(name)
                self.assertIsNone(result.error)
                self.assertTrue(result.submission_eligible)
                self.assertEqual((host/'patch.diff').read_text(), 'fixture patch\n')
                workspace.execute.assert_called_once()

    def test_timeout_never_captures_unconfirmed_workspace(self):
        for name in ('mini_swe_agent', 'trae', 'opencode'):
            with self.subTest(agent=name):
                result, host, workspace = self.run_adapter(name, timeout=True)
                self.assertFalse(result.submission_eligible)
                self.assertIn('Timeout', result.error)
                self.assertEqual((host/'patch.diff').read_text(), '')
                workspace.execute.assert_not_called()
                if name != 'opencode':
                    self.assertEqual((host/'stdout.txt').read_text(), 'partial stdout')

    def test_failed_session_or_exit_retains_diagnostic_patch(self):
        for name in ('mini_swe_agent', 'trae', 'opencode'):
            for failure in ({'session_complete': False}, {'exit_code': 2}):
                with self.subTest(agent=name, failure=failure):
                    result, host, _ = self.run_adapter(name, **failure)
                    self.assertFalse(result.submission_eligible)
                    self.assertTrue(result.error)
                    self.assertEqual((host/'diagnostic.diff').read_text(), 'fixture patch\n')
                    self.assertEqual((host/'patch.diff').read_text(), '')

    def test_capture_failure_and_empty_patch_are_ineligible(self):
        for name in ('mini_swe_agent', 'trae', 'opencode'):
            for failure in ({'patch_failure': True}, {'patch_text': ''}):
                with self.subTest(agent=name, failure=failure):
                    result, host, _ = self.run_adapter(name, **failure)
                    self.assertFalse(result.submission_eligible)
                    self.assertEqual((host/'patch.diff').read_text(), '')
                    if failure.get('patch_failure'):
                        self.assertIn('patch capture failed', result.error)


if __name__ == '__main__':
    unittest.main()
