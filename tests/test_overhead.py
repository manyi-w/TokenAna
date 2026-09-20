"""Offline response/service fixtures; no sockets, containers or model API."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from src.accounting import CaseUsage, UsageObservation, UsageOperation, corrected_accounting
from src.overhead import overhead_accounting, overhead_rows
from src.raw_usage import read_raw_usage
from src.service_usage import record_service, read_services
from src.interfaces import ArtifactDirectory, Task
from src.run_accounting import build_accounting
from src.analysis import export_accounting, compare_runs


METRICS = {'input': 100, 'output': 20, 'total': 120, 'cache_read': 40, 'cache_write': 10, 'reasoning': 5}


def case(name='a', purpose='main', attempt='1'):
    item = UsageObservation(name, attempt, 'call', 'response', METRICS, 'fixture',
                            purpose=purpose, model='fake', operation_id='request')
    op = UsageOperation(name, attempt, 'call', 'request', purpose, 'fake', duration_sec=2)
    return CaseUsage(name, True, [item], operations=[op])


class OverheadTests(unittest.TestCase):
    def test_disjoint_cache_denominator_and_idempotence(self):
        main, helper = case(), case(purpose='method_auxiliary', attempt='2')
        a = replace(main, observations=[*main.observations, *helper.observations, *helper.observations],
                    operations=[*main.operations, *helper.operations, *helper.operations])
        report = overhead_accounting([a, case('b'), CaseUsage('not-started', False)])
        groups = report['corrected']
        self.assertEqual(groups['all']['metrics']['total']['sum'], 360)
        self.assertEqual(groups['method_overhead']['metrics']['total']['sum'], 120)
        self.assertEqual(groups['method_overhead']['metrics']['total']['mean'], 60)
        self.assertEqual(groups['all']['metrics']['cache_read']['sum'], 120)
        self.assertEqual(groups['all']['metrics']['calls']['sum'], 3)
        self.assertEqual(groups['all']['metrics']['service_seconds']['sum'], 6)
        self.assertIsNone(report['original']['metrics']['total']['sum'])
        self.assertEqual(report, overhead_accounting([a, case('b'), CaseUsage('not-started', False)]))

    def test_unknown_attribution_is_not_zero_and_legacy_total_unchanged(self):
        legacy = replace(case(), observations=[replace(case().observations[0], purpose='unknown')], operations=[])
        result = overhead_accounting([legacy])
        self.assertEqual(result['corrected']['all']['metrics']['total'], corrected_accounting([legacy])['metrics']['total'])
        overhead = result['corrected']['method_overhead']['metrics']['total']
        self.assertIsNone(overhead['sum'])
        self.assertFalse(overhead['complete'])
        self.assertTrue(all(not row['complete'] for row in overhead_rows(None)))

    def test_conflicting_operation_attribution_rejected(self):
        a = case()
        with self.assertRaisesRegex(ValueError, 'conflicting operation'):
            overhead_accounting([replace(a, operations=[a.operations[0], replace(a.operations[0], purpose='method_auxiliary')])])

    def write_request(self, root, name='request', purpose='main', usage=True):
        root = root / name
        root.mkdir(parents=True)
        (root / 'metadata.json').write_text(json.dumps({'response_complete': True, 'model': 'fake',
            'attribution': {'purpose': purpose}, 'duration_sec': .25, 'response_headers': []}))
        (root / 'response.body').write_text(json.dumps({'id': 'r', 'usage': {
            'input_tokens': 100, 'output_tokens': 20}} if usage else {'error': 'busy'}))

    def test_failed_request_counted_and_partial_metadata_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_request(root, usage=False, purpose='method_auxiliary')
            result = read_raw_usage(root, case_id='a', attempt_id='1', call_id='c')
            self.assertTrue(result.llm_called)
            self.assertEqual(len(result.operations), 1)
            metrics = overhead_accounting([result])['corrected']['method_overhead']['metrics']
            self.assertEqual(metrics['calls']['sum'], 1)
            self.assertIsNone(metrics['total']['sum'])
            (root / 'request' / 'metadata.json').write_text('[]')
            self.assertFalse(read_raw_usage(root, case_id='a', attempt_id='1', call_id='c').coverage_complete)

    def test_service_unknown_usage_not_replaced_by_text_savings(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts = ArtifactDirectory(Path(temp), '/artifacts')
            with self.assertRaises(TimeoutError):
                with record_service(artifacts, model='compressor') as result:
                    result['effects'] = {'before_tokens': 10000, 'after_tokens': 100}
                    raise TimeoutError()
            usage = read_services(Path(temp) / 'service-records', case_id='a', attempt_id='1', call_id='c')
            self.assertEqual(len(usage.operations), 1)
            self.assertIsNone(corrected_accounting([usage])['metrics']['total']['sum'])
            self.assertFalse(usage.coverage_complete)
            self.assertIsNotNone(usage.operations[0].duration_sec)

    def test_rebuild_includes_auxiliary_channels_exports_and_compare(self):
        class Method:
            pass
        class Agent:
            def read_case_usage(self, directory, **identity):
                return read_raw_usage(directory / 'api-records', **identity)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attempt = root / 'tasks/a/attempt-0001'
            artifact = attempt / 'artifacts/call-0001'
            self.write_request(artifact / 'api-records')
            self.write_request(artifact / 'auxiliary-records/helper', purpose='method_auxiliary')
            (attempt / 'calls.json').write_text(json.dumps({'calls': [{'id': 'call-0001', 'artifacts': ['artifacts/call-0001']}]}))
            state = {'status': 'submission_prepared', 'tasks': {'a': {'directory': 'tasks/a/attempt-0001',
                    'attempts': ['tasks/a/attempt-0001', 'tasks/a/attempt-0001']}}}
            report = build_accounting(root, state, [Task('a', 'repo', 'base', 'fix')], Method(), Agent())
            self.assertEqual(report['corrected_token_accounting']['metrics']['total']['sum'], 240)
            self.assertEqual(report['overhead']['corrected']['method_overhead']['metrics']['total']['sum'], 120)
            self.assertEqual(report, build_accounting(root, state, [Task('a', 'repo', 'base', 'fix')], Method(), Agent()))
            report['experiment'] = {'dataset': {'name': 'fake'}, 'agent': {'name': 'fake'}, 'method': {'name': 'fake'}}
            export_accounting(report, root)
            self.assertIn('method_overhead', (root / 'accounting.md').read_text())
            self.assertIn('method_overhead', (root / 'accounting.txt').read_text())
            self.assertIn('method_overhead', (root / 'overhead.csv').read_text())
            compare_runs([root, root], root / 'comparison')
            self.assertIn('method_overhead', (root / 'comparison/comparison.md').read_text())
            self.assertIn('method_overhead', (root / 'comparison/overhead.txt').read_text())

    def test_auxiliary_channel_identity_and_state_are_isolated(self):
        from contextlib import contextmanager, nullcontext
        from unittest.mock import patch
        from src.model_channel import recording_model_channel
        class Workspace:
            usage_identity = {'case_id': 'a', 'attempt_id': '1', 'call_id': 'c'}
            seen = []
            @contextmanager
            def expose_model_endpoint(self, endpoint, artifacts):
                self.seen.append(artifacts)
                yield endpoint
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace()
            artifacts = ArtifactDirectory(Path(temp), '/out')
            with patch('src.usage_proxy.recording_proxy', return_value=nullcontext('http://fake')) as proxy:
                with recording_model_channel(workspace, artifacts, 'http://explicit-helper/v1',
                        channel_name='diet', attribution={'purpose': 'method_auxiliary', 'parent_call_id': 'r1'}):
                    pass
                with recording_model_channel(workspace, artifacts, 'http://explicit-backend/v1', channel_name='pruner'):
                    pass
            self.assertNotEqual(workspace.seen[0].host, workspace.seen[1].host)
            self.assertEqual(workspace.seen[0].execution, '/out/auxiliary-channel-state/diet')
            self.assertEqual(proxy.call_args_list[0].kwargs['attribution'], {
                **workspace.usage_identity, 'purpose': 'method_auxiliary', 'parent_call_id': 'r1'})
            self.assertEqual(proxy.call_args_list[0].args[1], 'http://explicit-helper/v1')

    def test_service_usage_and_orchestration_not_double_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts = ArtifactDirectory(Path(temp), '/out')
            with record_service(artifacts, model='fake', parent_call_id='parent') as result:
                result.update(raw_usage={'input_tokens': 10, 'output_tokens': 2}, protocol='responses')
            with record_service(artifacts, inference=False):
                pass
            usage = read_services(Path(temp) / 'service-records', case_id='a', attempt_id='1', call_id='c')
            report = overhead_accounting([usage])
            self.assertEqual(report['corrected']['all']['metrics']['total']['sum'], 12)
            self.assertTrue(report['corrected']['all']['metrics']['total']['complete'])
            self.assertEqual(report['corrected']['method_overhead']['metrics']['calls']['sum'], 2)
            self.assertEqual(usage.observations[0].parent_call_id, 'parent')

    def test_author_overhead_is_kept_separate(self):
        author = {'rule': 'author-fixture', 'cases_counted': 1, 'metrics': {
            'input': {'sum': 999, 'mean': None, 'complete': True, 'reasons': []}}}
        report = overhead_accounting([case()], author)
        self.assertEqual(report['original'], author)
        self.assertEqual(report['corrected']['all']['metrics']['input']['sum'], 100)

    def test_mismatched_case_identity_keeps_subtotal_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_request(root)
            path = root / 'request/metadata.json'
            meta = json.loads(path.read_text())
            meta['attribution']['case_id'] = 'different-case'
            path.write_text(json.dumps(meta))
            usage = read_raw_usage(root, case_id='a', attempt_id='1', call_id='c')
            metric = corrected_accounting([usage])['metrics']['total']
            self.assertEqual(metric['sum'], 120)
            self.assertFalse(metric['complete'])
            self.assertTrue(any('attribution' in reason for reason in metric['reasons']))
