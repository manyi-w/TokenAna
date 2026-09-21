from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from src.components import load_component
from src.records import write_json
from src.study import load_study, study_rows, export_study
from src.study_report import build_report

ROOT = Path(__file__).resolve().parents[1]


def fixture(root):
    study = load_study(ROOT/'experiments/paper.toml')
    study['datasets'] = {'deepswe': study['datasets']['deepswe'][:2]}
    study['configurations'] = [c for c in study['configurations'] if c['group'] == 'deepswe-general'
                             and c['agent'] == 'mini' and c['model'] == 'gpt-5.6-sol' and c['method'] in ('baseline', 'run_free')]
    for c in study['configurations']:
        c['selected'] = 2
    study['selected'] = 4
    study['analysis']['bootstrap_samples'] = 100
    rows = study_rows(study)
    profiles = {}
    for i, row in enumerate(rows):
        run = root/'combinations'/row['id']/'run'
        run.mkdir(parents=True)
        task = next(t for t in study['datasets']['deepswe'] if t['instance_id'] == row['case'])
        config = {kind: asdict(load_component(ROOT/folder/name, kind, options)) for kind, folder, name, options in [
            ('method', 'methods', row['method'], {}), ('agent', 'agents', 'mini_swe_agent', {}),
            ('dataset', 'datasets', 'deepswe', {'task_ids': [row['case']]})]}
        config['model'] = dict(name='gpt-5.6-sol', provider='openai', model_id='gpt-5.6-sol', protocol='responses',
                               base_url='http://fixture.invalid/v1', api_key_env='FAKE_KEY')
        config['path'] = 'fixture'
        write_json(run/'config.json', config)
        profile = json.loads((run/'config.json').read_text())
        profile.pop('path')
        profile['dataset']['options'].pop('task_ids')
        profiles[row['configuration_id']] = profile
        row['profile_id'] = row['configuration_id']
        write_json(run/'pricing.json', study['pricing'])
        write_json(run/'tasks.json', [{k: task[k] for k in ('instance_id', 'repo', 'base_commit')} | {'problem_statement': 'fixture'}])
        relative = f'tasks/{row["case"]}/attempt-0001'
        attempt = run/relative
        artifact = attempt/'artifacts/call-fixed'
        api = artifact/'api-records/request-1'
        api.mkdir(parents=True)
        write_json(attempt/'calls.json', {'calls': [{'id': 'call-0001', 'artifacts': ['artifacts/call-fixed']}]})
        write_json(run/'state.json', {'status': 'submission_prepared', 'tasks': {row['case']: {
            'directory': relative, 'attempts': [relative], 'stage': 'collected', 'resolved': row['method'] == 'baseline'}}, 'evaluation': {}})
        n = 100 if row['method'] == 'baseline' else 50
        usage = dict(input_tokens=n, output_tokens=10, total_tokens=n+10,
            input_tokens_details={'cached_tokens': 0, 'cache_write_tokens': 0}, output_tokens_details={'reasoning_tokens': 0})
        write_json(api/'metadata.json', {'protocol': 'responses', 'provider': 'openai', 'model': 'gpt-5.6-sol',
            'status': 200, 'started_at': 1, 'finished_at': 2, 'forwarded_at': 1, 'response_complete': True,
            'attribution': {'purpose': 'main'}})
        (api/'request.body').write_text(json.dumps({'model': 'gpt-5.6-sol', 'input': 'Fix fixture'}))
        (api/'response.body').write_text(json.dumps({'id': 'response', 'usage': usage}))
        write_json(artifact/'trajectory.json', {'info': {}, 'messages': [{'role': 'assistant', 'extra': {
            'response': {'id': 'response', 'usage': usage}}}]})
        (artifact/'patch.diff').write_text('fixture patch')
        (attempt/'timing.jsonl').write_text('\n'.join(map(json.dumps, [
            dict(id='g', phase='generation', event='start', utc_seconds=1),
            dict(id='g', phase='generation', event='end', utc_seconds=3, seconds=2)])))
    write_json(root/'pilot.json', {'study': study, 'rows': rows, 'profiles': profiles, 'measurement_jobs': 1})
    return study, rows


