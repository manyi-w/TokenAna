"""Offline retention acceptance; four isolated processes, no Docker/model calls.

Run after activating tokenAna: python -B scripts/validate_retention.py --jobs 4
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack, contextmanager
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import retention
from src.containers import container


def tar_bytes(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, value in entries.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            if value is None:
                member.type = tarfile.DIRTYPE
            elif isinstance(value, tuple):
                member.type, member.linkname = value
            else:
                member.size = len(value)
            archive.addfile(member, io.BytesIO(value) if isinstance(value, bytes) else None)
    return stream.getvalue()


class StreamProcess:
    def __init__(self, data, code=0):
        self.stdout = io.BytesIO(data)
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stdout.close()

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


class Docker:
    """Simulate only the Docker boundary; real tar readers/writers are exercised."""
    def __init__(self, changes='', trees=None, mounts=(), broken=False, cp_code=0):
        self.changes, self.trees, self.mounts = changes, trees or {}, mounts
        self.broken, self.cp_code, self.commands = broken, cp_code, []

    def run(self, command, **kwargs):
        self.commands.append(command)
        if command[:2] == ['docker', 'export']:
            kwargs['stdout'].write(tar_bytes({'app/full': b'full'}))
        return subprocess.CompletedProcess(command, 0, '', '')

    def output(self, command, **kwargs):
        self.commands.append(command)
        if command[1] == 'inspect':
            return json.dumps([{'Image': 'sha256:' + 'a' * 64, 'Mounts': list(self.mounts)}]).encode()
        if command[1] == 'diff':
            return self.changes
        raise AssertionError(command)

    def popen(self, command, **kwargs):
        self.commands.append(command)
        assert command[:2] == ['docker', 'cp'], command
        root = command[2].split(':', 1)[1]
        return StreamProcess(b'invalid tar' if self.broken else tar_bytes(self.trees[root]), self.cp_code)

    @contextmanager
    def installed(self):
        with ExitStack() as stack:
            for name, replacement in (('run', self.run), ('check_output', self.output), ('Popen', self.popen)):
                stack.enter_context(patch('subprocess.' + name, replacement))
            yield self


class RetentionTests(unittest.TestCase):
    def test_new_runtime_freezes_mode_and_resume_preserves_legacy(self):
        from src import execution
        from src.interfaces import Task
        seen = []

        @contextmanager
        def prepare(task, directory, runtime):
            seen.append(retention.retention_mode(runtime))
            raise RuntimeError('fixture stops before agent execution')
            yield  # Declare a context manager without executing an agent.

        method = SimpleNamespace(version='test-v1')
        agent = SimpleNamespace(adapter=SimpleNamespace(plan=lambda options: {}),
                                effective_options=lambda: {}, method_version='test-v1')
        dataset = SimpleNamespace(tasks=lambda: [Task('case', 'repo', 'base', 'task')],
                                  validate_runtime=lambda tasks, runtime: None, prepare=prepare)
        config = SimpleNamespace(method=SimpleNamespace(options={}), agent=SimpleNamespace(options={}),
                                 model=None, dataset=SimpleNamespace(options={}))
        with TemporaryDirectory() as temp, ExitStack() as stack:
            stack.enter_context(patch.object(execution, 'load_adapter',
                side_effect=lambda component: method if component is config.method else
                                              dataset if component is config.dataset else agent.adapter))
            stack.enter_context(patch.object(execution, 'BoundAgent', return_value=agent))
            stack.enter_context(patch.object(execution, 'validate_method'))
            stack.enter_context(patch.object(execution, 'accounting_plan', return_value={'errors': []}))
            stack.enter_context(patch.object(execution, 'config_dict', return_value={'configuration': 'fixture'}))
            stack.enter_context(patch.object(execution, 'save_accounting', return_value={}))
            directory = Path(temp) / 'run'
            runtime = {'retain_files': True}
            with self.assertRaisesRegex(RuntimeError, 'fixture stops'):
                execution.run_experiment(config, runtime, directory)
            saved = directory / 'runtime.json'
            self.assertEqual(json.loads(saved.read_text())['retention_mode'], 'research')
            for legacy in (False, True):
                if legacy:
                    saved.write_text(json.dumps(runtime))
                before = saved.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'fixture stops'):
                    execution.run_experiment(config, runtime, directory, resume=True)
                self.assertEqual(saved.read_bytes(), before)
            self.assertEqual(seen, ['research', 'research', 'full'])

    def test_request_snapshots_only_in_full_mode(self):
        from datasets.deepswe import workspace as deep
        from datasets.swe_bench_verified import workspace as verified
        from src.interfaces import Task

        @contextmanager
        def prepared(*args, **kwargs):
            yield 'simulated-task'

        task = Task('case', 'org/repo', 'base', 'task')
        record = SimpleNamespace(task=task, config={
            'environment': {}, 'verifier': {'collect': [{'command': 'true', 'timeout_sec': 10}]},
            'agent': {'timeout_sec': 10}})
        for mode in ('research', 'full'):
            runtime = {'images': {'case': 'base-image'}, 'retain_files': True,
                       'retention_mode': mode, 'model_channel': {'python': '/opt/python'}}
            with TemporaryDirectory() as temp, patch.object(deep, 'container', prepared), \
                    patch.object(deep, 'check_repository'), \
                    patch.object(deep.DeepSWEWorkspace, 'execute', return_value=subprocess.CompletedProcess([], 0, '', '')):
                directory = Path(temp)
                artifacts = directory / 'artifacts'
                artifacts.mkdir()
                with deep._prepare(record, directory, runtime, artifacts, temp) as workspace:
                    self.assertEqual(callable(getattr(workspace, 'snapshot', None)), mode == 'full')
            with TemporaryDirectory() as temp, patch.object(verified, 'container', prepared), \
                    patch.object(verified, 'check_repository'):
                with verified.prepare_isolated(task, Path(temp), runtime) as workspace:
                    self.assertEqual(callable(getattr(workspace, 'snapshot', None)), mode == 'full')

    def test_delta_round_trip(self):
        # Includes committed Git metadata, binary edits, new/ignored files, rename,
        # deletion and a symlink. An unchanged large dependency must not be stored.
        trees = {'/app': {'app': None, 'app/code.py': b'new code\n',
                 'app/dependency.bin': b'x' * 2_000_000, 'app/data.bin': b'\x00\xff\x03',
                 'app/new name.py': b'new file', 'app/.git/index': b'changed index',
                 'app/.git/objects/new': b'committed object', 'app/.ignored': b'ignored output',
                 'app/link': (tarfile.SYMTYPE, 'code.py')}}
        changes = '\n'.join(['C /app', 'C /app/code.py', 'C /app/data.bin',
                    'A /app/new name.py', 'D /app/old name.py', 'C /app/.git/index',
                    'A /app/.git/objects/new', 'A /app/.ignored', 'A /app/link', 'C /usr/lib/python'])
        docker = Docker(changes, trees)
        with TemporaryDirectory() as temp, docker.installed():
            directory = Path(temp)
            record = retention.archive_container('task', directory)
            archive = directory / record['archive']
            self.assertLess(archive.stat().st_size, 2000)
            self.assertEqual(record['deleted'], ['/app/old name.py'])
            self.assertNotIn('/usr/lib/python', record['changes'])
            base = directory / 'restore'
            (base / 'app').mkdir(parents=True)
            (base / 'app/old name.py').write_text('old name')
            (base / 'app/dependency.bin').write_bytes(b'x' * 2_000_000)
            for deleted in record['deleted']:
                (base / deleted.lstrip('/')).unlink()
            with tarfile.open(archive) as saved:
                self.assertNotIn('app/dependency.bin', saved.getnames())
                saved.extractall(base, filter='data')
            for path, value in trees['/app'].items():
                if isinstance(value, bytes):
                    self.assertEqual((base / path).read_bytes(), value)
            self.assertTrue((base / 'app/link').is_symlink())
            self.assertFalse((base / 'app/old name.py').exists())
            self.assertTrue(record['complete'])
            self.assertFalse(any(c[1] == 'export' for c in docker.commands))

    def test_persisted_logs_are_not_duplicated(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'logs').mkdir()
            log = directory / 'logs/report.json'
            log.write_text('{"resolved": true}')
            docker = Docker('C /logs\nA /logs/report.json', mounts=[
                {'Destination': '/logs', 'Type': 'bind', 'RW': True, 'Source': str(directory / 'logs')}])
            with docker.installed():
                record = retention.archive_container('eval', directory)
            self.assertEqual(record['mounts'][0]['result_path'], 'logs')
            self.assertFalse(any(c[1] == 'cp' for c in docker.commands))
            self.assertEqual(log.read_text(), '{"resolved": true}')

    def test_external_mount_is_compressed(self):
        docker = Docker(trees={'/extra': {'extra': None, 'extra/output': b'evidence'}}, mounts=[
            {'Destination': '/extra', 'Type': 'volume', 'RW': True}])
        with TemporaryDirectory() as temp, docker.installed():
            record = retention.archive_container('task', Path(temp))
            with tarfile.open(Path(temp) / record['mounts'][0]['archive']) as saved:
                self.assertEqual(saved.extractfile('extra/output').read(), b'evidence')

    def test_added_parent_does_not_duplicate_mount(self):
        docker = Docker('A /app\nA /app/new\nA /app/cache/file',
            {'/app': {'app': None, 'app/new': b'new', 'app/cache/file': b'mounted'}},
            mounts=[{'Destination': '/app/cache', 'Type': 'bind', 'RW': True, 'Source': '/unused'}])
        with TemporaryDirectory() as temp, docker.installed():
            record = retention.archive_container('task', Path(temp), persisted_mounts={'/app/cache': 'cache'})
            with tarfile.open(Path(temp) / record['archive']) as saved:
                self.assertNotIn('app/cache/file', saved.getnames())

    def test_empty_delta_is_valid(self):
        with TemporaryDirectory() as temp, Docker().installed():
            record = retention.archive_container('task', Path(temp))
            self.assertTrue(record['complete'])
            self.assertEqual(record['archived_entries'], 0)

    def test_missing_entry_blocks_cleanup(self):
        docker = Docker('C /app/missing', {'/app': {'app': None}})
        with TemporaryDirectory() as temp, docker.installed():
            directory = Path(temp)
            with self.assertRaises(ValueError):
                with container('base', directory, None, [], retention=True):
                    pass
            self.assertFalse(any(c[1] == 'rm' for c in docker.commands))
            record = json.loads((directory / 'retention.json').read_text())
            self.assertFalse(record['complete'])
            self.assertTrue(record['retained'])

    def test_corrupt_archive_blocks_cleanup(self):
        docker = Docker('C /app/file', broken=True)
        with TemporaryDirectory() as temp, docker.installed():
            directory = Path(temp)
            with self.assertRaises(tarfile.ReadError):
                with container('base', directory, None, [], retention=True):
                    pass
            self.assertFalse(any(c[1] == 'rm' for c in docker.commands))
            self.assertFalse(json.loads((directory / 'retention.json').read_text())['complete'])

    def test_failed_cp_blocks_cleanup(self):
        docker = Docker('C /app/file', {'/app': {'app/file': b'partial'}}, cp_code=1)
        with TemporaryDirectory() as temp, docker.installed():
            with self.assertRaises(RuntimeError):
                with container('base', Path(temp), None, [], retention=True):
                    pass
            self.assertFalse(any(c[1] == 'rm' for c in docker.commands))

    def test_success_cleanup_follows_archive(self):
        docker = Docker()
        with TemporaryDirectory() as temp, docker.installed():
            with container('base', Path(temp), None, [], retention=True):
                pass
            record = json.loads((Path(temp) / 'retention.json').read_text())
            self.assertTrue(record['complete'])
            self.assertFalse(record['retained'])
            self.assertLess(next(i for i, c in enumerate(docker.commands) if c[1] == 'stop'),
                            next(i for i, c in enumerate(docker.commands) if c[1] == 'rm'))

    def test_full_mode_remains_explicit(self):
        docker = Docker()
        with TemporaryDirectory() as temp, docker.installed():
            record = retention.archive_container('task', Path(temp), mode='full')
            self.assertEqual(record['mode'], 'full')
            self.assertTrue((Path(temp) / 'container-filesystem.tar').exists())
            self.assertFalse((Path(temp) / 'workspace-delta.tar.gz').exists())

    def test_verified_workspace_root(self):
        docker = Docker('A /testbed/new.py', {'/testbed': {'testbed/new.py': b'new'}})
        with TemporaryDirectory() as temp, docker.installed():
            record = retention.archive_container('task', Path(temp), root='/testbed')
            self.assertEqual(record['scope'], ['/testbed', '/logs'])
            with tarfile.open(Path(temp) / record['archive']) as saved:
                self.assertEqual(saved.extractfile('testbed/new.py').read(), b'new')

    def test_verified_legacy_network_and_cleanup(self):
        from datasets.swe_bench_verified.workspace import prepare
        from src.interfaces import Task
        task = Task('case', 'org/repo', 'base', 'task')
        runtime = {'images': {'case': 'image'}, 'network': 'fixture-network',
                   'command_prefix': ['/prepared/python'], 'retain_files': True,
                   'retention_mode': 'research'}
        docker = Docker()
        with TemporaryDirectory() as temp, docker.installed(), \
                patch('src.workspaces.DockerWorkspace.execute', side_effect=[
                    subprocess.CompletedProcess([], 0, 'base\n'), subprocess.CompletedProcess([], 0, '')]):
            with prepare(task, Path(temp), runtime) as workspace:
                self.assertEqual(workspace.root, '/testbed')
                self.assertEqual(workspace.command_prefix, ('/prepared/python',))
            command = next(c for c in docker.commands if c[1] == 'create')
            self.assertEqual(command[command.index('--network')+1], 'fixture-network')
            record = json.loads((Path(temp)/'retention.json').read_text())
            self.assertTrue(record['complete'])
            self.assertFalse(record['retained'])

    def test_verified_failed_archive_keeps_container(self):
        from datasets.swe_bench_verified.workspace import prepare
        from src.interfaces import Task
        docker = Docker()
        runtime = {'images': {'case': 'image'}, 'network': 'none', 'command_prefix': [],
                   'retain_files': True, 'retention_mode': 'research'}
        with TemporaryDirectory() as temp, docker.installed(), \
                patch('src.workspaces.DockerWorkspace.execute', side_effect=[
                    subprocess.CompletedProcess([], 0, 'base\n'), subprocess.CompletedProcess([], 0, '')]), \
                patch('src.retention.archive_container', side_effect=ValueError('broken archive')):
            with self.assertRaisesRegex(ValueError, 'broken archive'):
                with prepare(Task('case', 'org/repo', 'base', 'task'), Path(temp), runtime):
                    pass
            self.assertFalse(any(c[1] == 'rm' for c in docker.commands))

    def test_modes_and_legacy(self):
        self.assertEqual(retention.retention_mode({}), 'full')
        self.assertEqual(retention.retention_mode({'retention_mode': 'research'}), 'research')
        with self.assertRaises(ValueError):
            retention.retention_mode({'retention_mode': 'typo'})


def run_case(name):
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(RetentionTests(name))
    return result.wasSuccessful(), output.getvalue()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    names = unittest.defaultTestLoader.getTestCaseNames(RetentionTests)
    with ProcessPoolExecutor(max_workers=args.jobs) as workers:
        results = list(workers.map(run_case, names))
    for passed, output in results:
        if not passed:
            print(output)
    print(f'{sum(passed for passed, _ in results)}/{len(results)} passed; jobs={args.jobs}; mocked Docker only')
    sys.exit(0 if all(passed for passed, _ in results) else 1)
