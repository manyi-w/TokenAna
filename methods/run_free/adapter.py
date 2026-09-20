"""Adapt the preserved Python prompt with the explicitly approved Git change."""

from typing import Any, Mapping

from src.interfaces import Agent, MethodResult, Task, Workspace
from .upstream.experiments.prompt_builder import PromptBuilder


class RunFree:
    original_trace_formats = ("codex-jsonl",)
    version = "run-free-git-v2"
    task_languages = frozenset({"python"})

    def build_prompt(self, task):
        prompt = PromptBuilder.build_run_free_prompt({
            "repo": task.repo, "base_commit": task.base_commit,
            "problem_statement": task.problem_statement,
        })
        marker = "❌ Run `git` commands (interferes with experiment)\n\n## Debugging Strategy"
        before, separator, after = prompt.rpartition(marker)
        if not separator:
            raise ValueError("upstream run_free prompt changed; review the Git adaptation")
        return before + "\n## Debugging Strategy" + after

    def original_accounting(self, cases):
        """Reproduce original Codex analysis without changing execution."""
        from .accounting import original_accounting

        return original_accounting(cases)

    def run(
        self, task: Task, agent: Agent, workspace: Workspace,
        options: Mapping[str, Any],
    ) -> MethodResult:
        """Run one agent call; run_free has no method-specific options."""
        from contextlib import nullcontext
        from src.telemetry import span
        root = getattr(getattr(workspace, 'artifacts', None), 'host', None)
        with span(root, 'method_prepare') if root is not None else nullcontext():
            prompt = self.build_prompt(task)
        return MethodResult(calls=[agent.run(prompt, workspace, {})])
