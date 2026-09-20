"""Own one task container using a prebuilt, explicitly selected agent image."""

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from src.interfaces import ArtifactDirectory, Task
from src.workspaces import DockerWorkspace


@contextmanager
def prepare(task: Task, directory: Path, runtime: dict):
    image = runtime["images"][task.instance_id]
    network = runtime["network"]
    directory = directory.resolve()
    artifacts = directory / "artifacts"
    artifacts.mkdir()
    name = f"tokenana-{uuid4().hex}"
    workspace = DockerWorkspace(name, "/testbed", ArtifactDirectory(artifacts, "/workspace/output"),
                                tuple(runtime["command_prefix"]))

    def docker(argv):
        result = subprocess.run(["docker", *argv], capture_output=True, text=True)
        with (directory / "container.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"operation": argv[0], "container": name,
                                  "returncode": result.returncode,
                                  "stdout": result.stdout, "stderr": result.stderr}) + "\n")
        result.check_returncode()
        return result

    command = ["create", "--pull", "never", "--name", name, "--network", network,
               "--mount", f"type=bind,src={artifacts},dst=/workspace/output",
               "--entrypoint", "/bin/bash"]
    for key, value in runtime.get("environment", {}).items():
        command.extend(["--env", f"{key}={value}"])
    command.extend([image, "-c", "sleep infinity"])
    docker(command)
    try:
        docker(["start", name])
        head = workspace.execute(["git", "rev-parse", "HEAD"])
        head.check_returncode()
        if head.stdout.strip() != task.base_commit:
            raise ValueError(f"{task.instance_id}: image HEAD differs from task base_commit")
        clean = workspace.execute(["git", "status", "--porcelain"])
        clean.check_returncode()
        if clean.stdout.strip():
            raise ValueError(f"{task.instance_id}: image repository is not clean")
        yield workspace
    finally:
        # Removal stops remaining exec processes before artifacts are collected.
        docker(["rm", "--force", name])
