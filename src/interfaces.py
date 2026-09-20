"""Shared contracts for repository-repair methods and agents; no execution code."""

from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Task:
    """Public task input; dataset adapters keep evaluation-only data private."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str


@dataclass
class AgentResult:
    """Original caller fields, without reinterpreting token or execution counts."""

    agent_type: str
    prompt: str
    output: str
    tokens_used: int
    exec_count: int
    duration_sec: float
    raw_trace: list[dict[str, Any]]
    error: str | None = None
    artifacts: "ArtifactDirectory | None" = None
    submission_eligible: bool = True
    control: Mapping[str, Any] | None = None


@dataclass
class MethodResult:
    """Ordered agent calls; patch collection and official evaluation are separate."""

    calls: list[AgentResult]


@dataclass
class PatchResult:
    """Collected submission; success is the legacy generation flag, not resolved."""

    patch: str
    success: bool
    error: str


@dataclass(frozen=True)
class ArtifactDirectory:
    """One call's shared directory, outside the repository and evaluation data."""

    host: Path
    execution: str


@dataclass
class SubmissionPlan:
    """Saved evaluation identity; preparation never executes the evaluator."""

    predictions: Path
    command: list[str]
    report_command: list[str]
    kind: str = "remote_commands"
    run_id: str | None = None


class Workspace(Protocol):
    """Operations occur inside the task environment, never implicitly on the host."""

    @property
    def root(self) -> str:
        """Repository path inside the execution environment."""
        ...

    def execute(
        self, argv: Sequence[str], *, timeout: float | None = None
    ) -> CompletedProcess[str]:
        """Run argv without a shell at root; capture text, preserve nonzero exits.

        Process launch failures and timeouts raise exceptions; no automatic retries.
        Errors reported by the transport CLI retain its nonzero exit code.
        """
        ...

    def read_text(self, path: str) -> str:
        """Read UTF-8 text at a repository-relative path."""
        ...

    def write_text(self, path: str, content: str) -> None:
        """Write UTF-8 text at a repository-relative path."""
        ...

    def new_artifacts(self) -> ArtifactDirectory:
        """Create a fresh directory, shared with the task environment, per call."""
        ...

    def launch_command(self, argv: Sequence[str]) -> list[str]:
        """Build host argv entering the prepared environment at root; do not run.

        The environment must be activated and artifact mappings available there.
        Returned argv must work independently of the host working directory.
        The original caller may execute it directly and own timeout handling.
        """
        ...


class PatchCaptureWorkspace(Workspace, Protocol):
    """Optional dataset-specific capture; legacy workspaces keep git diff."""

    def patch_capture_command(self, artifacts: ArtifactDirectory) -> list[str]:
        """Build in-environment argv writing this call's patch.diff and evidence."""
        ...


class ModelChannelWorkspace(Workspace, Protocol):
    """Optional transport exposing a host recording proxy inside an isolated workspace."""

    def expose_model_endpoint(self, endpoint: str, artifacts: ArtifactDirectory):
        """Return a context manager yielding an agent-visible model base URL."""
        ...


class Agent(Protocol):
    def run(
        self, prompt: str, workspace: Workspace, options: Mapping[str, Any]
    ) -> AgentResult: ...


class Method(Protocol):
    def run(
        self, task: Task, agent: Agent, workspace: Workspace,
        options: Mapping[str, Any],
    ) -> MethodResult:
        """Run method logic; options belong to the method.

        The supplied agent owns its configured defaults. Per-call options passed
        to Agent.run are explicit overrides, interpreted by the agent adapter.
        """
        ...


class SessionAgent(Agent, Protocol):
    """Optional extension; native adapters must explicitly advertise capabilities."""

    session_capabilities: Sequence[str]

    def run_session(self, prompt: str, workspace: Workspace,
                    options: Mapping[str, Any], callback) -> AgentResult: ...
