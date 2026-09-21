"""Policy, serialization and launcher invariants affected by shared helpers."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import csv
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.accounting import CaseUsage, UsageObservation, corrected_accounting
from src.accounting_v2 import corrected_v2
from src import pilot
from src.raw_usage import read_usage_views
from src.records import write_json
from src.service_usage import read_services
from src.study_statistics import paired_records, pair_outcome
from src.tabular import csv_file, append_csv, markdown


class AccountingRegressionTests(unittest.TestCase):
    def test_v1_and_v2_keep_distinct_evidence_and_denominators(self):
        item = UsageObservation('a', 'attempt', 'call', 'response',
                                {'input': 10, 'output': 2, 'total': 12}, 'response.body')
        cases = [CaseUsage('a', True, [item, replace(item, source='copy.body')]), CaseUsage('b', False)]
        self.assertEqual(corrected_accounting(cases)['metrics']['total']['mean'], 12)
        self.assertEqual(corrected_v2(cases)['metrics']['total']['mean'], 6)
        differing = [CaseUsage('a', True, [item, replace(item, raw_usage={'additional': 1})])]
        self.assertEqual(corrected_accounting(differing)['metrics']['total']['sum'], 12)
        with self.assertRaisesRegex(ValueError, 'Conflicting response evidence'):
            corrected_v2(differing)

    def test_empty_confirmed_call_remains_unknown(self):
        for account in (corrected_accounting, corrected_v2):
            value = account([CaseUsage('a', True)])['metrics']['total']
            self.assertFalse(value['complete'])
            self.assertIsNone(value['sum'])

    def test_raw_views_read_response_once_and_preserve_prepared_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, meta, body in [
                ('prepared', {'forwarded_at': None}, None),
                ('rejected', {'rejected': True}, None),
                ('complete', {'forwarded_at': 1, 'response_complete': True},
                 {'id': 'r', 'usage': {'input_tokens': 10, 'output_tokens': 2}}),
            ]:
                path = root/name
                path.mkdir()
                write_json(path/'metadata.json', {'protocol': 'responses', **meta})
                if body:
                    write_json(path/'response.body', body)
            original = Path.read_bytes
            reads = []
            def read(path):
                reads.append(path)
                return original(path)
            with patch.object(Path, 'read_bytes', read):
                legacy, api = read_usage_views(root, case_id='a', attempt_id='one', call_id='main')
            self.assertEqual(reads.count(root/'complete/response.body'), 1)
            self.assertFalse(legacy.coverage_complete)
            self.assertTrue(api.coverage_complete)
            self.assertEqual(sum(op.inference for op in api.operations), 1)
            self.assertEqual(corrected_v2([api])['metrics']['total']['sum'], 12)

    def test_service_evidence_keeps_local_compute_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root/'local.json', {'version': 1, 'inference': True,
                'complete': True, 'attribution': {'purpose': 'compression_service'},
                'local_compute': {'forward_count': 3, 'input_tokens': 100}})
            (root/'broken.json').write_text('{')
            local = []
            usage = read_services(root, case_id='a', attempt_id='one', call_id='service', local_compute=local)
            self.assertFalse(usage.coverage_complete)
            self.assertTrue(all(o.metrics['total'] is None for o in usage.observations))
            self.assertEqual(len(local), 2)
            self.assertEqual(local[1]['compute']['forward_count'], 3)


class ReportRegressionTests(unittest.TestCase):
    def test_csv_conventions_and_streamed_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [{'missing': None, 'flag': True, 'nested': ['中文']}]
            csv_file(root/'legacy.csv', rows, legacy=True)
            csv_file(root/'study.csv', rows)
            def read(name):
                with (root/name).open(newline='') as stream:
                    return next(csv.DictReader(stream))
            self.assertEqual(read('legacy.csv'), {'missing': 'null', 'flag': 'true', 'nested': '["中文"]'})
            self.assertEqual(read('study.csv'), {'missing': '', 'flag': 'True', 'nested': '["中文"]'})
            self.assertFalse((root/'legacy.csv.tmp').exists())
            append_csv(root/'stream.csv', [{'case': 'a'}])
            append_csv(root/'stream.csv', [{'case': 'b', 'new': 3}])
            with (root/'stream.csv').open(newline='') as stream:
                saved = list(csv.DictReader(stream))
            self.assertEqual(json.loads(saved[1]['extra_fields']), {'new': 3})
            self.assertIn('a\\|b<br>c', markdown([{'text': 'a|b\nc'}], ['text']))

    def test_pair_alignment_keeps_missing_baseline_and_unknown_outcomes(self):
        base = dict(configuration_id='base', case='a', baseline_id=None, resolved=True)
        rows = [base, dict(configuration_id='method', case='a', baseline_id='base', resolved=False),
                dict(configuration_id='method', case='b', baseline_id='base', resolved=False)]
        pairs = paired_records(rows)
        self.assertIs(pairs[1][1], base)
        self.assertIsNone(pairs[2][1])
        self.assertEqual([pair_outcome(*pair) for pair in pairs], ['unknown', 'baseline_only_success', 'unknown'])


class LauncherRegressionTests(unittest.TestCase):
    def test_recorded_history_exits_before_preflight_or_docker(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stack.enter_context(patch.object(pilot, 'read_settings', return_value=({}, {})))
            stack.enter_context(patch('src.study.task_catalog', return_value={'a': {'language': 'python'}}))
            stack.enter_context(patch.object(pilot, 'fixed_cases', return_value=['a']))
            stack.enter_context(patch('src.pilot_history.filter_history', return_value=([], [], [])))
            prepare = stack.enter_context(patch.object(pilot, 'preflight'))
            docker = stack.enter_context(patch('subprocess.run', side_effect=AssertionError('unexpected process')))
            with redirect_stdout(io.StringIO()):
                result = pilot.main(['--dry-run', '--output', tmp])
            self.assertEqual(result, 0)
            prepare.assert_not_called()
            docker.assert_not_called()

    def test_runtime_resume_preserves_exact_saved_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'run').mkdir()
            saved = {'legacy': True, 'network': 'saved-network', 'images': {'a': 'saved-image'}}
            write_json(root/'run/runtime.json', saved)
            before = (root/'run/runtime.json').read_bytes()
            row = dict(model='m', agent='mini', case='a', dataset='verified')
            settings = {'models': {'m': {'api_key_env': 'FAKE_KEY'}}}
            images = {'mini': {'a': 'new-image'}, 'verified_verifier': 'verifier'}
            result = pilot.case_runtime(row, settings, {'relaxed_storage': False}, images, root, resume=True)
            self.assertEqual(result, saved)
            self.assertEqual((root/'run/runtime.json').read_bytes(), before)

    def test_baselines_finish_before_budget_freezing_and_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [dict(id='base', method='baseline'),
                    dict(id='method', method='turn_control', baseline_id='base', profile_id='controlled')]
            manifest = {'study': True, 'profiles': {}}
            order = []
            def freeze(*args, **kwargs):
                self.assertEqual(order, ['base'])
                order.append('budget')
                return {'initial': 1, 'final': 2}
            def run(row):
                if row['method'] != 'baseline':
                    self.assertEqual(manifest['profiles']['controlled'], {'frozen': True})
                order.append(row['id'])
            stop = Mock()
            with patch('src.budgets.freeze_budget', side_effect=freeze), \
                    patch.object(pilot, 'freeze_profiles', return_value={'controlled': {'frozen': True}}), \
                    patch.object(pilot, 'summarize'):
                pilot.run_phases(rows, manifest, {}, root, run, jobs=4, stop=stop)
            self.assertEqual(order, ['base', 'budget', 'method'])
            stop.assert_not_called()

    def test_phase_failure_stops_before_submitting_more_work(self):
        run = Mock(side_effect=RuntimeError('fixture failure'))
        stop = Mock()
        with patch.object(pilot, 'summarize'), self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            pilot.run_phases([{'id': str(i)} for i in range(4)], {}, {}, Path('.'), run, jobs=1, stop=stop)
        run.assert_called_once()
        stop.assert_called_once()

    def test_resume_terminal_results_never_launches_controllers(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            rows = [{'id': status} for status in ('completed', 'agent_failed', 'not_submitted')]
            for row in rows:
                parent = root/'combinations'/row['id']
                parent.mkdir(parents=True)
                write_json(parent/'result.json', {'status': row['id']})
            stack.enter_context(patch('src.pilot_images.prepare_images', return_value={}))
            stack.enter_context(patch.object(pilot, 'ensure_stopped'))
            stack.enter_context(patch.object(pilot, 'summarize'))
            stack.enter_context(patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)))
            launch = stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('unexpected controller')))
            failures = pilot.run_matrix(root, rows, {}, {}, {}, None, jobs=4, resume=True)
            self.assertEqual(set(failures), {'agent_failed', 'not_submitted'})
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
