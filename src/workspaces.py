"""Access an already prepared Docker container; no container lifecycle actions."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Sequence

from .interfaces import ArtifactDirectory


def execution_timeout(workspace, options):
    """A dataset may cap the call; legacy workspaces retain their configured limit."""
    limit = getattr(workspace, "agent_timeout", None)
    timeout = options.get("timeout", limit if limit is not None else 600)
    if limit is None:
        return timeout
    if type(timeout) not in (int, float) or not 0 < timeout < float("inf"):
        raise ValueError("agent timeout must be a finite positive number")
    return min(timeout, limit)


@dataclass
class DockerWorkspace:
    container: str
    root: str
    artifacts: ArtifactDirectory
    # For example: ("/opt/miniconda3/bin/conda", "run", "--no-capture-output",
    #               "-n", "testbed"). The dataset preparation layer supplies it.
    command_prefix: tuple[str, ...] = ()

    def launch_command(self, argv: Sequence[str]) -> list[str]:
        return ["docker", "exec", "-i", "--workdir", self.root, self.container,
                *self.command_prefix, *argv]

    def execute(
        self, argv: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(self.launch_command(argv), capture_output=True,
                              text=True, encoding="utf-8", timeout=timeout)

    def _repository_path(self, path: str) -> str:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("file path must be repository-relative without '..'")
        return str(PurePosixPath(self.root) / relative)

    def read_text(self, path: str) -> str:
        result = self.execute(["cat", "--", self._repository_path(path)])
        result.check_returncode()
        return result.stdout

    def write_text(self, path: str, content: str) -> None:
        argv = ["bash", "-c", 'cat > "$1"', "write_text", self._repository_path(path)]
        subprocess.run(self.launch_command(argv), input=content, capture_output=True,
                       text=True, encoding="utf-8", check=True)

    def new_artifacts(self) -> ArtifactDirectory:
        """The parent directory must already be mounted at artifacts.execution."""
        directory = Path(tempfile.mkdtemp(prefix="call-", dir=self.artifacts.host)).resolve()
        execution = PurePosixPath(self.artifacts.execution) / directory.name
        return ArtifactDirectory(directory, str(execution))
