"""Enumerate public repair tasks from the local Verified data copy."""

import json
from pathlib import Path

from src.interfaces import AgentResult, PatchResult, Task, Workspace


class SweBenchVerified:
    def __init__(self, data_path: Path | None = None):
        self.data_path = (data_path if data_path is not None else
                          Path(__file__).parent / "data/swe_bench_verified.json")

    def plan(self, options) -> dict:
        return {
            "data_path": str(self.data_path.resolve()),
            "required": ["Prepared task containers at each selected base commit",
                         "Activated task environment and separate artifact mount",
                         "sb-cli and SWEBENCH_API_KEY for later authorized submission"],
            "submission_template": ["sb-cli", "submit", "swe-bench_verified", "test",
                                    "--predictions_path", "<predictions.json>", "--run_id", "<run-id>"],
            "report_template": ["sb-cli", "get-report", "swe-bench_verified", "test", "<run-id>"],
            "environment_checked": False,
        }

    def tasks(self, *, limit: int | None = None) -> list[Task]:
        """Keep file order; None selects all records, zero selects none."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a non-negative integer or None")
        with self.data_path.open(encoding="utf-8") as stream:
            records = json.load(stream)
        return [Task(
            instance_id=record["instance_id"],
            repo=record["repo"],
            base_commit=record["base_commit"],
            problem_statement=record["problem_statement"],
        ) for record in records[:limit]]

    def prepare_submission(self, results, directory: Path, *, agent: str, mode: str,
                           run_id: str | None = None):
        from .evaluation import prepare_submission

        return prepare_submission(results, directory, agent=agent, mode=mode, run_id=run_id)

    def read_report(self, path: Path):
        from .evaluation import read_report

        return read_report(path)

    def validate_runtime(self, tasks, runtime):
        for key in ("network", "command_prefix", "images"):
            if key not in runtime:
                raise ValueError(f"runtime requires {key}")
        missing = [task.instance_id for task in tasks if task.instance_id not in runtime["images"]]
        if missing:
            raise ValueError(f"runtime missing task images: {', '.join(missing)}")

    def prepare(self, task: Task, directory: Path, runtime: dict):
        from .workspace import prepare

        return prepare(task, directory, runtime)

    def collect_saved(self, trace: AgentResult) -> PatchResult:
        if trace.artifacts is None:
            raise ValueError("agent result has no artifact directory")
        path = trace.artifacts.host / "patch.diff"
        patch = path.read_text(encoding="utf-8") if path.exists() else ""
        return self._patch_result(patch, trace)

    def collect(self, workspace: Workspace, trace: AgentResult) -> PatchResult:
        """Prefer workspace git diff, then the original runner's text fallback.

        The caller explicitly supplies the submission's agent result; this does
        not choose a final call or aggregate errors for multi-call methods.
        """
        diff = workspace.execute(["git", "diff"])
        diff.check_returncode()
        return self._patch_result(diff.stdout, trace)

    @staticmethod
    def _patch_result(patch: str, trace: AgentResult) -> PatchResult:
        patch = patch.strip()
        if not patch:
            lines = trace.output.split("\n")
            for index, line in enumerate(lines):
                if line.startswith(("diff --git", "---", "+++")):
                    patch = "\n".join(lines[index:]).strip()
                    break
        return PatchResult(patch=patch, success=bool(patch) and not trace.error,
                           error=trace.error or "")
