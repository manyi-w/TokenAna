import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from src import pilot
from src.records import write_json
from src.retention import archive_container, verify_tar
from src.telemetry import span, totals
from datasets.deepswe.workspace import container, resource_options


class PilotTests(unittest.TestCase):
    def test_default_matrix_and_codex_filter(self):
        rows, skipped = pilot.matrix(pilot.METHODS, pilot.AGENTS, pilot.MODELS, [pilot.DEFAULT_CASE])
        self.assertEqual(len(rows), 52)
        self.assertEqual(len({r['id'] for r in rows}), 52)
        self.assertEqual(len(skipped), 12)
        self.assertTrue(all(r['model'] == pilot.MODELS[0] for r in rows if r['agent'] == 'codex'))

    def test_multiple_filters_and_twenty_cases(self):
        rows, _ = pilot.matrix(['eet', 'run_free'], ['mini', 'trae'], [pilot.MODELS[3]], ['a', 'b'])
        self.assertEqual(len(rows), 8)
        rows, _ = pilot.matrix(pilot.METHODS, pilot.AGENTS, pilot.MODELS, pilot.fixed_cases()[:20])
        self.assertEqual(len(rows), 1040)
        self.assertEqual(pilot.fixed_cases()[0], pilot.DEFAULT_CASE)

    def test_invalid_and_empty_selection(self):
        with self.assertRaises(ValueError):
            pilot.selection('agent-diett', pilot.METHODS, 'method')
        with self.assertRaises(ValueError):
            pilot.matrix(['eet'], ['codex'], [pilot.MODELS[2]], ['a'])
        with self.assertRaises(SystemExit), patch('sys.stderr', io.StringIO()):
            pilot.parser().parse_args(['--case', 'a', '--cases', '20'])

    def test_read_settings_redacts_literal_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'settings.toml').write_text('[models.a]\nname="gpt-5.6-sol"\napi_key_env="sk-secret-example"\n')
            with patch.object(pilot, 'LOCAL', directory):
                settings, credentials = pilot.read_settings()
            self.assertNotIn('sk-secret-example', json.dumps(settings))
            self.assertEqual(credentials['TOKENANA_PILOT_KEY_0'], 'sk-secret-example')

    def test_dry_run_writes_nothing_and_contacts_no_docker(self):
        settings = {'models': {}, 'images': {}, 'runtime_paths': {}, 'agent_diet_auxiliary': {}}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'run'
            with patch.object(pilot, 'read_settings', return_value=(settings, {})), \
                 patch.object(pilot, 'preflight', return_value=([], [])), \
                 patch('subprocess.run') as run, patch('subprocess.check_output') as check, \
                 patch('sys.stdout', io.StringIO()):
                self.assertEqual(pilot.main(['--dry-run', '--output', str(output)]), 0)
            self.assertFalse(output.exists())
            run.assert_not_called()
            check.assert_not_called()

    def test_relaxed_storage_does_not_change_cpu_or_ram(self):
        resources = {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480}
        strict = resource_options(resources)
        relaxed = resource_options(resources, relaxed_storage=True)
        self.assertIn('size=20480M', strict)
        self.assertEqual(relaxed, ['--cpus', '2', '--memory', '8192m'])

    def test_failed_archive_keeps_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch('datasets.deepswe.workspace.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as docker, \
                 patch('src.retention.archive_container', side_effect=RuntimeError('archive failed')):
                with self.assertRaises(RuntimeError):
                    with container('image', temporary, {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480}, [], retention=True):
                        pass
            self.assertFalse(any(c.args[0][1] == 'rm' for c in docker.call_args_list))

    def test_verified_archive_precedes_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            events = []
            def archive(name, directory):
                events.append('archive')
                write_json(Path(directory) / 'retention.json', {'complete': True, 'retained': True})
            def docker(argv, **kwargs):
                events.append(argv[1])
                return subprocess.CompletedProcess(argv, 0, '', '')
            with patch('datasets.deepswe.workspace.subprocess.run', side_effect=docker), \
                 patch('src.retention.archive_container', side_effect=archive):
                with container('image', temporary, {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480}, [], retention=True):
                    pass
            self.assertLess(events.index('archive'), events.index('rm'))
            self.assertFalse(json.loads((Path(temporary) / 'retention.json').read_text())['retained'])

    def test_tar_verification_reads_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fs.tar'
            with tarfile.open(path, 'w') as tar:
                item = tarfile.TarInfo('file')
                item.size = 4096
                tar.addfile(item, io.BytesIO(b'x' * 4096))
            verify_tar(path)
            path.write_bytes(path.read_bytes()[:700])
            with self.assertRaises(tarfile.ReadError):
                verify_tar(path)

    def test_patch_timer_keeps_exit_code_on_failure(self):
        from datasets.deepswe.workspace import DeepSWEWorkspace
        from src.interfaces import ArtifactDirectory
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = DeepSWEWorkspace('fixture', '/app', ArtifactDirectory(root, '/out'), channel_python=sys.executable)
            argv = workspace.patch_capture_command(workspace.artifacts)
            timed_code = argv[5]
            result = subprocess.run([sys.executable, '-c', timed_code, 'exit 7', str(root / 'timing.jsonl')])
            self.assertEqual(result.returncode, 7)
            self.assertEqual(json.loads((root / 'timing.jsonl').read_text())['status'], 'failed')

    def test_timing_includes_failed_spans(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError):
                with span(temporary, 'method_callback', case_id='a'):
                    raise RuntimeError('failed')
            lines = [json.loads(s) for s in (Path(temporary) / 'timing.jsonl').read_text().splitlines()]
            self.assertEqual([r['event'] for r in lines], ['start', 'end'])
            self.assertEqual(lines[-1]['status'], 'failed')
            self.assertGreaterEqual(totals(temporary)['method_callback'], 0)

    def test_resume_refuses_still_running_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'container.jsonl').write_text('{"container":"still-running"}\n')
            with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'true\n', '')):
                with self.assertRaisesRegex(ValueError, 'still running'):
                    pilot.ensure_stopped(output)

    def test_summary_keeps_unknown_separate_from_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows, _ = pilot.matrix(['eet'], ['mini'], [pilot.MODELS[0]], ['a'])
            records = pilot.summarize(root, rows)
            self.assertIsNone(records[0]['corrected'])
            self.assertIn('original_input', (root / 'summary.csv').read_text())


if __name__ == '__main__':
    unittest.main()
