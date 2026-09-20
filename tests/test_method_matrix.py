"""48 logical combinations: real config/readers/report pipeline, simulated records.

No native agent/model/container execution. Session algorithms and native mappings
are exercised independently in test_method_integrations and test_*session.
"""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.config import load_experiment
from src.loading import load_adapter
from src.planning import plan_experiment
from src.run_accounting import build_accounting, original_supported
from src.analysis import export_accounting, compare_runs
from src.interfaces import Task

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def native_records(root, agent):
    usage = {'input_tokens': 100, 'output_tokens': 20}
    write(root / 'process.json', {'returncode': 0})
    (root / 'patch.diff').write_text('diff --git a/source b/source\n')
    write(root / 'method-state.json', {'metrics': {}, 'source_error': None})
    if agent == 'codex':
        jsonl(root / 'trace.jsonl', [{'type': 'thread.started', 'thread_id': 'thread'},
            {'type': 'turn.started'}, {'type': 'turn.completed', 'usage': usage}])
        write(root / 'control-request.json', {'version': 'codex-sampling-boundary-v1', 'profile': 'gpt'})
        base = {'version': 'codex-sampling-boundary-v1', 'initial_budget': 50, 'final_budget': 67,
                'current_budget': 50, 'extended': False}
        jsonl(root / 'control-events.jsonl', [dict(base, event='initialized', turn_id='turn', used_turns=0),
                dict(base, event='before_sampling', turn_id=None, used_turns=1)])
        jsonl(root / 'codex-home/sessions/root.jsonl', [
            {'type': 'session_meta', 'payload': {'id': 'thread', 'source': 'exec'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer', 'content': [
                {'type': 'input_text', 'text': '<tokenana_budget>\nThis is main-loop interaction 1 of 50.'}]}},
            {'type': 'token_usage_record', 'payload': {'thread_id': 'thread', 'turn_id': 'turn', 'response_id': 'r', 'usage': usage}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': usage}}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'turn'}}])
    elif agent == 'mini_swe_agent':
        write(root / 'trajectory.json', {'info': {'exit_status': 'Submitted', 'model_stats': {'api_calls': 1}},
            'messages': [{'role': 'assistant', 'content': 'done', 'usage': usage},
                         {'role': 'exit', 'extra': {'exit_status': 'Submitted'}}]})
    elif agent == 'trae':
        write(root / 'trajectory.json', {'end_time': 'fixture', 'success': True, 'agent_steps': [{'tool_calls': []}],
            'llm_interactions': [{'response': {'usage': usage}}]})
    else:
        tokens = {'input': 90, 'output': 15, 'reasoning': 5, 'cache': {'read': 7, 'write': 3}}
        part = {'type': 'step-finish', 'id': 'part', 'sessionID': 'session', 'messageID': 'assistant', 'tokens': tokens, 'reason': 'stop'}
        jsonl(root / 'trace.jsonl', [{'type': 'step_finish', 'part': part}])
        write(root / 'control.json', {'version': 'opencode-message-boundary-v1', 'initialized': True,
            'initial_budget': 50, 'final_budget': 67, 'used_turns': 1, 'active_budget': 50,
            'turns': [{'number': 1, 'message_id': 'assistant', 'active_budget': 50}], 'extensions': [],
            'main_session': 'session', 'termination_reason': 'running'})
        write(root / 'session.json', {'info': {'id': 'session'}, 'messages': [
            {'info': {'id': 'user', 'sessionID': 'session', 'role': 'user'}, 'parts': []},
            {'info': {'id': 'assistant', 'sessionID': 'session', 'role': 'assistant', 'agent': 'build',
                      'parentID': 'user', 'time': {'completed': 1}, 'finish': 'stop'}, 'parts': [part]}]})


def raw_request(root, purpose, usage=True):
    write(root / 'request/metadata.json', {'response_complete': True, 'model': 'fixture',
        'attribution': {'purpose': purpose}, 'duration_sec': .1, 'response_headers': []})
    write(root / 'request/response.body', {'id': purpose, 'usage': {'input_tokens': 100, 'output_tokens': 20,
        'input_tokens_details': {'cached_tokens': 40}, 'output_tokens_details': {'reasoning_tokens': 5}}} if usage else {'error': 'fixture failure'})


class MatrixTests(unittest.TestCase):
    def test_48_configurations_native_readers_both_accounts_and_resume(self):
        configs = sorted((ROOT / 'experiments/method_matrix').glob('*.toml'))
        base = [path for path in configs if not path.name.startswith('run_free_multilingual_')]
        self.assertEqual(len(base), 48)
        self.assertEqual(len(configs), 52)
        combinations = set()
        with tempfile.TemporaryDirectory() as temporary:
            for path in configs:
                with self.subTest(config=path.name):
                    config = load_experiment(path)
                    plan = plan_experiment(config)
                    self.assertEqual(plan['status'], 'plan_only')
                    self.assertFalse(plan['runtime_ready'])
                    self.assertGreater(plan['task_count'], 0)
                    method, agent = load_adapter(config.method), load_adapter(config.agent)
                    self.assertTrue(original_supported(method, agent))
                    self.assertEqual(plan['accounting_compatibility']['status'], 'configuration_compatible')
                    if config.method.name in ('agent_diet', 'attn_compress', 'eet', 'swe_pruner_pro'):
                        self.assertEqual(plan['method_compatibility']['status'], 'blocked')  # missing prepared dependencies/head
                    else:
                        self.assertEqual(plan['method_compatibility']['status'], 'configuration_compatible')
                    if not path.name.startswith('run_free_multilingual_'):
                        combinations.add((config.method.name, config.agent.name, config.dataset.name))
                    output = Path(temporary) / path.stem
                    attempt = output / 'tasks/case/attempt-0001'
                    artifact = attempt / 'artifacts/call-0001'
                    artifact.mkdir(parents=True)
                    native_records(artifact, config.agent.path.name)
                    raw_request(artifact / 'api-records', 'main')
                    raw_request(artifact / 'auxiliary-records/native', 'agent_auxiliary')
                    raw_request(artifact / 'auxiliary-records/method', 'method_auxiliary')
                    write(attempt / 'calls.json', {'calls': [{'id': 'call-0001', 'artifacts': ['artifacts/call-0001']}]})
                    relative = 'tasks/case/attempt-0001'
                    state = {'status': 'submission_prepared', 'tasks': {'case': {'directory': relative, 'attempts': [relative, relative]}}}
                    tasks = [Task('case', 'repo', 'base', 'fix')]
                    report = build_accounting(output, state, tasks, method, agent)
                    self.assertEqual(report['original_token_accounting']['metrics']['total']['sum'], 120)
                    self.assertTrue(report['original_token_accounting']['metrics']['total']['complete'])
                    self.assertEqual(report['corrected_token_accounting']['metrics']['total']['sum'], 360)
                    self.assertEqual(report['overhead']['corrected']['method_overhead']['metrics']['total']['sum'], 120)
                    self.assertEqual(report['overhead']['corrected']['all']['metrics']['cache_read']['sum'], 120)
                    if config.method.name == 'turn_control':
                        self.assertIsNone(report['original_token_accounting']['metrics']['total']['mean'])
                    self.assertEqual(report, build_accounting(output, state, tasks, method, agent))
                    report['experiment'] = {'method': {'name': config.method.name}, 'agent': {'name': config.agent.name},
                                             'dataset': {'name': config.dataset.name}}
                    export_accounting(report, output)
                    for name in ('accounting.json', 'accounting.md', 'accounting.txt', 'overhead.csv'):
                        self.assertTrue((output / name).is_file())
                    compare_runs([output, output], output / 'compare')
                    self.assertIn('method_overhead', (output / 'compare/comparison.md').read_text())
            self.assertEqual(len(combinations), 48)

    def test_unselected_dependencies_remain_unloaded(self):
        import subprocess
        import sys
        # A fresh interpreter proves component discovery does not import optional stacks.
        code = "from pathlib import Path; from src.config import load_experiment; from src.planning import plan_experiment; import sys; plan_experiment(load_experiment('experiments/method_matrix/run_free_codex_verified.toml')); assert not {'torch','transformers','tiktoken','jinja2','openai','litellm'} & sys.modules.keys()"
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True, capture_output=True)
