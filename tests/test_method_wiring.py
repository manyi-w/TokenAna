from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from src.study import load_study
from src.pilot import agent_options
from src.budgets import freeze_budget
from src.accounting import FinalSummary, OriginalCase
from src.components import load_component
from src.loading import load_adapter
from src.models import ModelConfig
from src.records import write_json

ROOT = Path(__file__).resolve().parents[1]


class WiringTests(unittest.TestCase):
    def test_all_paper_agent_model_mappings(self):
        study = load_study(ROOT/'experiments/paper.toml')
        for config in study['configurations']:
            name = {'mini': 'mini_swe_agent'}.get(config['agent'], config['agent'])
            agent = load_adapter(load_component(ROOT/'agents'/name, 'agent', {}))
            provider, protocol = ('anthropic', 'anthropic_messages') if config['model'] == 'claude-opus-5' else (
                ('openai', 'responses') if config['model'] == 'gpt-5.6-sol' else
                ('deepseek', 'chat_completions') if config['model'] == 'deepseek-v4.1-flash' else
                ('dashscope', 'chat_completions') if config['model'] == 'qwen3.8-max' else ('openai', 'chat_completions'))
            model = ModelConfig(config['model'], provider, config['model'], protocol, 'http://fixture.invalid/v1', 'FAKE_KEY')
            agent.configure_model(model, agent_options(config['agent'], {}, {}))
            method = load_adapter(load_component(ROOT/'methods'/config['method'], 'method', {}))
            self.assertTrue(callable(method.original_accounting))
            if config['method'] not in ('baseline', 'run_free', 'turn_control'):
                self.assertTrue(callable(agent.run_session))

    def test_verified_and_deepswe_git(self):
        method = load_adapter(load_component(ROOT/'methods/run_free', 'method', {}))
        task = SimpleNamespace(problem_statement='Example', instance_id='t', repo='r', base_commit='abc')
        verified = method.build_prompt(task)
        deep = method.build_prompt(task, allow_submission_git=True)
        self.assertIn('❌ Run `git` commands', verified)
        self.assertNotIn('❌ Run `git` commands', deep)

    def test_budget_freezes_original_percentiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for i, n in enumerate((1, 2, 10, 20)):
                row = dict(id=str(i), method='baseline', configuration_id='base', case=str(i))
                rows.append(row)
                run = root/'combinations'/str(i)/'run'
                artifact = run/'attempt/artifacts/call-a'
                artifact.mkdir(parents=True)
                write_json(run/'state.json', {'status': 'submission_prepared', 'tasks': {str(i): {'stage': 'collected', 'directory': 'attempt'}}})
                write_json(run/'config.json', {'agent': {'kind': 'agent', 'name': 'fixture', 'path': '/fixture', 'options': {}, 'entrypoint': 'Fixture'}})
            reader = SimpleNamespace(read_final_summary_case=lambda p, case: OriginalCase(case, None, None,
                FinalSummary(True, (1, 2, 10, 20)[int(case)], 0, 0, 'fixture')))
            with patch('src.budgets.load_adapter', return_value=reader):
                budget = freeze_budget(root, rows, configuration_id='base')
                self.assertEqual((budget['initial'], budget['final']), (6, 13))
                self.assertEqual(budget, freeze_budget(root, rows, configuration_id='base'))

    def test_verified_local_plan(self):
        from datasets.swe_bench_verified.local_evaluation import plan
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root/'runtime.json', {'verified_evaluation': {'image': 'fixture', 'jobs': 4}})
            write_json(root/'config.json', {'dataset': {'options': {'task_ids': ['a', 'b']}}})
            result = plan(root, {'run_id': 'fixture', 'predictions': '/saved/predictions.jsonl'})
            self.assertIn('swebench.harness.run_evaluation', result['command'])
            self.assertEqual(result['command'][-2:], ['a', 'b'])
            self.assertIn('False', result['command'])


if __name__ == '__main__':
    unittest.main()
