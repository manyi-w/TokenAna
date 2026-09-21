"""Shared Docker lifecycle and repository checks for prepared task environments."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4


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
    if config.get('gpus', 0):
        flags += ['--gpus', str(config['gpus'])]
    return flags


@contextmanager
def container(image, directory, resources, mounts, *, environment=None, retention=False,
              relaxed_storage=False, environment_names=(), root='/app', retention_mode='research',
              network='none', name_prefix='tokenana-deepswe'):
    """Each mount is (existing host path, container path, read_only)."""
    directory = Path(directory).resolve()
    from src.retention import retention_mode as validate_retention_mode
    validate_retention_mode({'retention_mode': retention_mode})
    name = f"{name_prefix}-{uuid4().hex}"

    def docker(argv, *, check=True):
        result = subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=120)
        with (directory / "container.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"operation": argv[0], "container": name,
                                     "returncode": result.returncode,
                                     "stdout": result.stdout, "stderr": result.stderr}) + "\n")
        if check:
            result.check_returncode()
        return result

    command = ["create", "--pull", "never", "--name", name, "--network", network,
               *(resource_options(resources, relaxed_storage=relaxed_storage) if resources else []),
               "--workdir", root, "--entrypoint", "/bin/bash"]
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
                archive_container(name, directory, mode=retention_mode, root=root,
                    persisted_mounts={target: str(Path(source).resolve().relative_to(directory))
                                      for source, target, _ in mounts
                                      if Path(source).resolve().is_relative_to(directory)})
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
