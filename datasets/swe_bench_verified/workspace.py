"""Own one task container using a prebuilt, explicitly selected agent image."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.interfaces import ArtifactDirectory, Task
from src.workspaces import DockerWorkspace
from src.containers import container, check_repository
from src.retention import retention_mode


@dataclass
class VerifiedWorkspace(DockerWorkspace):
    channel_directory: str = ''
    channel_python: str = ''

    def expose_model_endpoint(self, endpoint, artifacts):
        from src.model_channel import isolated_endpoint
        return isolated_endpoint(self, endpoint, artifacts)


@contextmanager
def prepare(task: Task, directory: Path, runtime: dict):
    mode = retention_mode(runtime)
    if runtime.get('model_channel'):
        with prepare_isolated(task, directory, runtime) as workspace:
            yield workspace
        return
    image = runtime["images"][task.instance_id]
    network = runtime["network"]
    directory = directory.resolve()
    artifacts = directory / "artifacts"
    artifacts.mkdir()
    with container(image, directory, None, [(artifacts, '/workspace/output', False)],
                   environment=runtime.get('environment', {}), network=network,
                   name_prefix='tokenana', root='/testbed',
                   retention=runtime.get('retain_files', False), retention_mode=mode) as name:
        workspace = DockerWorkspace(name, '/testbed', ArtifactDirectory(artifacts, '/workspace/output'),
                                    tuple(runtime['command_prefix']))
        head = workspace.execute(["git", "rev-parse", "HEAD"])
        head.check_returncode()
        if head.stdout.strip() != task.base_commit:
            raise ValueError(f"{task.instance_id}: image HEAD differs from task base_commit")
        clean = workspace.execute(["git", "status", "--porcelain"])
        clean.check_returncode()
        if clean.stdout.strip():
            raise ValueError(f"{task.instance_id}: image repository is not clean")
        if runtime.get('retain_files') and mode == 'full':
            from src.retention import Snapshots
            workspace.snapshot = Snapshots(name, artifacts)
        yield workspace


@contextmanager
def prepare_isolated(task, directory, runtime):
    import os
    from tempfile import TemporaryDirectory
    from src import byte_relay
    from src.retention import Snapshots
    directory = directory.resolve()
    artifacts = directory / 'artifacts'
    artifacts.mkdir()
    with TemporaryDirectory(prefix='tc-', dir=os.environ.get('TOKENANA_CHANNEL_ROOT', '/tmp')) as channel:
        Path(channel).chmod(0o711)
        mounts = [(artifacts, '/workspace/output', False), (Path(channel), '/tokenana/model-channel', True),
                  (Path(byte_relay.__file__), '/tokenana/byte_relay.py', True)]
        if runtime.get('opencode_config_root'):
            root = directory / 'opencode-config'
            (root / 'opencode').mkdir(parents=True)
            (root / 'opencode/.gitignore').touch()
            mounts.append((root, runtime['opencode_config_root'], True))
        resources = runtime.get('resources')
        with container(runtime['images'][task.instance_id], directory, resources, mounts,
                       environment_names=runtime.get('environment_names', []),
                       retention=runtime.get('retain_files', True),
                       retention_mode=retention_mode(runtime),
                       relaxed_storage=runtime.get('relaxed_storage', False), root='/testbed') as name:
            workspace = VerifiedWorkspace(name, '/testbed', ArtifactDirectory(artifacts, '/workspace/output'),
                tuple(runtime.get('command_prefix', [])), channel, runtime['model_channel']['python'])
            check_repository(workspace, task.base_commit)
            if runtime.get('retain_files', True) and retention_mode(runtime) == 'full':
                workspace.snapshot = Snapshots(name, artifacts)
            yield workspace