class EndToEndTests(unittest.TestCase):
    def test_matrix_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = export_study(ROOT/'experiments/paper.toml', Path(tmp)/'selection')
            self.assertEqual(result['configurations'], 109)
            self.assertEqual(result['selected'], 12278)

    def test_reconstruction_failure_diagnostics_and_original_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            study, rows = fixture(root)
            out = Path(tmp)/'report'
            out.mkdir()
            result = build_report(study, rows, root, out, jobs=4, figures=False)
            data = json.loads((out/'research.json').read_text())
            self.assertEqual(result['complete_token_configurations'], 2, data['reconstruction_errors'])
            self.assertEqual(data['reconstruction_errors'], [])
            method = next(c for c in data['aggregates'] if c['method'] == 'run_free')
            self.assertEqual(method['tokens'], 120)
            self.assertEqual(method['resolved_rate'], 0)
            self.assertAlmostEqual(method['tokens_savings'], 1-120/220)
            self.assertTrue(all(c['apparent_saving_with_failure'] for c in data['diagnostics']['cases']))
            original = next(c for c in data['diagnostics']['original_policy'] if c['configuration_id'] == method['id'] and c['metric'] == 'total')
            self.assertEqual(original['baseline_original'], 220)
            self.assertEqual(original['original_denominator'], 2)
            self.assertEqual(original['baseline_original_mean'], 110)
            self.assertFalse((root/'combinations'/rows[0]['id']/'run'/'accounting.json').exists())
            self.assertTrue((out/'requests.csv').exists())
            self.assertTrue((out/'content-structure.csv').exists())
            import csv
            with (out/'cases.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 4)
            self.assertTrue((out/'traceable-cases.csv').exists())
            self.assertEqual(len((out/'execution-order.csv').read_text().splitlines()), 5)
            self.assertEqual(data['diagnostics']['preparation']['status'], 'unknown')

    def test_foundation_mismatch_excludes_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            study, rows = fixture(root)
            manifest = json.loads((root/'pilot.json').read_text())
            for row in rows:
                if row['method'] != 'run_free':
                    continue
                path = root/'combinations'/row['id']/'run/config.json'
                config = json.loads(path.read_text())
                config['agent']['options']['timeout'] = 1
                write_json(path, config)
                manifest['profiles'][row['profile_id']]['agent']['options']['timeout'] = 1
            write_json(root/'pilot.json', manifest)
            out = Path(tmp)/'report'
            out.mkdir()
            build_report(study, rows, root, out, figures=False)
            data = json.loads((out/'research.json').read_text())
            method = next(c for c in data['aggregates'] if c['method'] == 'run_free')
            self.assertEqual(method['tokens'], 120)
            self.assertIsNone(method['tokens_savings'])
            self.assertTrue(all(p['savings'] is None for p in data['diagnostics']['original_policy']))

    def test_preparation_cost_requires_raw_evidence_and_scope(self):
        from src.preparation_accounting import preparation_report
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            study, rows = fixture(root)
            study['configurations'].append(dict(id='eet', method='eet', selected=2))
            source = root/'combinations'/rows[0]['id']/'run/tasks'/rows[0]['case']/'attempt-0001/artifacts/call-fixed/api-records'
            shutil.copytree(source, root/'preparation/raw')
            path = root/'preparation/experience.json'
            ledger = dict(version=1, kind='historical_experience_preparation', configuration_ids=['eet'],
                          records=[dict(id='library-call-1', api_records='raw')])
            write_json(path, ledger)
            report = preparation_report(root, study)
            self.assertEqual(report['status'], 'measured_evidence')
            self.assertEqual(report['api_usage']['metrics']['total']['sum'], 110)
            self.assertIsNotNone(report['amortized_cost_usd'])
            ledger['records'].append(dict(id='duplicate-charge', api_records='raw'))
            write_json(path, ledger)
            self.assertEqual(preparation_report(root, study)['status'], 'invalid_evidence')

    def test_missing_task_not_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            study, rows = fixture(root)
            (root/'combinations'/rows[-1]['id']/'run/state.json').unlink()
            out = Path(tmp)/'report'
            out.mkdir()
            build_report(study, rows, root, out, figures=False)
            data = json.loads((out/'research.json').read_text())
            method = next(c for c in data['aggregates'] if c['method'] == 'run_free')
            self.assertEqual(method['selected'], 2)
            self.assertIsNone(method['tokens'])
            self.assertEqual(method['tokens_known_subtotal'], 60)

    def test_wrong_profile_cannot_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            study, rows = fixture(root)
            p = root/'combinations'/rows[-1]['id']/'run/config.json'
            config = json.loads(p.read_text())
            config['agent']['options']['timeout'] = 1
            write_json(p, config)
            out = Path(tmp)/'report'
            out.mkdir()
            build_report(study, rows, root, out, figures=False)
            data = json.loads((out/'research.json').read_text())
            self.assertEqual(len(data['reconstruction_errors']), 1)
            self.assertTrue(all(r['rank'] is None for r in data['rankings'] if r['configuration_id'] == rows[-1]['configuration_id']))


if __name__ == '__main__':
    unittest.main()
