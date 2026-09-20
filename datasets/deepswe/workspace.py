"""Prepared offline Docker environments; never pull images or build task code."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from shlex import quote
import subprocess
import os
from tempfile import TemporaryDirectory
from uuid import uuid4

from src.interfaces import ArtifactDirectory
from src.workspaces import DockerWorkspace


@dataclass
class DeepSWEWorkspace(DockerWorkspace):
    collect_command: str = ""
    collect_timeout: float = 300
    agent_timeout: float = 5400
    channel_directory: str = ""
    channel_python: str = ""
    submission_instructions: str = ""
    patch_rule: str = "deepswe-committed-v1"
    patch_base_commit: str = ""

    def expose_model_endpoint(self, endpoint, artifacts):
        from src.model_channel import isolated_endpoint
        return isolated_endpoint(self, endpoint, artifacts)

    def patch_capture_command(self, artifacts):
        target = PurePosixPath(artifacts.execution)
        # Run the original command verbatim, then retain the original bytes per call.
        script = "\n".join([
            "set -e", self.collect_command,
            f"cp /logs/artifacts/model.patch {quote(str(target / 'model.patch'))}",
            f"cp {quote(str(target / 'model.patch'))} {quote(str(target / 'patch.diff'))}",
            f"git diff --binary HEAD > {quote(str(target / 'uncommitted.diff'))}",
            f"git ls-files --others --exclude-standard -z > {quote(str(target / 'untracked-files.nul'))}",
            f"printf '%s\\n' captured > {quote(str(target / 'patch-capture.status'))}",
        ])
        timed = ("import json,subprocess,sys,time\n"
                 "started=time.monotonic(); status='failed'\n"
                 "try:\n"
                 " p=subprocess.run(['bash','-c',sys.argv[1]]); status='completed' if p.returncode==0 else 'failed'\n"
                 "finally:\n"
                 " with open(sys.argv[2],'a') as f: f.write(json.dumps(dict(version=1,event='end',phase='patch_capture',utc_seconds=time.time(),seconds=time.monotonic()-started,status=status))+'\\n')\n"
                 "sys.exit(p.returncode)\n")
        return ["timeout", "--signal=KILL", str(self.collect_timeout), self.channel_python,
                '-c', timed, script, str(target / 'timing.jsonl')]


def resource_options(config, *, relaxed_storage=False):
    flags = []
    for key, flag, suffix in (("cpus", "--cpus", ""), ("memory_mb", "--memory", "m"),
                               ("storage_mb", "--storage-opt", "")):
        value = config.get(key)
        if type(value) not in (int, float) or not 0 < value < float("inf"):
            raise ValueError(f"DeepSWE environment requires positive {key}")
        text = f"size={value}M" if key == "storage_mb" else f"{value}{suffix}"
        if key != 'storage_mb' or not relaxed_storage:
            flags += [flag, text]
    if config.get("gpus", 0) != 0:
        raise ValueError("GPU task environments are not supported")
    return flags


@contextmanager
def container(image, directory, resources, mounts, *, environment=None, retention=False,
              relaxed_storage=False, environment_names=()):
    """Each mount is (existing host path, container path, read_only)."""
    directory = Path(directory).resolve()
    name = f"tokenana-deepswe-{uuid4().hex}"

    def docker(argv, *, check=True):
        result = subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=120)
        with (directory / "container.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"operation": argv[0], "container": name,
                                     "returncode": result.returncode,
                                     "stdout": result.stdout, "stderr": result.stderr}) + "\n")
        if check:
            result.check_returncode()
        return result

    command = ["create", "--pull", "never", "--name", name, "--network", "none",
               *resource_options(resources, relaxed_storage=relaxed_storage), "--workdir", "/app", "--entrypoint", "/bin/bash"]
    for source, target, readonly in mounts:
        source = Path(source).resolve()
        if not source.exists() or "," in str(source) or "," in target:
            raise ValueError("bind mount requires an existing path without commas")
        channel_root = os.environ.get('TOKENANA_CHANNEL_ROOT', '')
        volume = os.environ.get('TOKENANA_CHANNEL_VOLUME', '')
        if channel_root and volume and source.is_relative_to(Path(channel_root)):
            command += ['--mount', f'type=volume,src={volume},dst={target},volume-subpath={source.relative_to(channel_root)}'
                        + (',readonly' if readonly else '')]
        else:
            command += ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]
    for key, value in (environment or {}).items():
        command += ["--env", f"{key}={value}"]
    for key in environment_names:
        command += ['--env', key]
    command += [image, "-c", "sleep infinity"]
    active_error = False
    try:
        from src.telemetry import span
        with span(directory, 'environment_prepare'):
            docker(command)
            docker(["start", name])
        yield name
    except BaseException:
        active_error = True
        raise
    finally:
        # A failed archive deliberately keeps its stopped container for recovery.
        try:
            if retention:
                from src.retention import archive_container
                archive_container(name, directory)
            from src.telemetry import span
            with span(directory, 'cleanup'):
                docker(["rm", "--force", name], check=not active_error)
            if retention:
                path = directory / 'retention.json'
                value = json.loads(path.read_text())
                value['retained'] = False
                from src.records import write_json
                write_json(path, value)
        except BaseException:
            if not active_error:
                raise


def check_repository(workspace, base):
    head = workspace.execute(["git", "rev-parse", "HEAD"], timeout=60)
    head.check_returncode()
    resolved = workspace.execute(["git", "rev-parse", "--verify", f"{base}^{{commit}}"], timeout=60)
    resolved.check_returncode()
    clean = workspace.execute(["git", "status", "--porcelain"], timeout=60)
    clean.check_returncode()
    if head.stdout.strip() != resolved.stdout.strip() or clean.stdout.strip():
        raise ValueError("DeepSWE image must contain a clean /app at the task base commit")


@contextmanager
def prepare(record, directory, runtime):
    directory = directory.resolve()
    artifacts = directory / "artifacts"
    artifacts.mkdir()
    # Short socket paths avoid AF_UNIX limits on deeply nested run directories.
    with TemporaryDirectory(prefix="tc-", dir=os.environ.get('TOKENANA_CHANNEL_ROOT', '/tmp')) as channel_directory:
        Path(channel_directory).chmod(0o711)
        with _prepare(record, directory, runtime, artifacts, channel_directory) as workspace:
            yield workspace


@contextmanager
def _prepare(record, directory, runtime, artifacts, channel_directory):
    from src import byte_relay

    config = record.config
    hook = config["verifier"]["collect"][0]
    extra_mounts = []
    if runtime.get('opencode_config_root'):
        root = directory / 'opencode-config'
        (root / 'opencode').mkdir(parents=True)
        (root / 'opencode/.gitignore').touch()
        extra_mounts.append((root, runtime['opencode_config_root'], True))
    with container(runtime["images"][record.task.instance_id], directory, config["environment"],
                   [(artifacts, "/workspace/output", False),
                    (Path(channel_directory), "/tokenana/model-channel", True),
                    (Path(byte_relay.__file__), "/tokenana/byte_relay.py", True), *extra_mounts],
                   environment={**config["environment"].get("env", {}), **runtime.get("environment", {})},
                   environment_names=runtime.get('environment_names', []),
                   retention=runtime.get('retain_files', False),
                   relaxed_storage=runtime.get('relaxed_storage', False)) as name:
        workspace = DeepSWEWorkspace(name, "/app", ArtifactDirectory(artifacts, "/workspace/output"),
                                     tuple(runtime.get("command_prefix", [])),
                                     hook["command"], hook["timeout_sec"], config["agent"]["timeout_sec"],
                                     channel_directory, runtime["model_channel"]["python"],
                                     "## DeepSWE delivery instructions\n"
                                     "Apply real source edits and follow the original task's branch and commit requirements.\n"
                                     f"The evaluator collects `git diff --binary {record.task.base_commit} HEAD`.\n"
                                     "You must commit your changes before finishing. After committing, plain `git diff` "
                                     "being empty is normal and is not a failure. This committed patch rule supersedes "
                                     "any earlier requirement for a nonempty plain git diff. Uncommitted changes are "
                                     "diagnostic only; the framework will not commit them for you.\n"
                                     "Git permission does not override any execution restrictions imposed by the selected method.",
                                     patch_base_commit=record.task.base_commit)
        check_repository(workspace, record.task.base_commit)
        if runtime.get('retain_files'):
            from src.retention import Snapshots
            workspace.snapshot = Snapshots(name, artifacts)
        # Task images must not have verifier or reference-solution material baked in.
        workspace.execute(["bash", "-c", "test ! -e /tests && test ! -e /solution"], timeout=60).check_returncode()
        yield workspace
