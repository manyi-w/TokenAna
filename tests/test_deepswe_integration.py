"""Dataset boundary simulation; no Docker, verifier, or repository commands run."""
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from datasets.deepswe.tasks import select_tasks
from datasets.deepswe.workspace import DeepSWEWorkspace, check_repository
from datasets.deepswe.adapter import DeepSWE
from methods.run_free.adapter import RunFree
from methods.run_free_multilingual.adapter import RunFreeMultilingual
from src.interfaces import ArtifactDirectory
from src.patches import capture_patch, patch_capture_command


class DeepSWEIntegrationTests(unittest.TestCase):
    def test_all_tasks_and_language_variants_preserve_metadata(self):
        all_tasks = select_tasks()
        python = select_tasks(languages=['python'])
        multilingual = select_tasks(languages=['go', 'typescript', 'javascript', 'rust'])
        self.assertEqual((len(all_tasks), len(python), len(multilingual)), (113, 34, 79))
        self.assertEqual({r.task.instance_id for r in all_tasks}, {r.task.instance_id for r in python + multilingual})
        self.assertTrue(any(len(r.task.base_commit) < 40 for r in all_tasks))
        dataset = DeepSWE()
        dataset.tasks()
        dataset.validate_selection(RunFree(), [r.task for r in python])
        dataset.validate_selection(RunFreeMultilingual(), [r.task for r in multilingual])
        with self.assertRaises(ValueError):
            dataset.validate_selection(RunFree(), [r.task for r in all_tasks])

    def test_repository_short_commit_resolves_before_comparison(self):
        workspace = SimpleNamespace(execute=Mock(side_effect=[
            subprocess.CompletedProcess([], 0, 'a' * 40 + '\n'),
            subprocess.CompletedProcess([], 0, 'a' * 40 + '\n'),
            subprocess.CompletedProcess([], 0, '')]))
        check_repository(workspace, 'aaaaaaa')
        self.assertEqual(workspace.execute.call_args_list[1].args[0], ['git', 'rev-parse', '--verify', 'aaaaaaa^{commit}'])
        workspace.execute.side_effect = [subprocess.CompletedProcess([], 0, 'a' * 40),
            subprocess.CompletedProcess([], 0, 'b' * 40), subprocess.CompletedProcess([], 0, '')]
        with self.assertRaises(ValueError):
            check_repository(workspace, 'bbbbbbb')

    def test_committed_patch_bytes_empty_patch_and_capture_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts = ArtifactDirectory(Path(temp), '/output/call')
            record = select_tasks(limit=1)[0]
            workspace = DeepSWEWorkspace('fixture', '/app', artifacts, collect_command=record.config['verifier']['collect'][0]['command'])
            workspace.execute = Mock(return_value=subprocess.CompletedProcess([], 0, ''))
            command = patch_capture_command(workspace, artifacts)[-2]
            self.assertIn(f'git diff --binary {record.task.base_commit} HEAD', command)
            self.assertIn('uncommitted.diff', command)
            (Path(temp) / 'patch.diff').write_bytes(b'diff\r\ncommitted\r\n')
            self.assertEqual(capture_patch(workspace, artifacts), 'diff\r\ncommitted\r\n')
            (Path(temp) / 'patch.diff').write_bytes(b'')
            self.assertEqual(capture_patch(workspace, artifacts), '')
            workspace.execute.return_value = subprocess.CompletedProcess([], 1, '', 'collect failed')
            with self.assertRaises(subprocess.CalledProcessError):
                capture_patch(workspace, artifacts)
            verified = SimpleNamespace(execute=Mock(return_value=subprocess.CompletedProcess([], 0, 'working-tree diff')))
            self.assertEqual(capture_patch(verified, artifacts), 'working-tree diff')
            self.assertEqual(verified.execute.call_args.args[0], ['git', 'diff'])
