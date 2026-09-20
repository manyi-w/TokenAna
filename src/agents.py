"""Bind experiment agent options without involving the selected method."""

from dataclasses import dataclass
from typing import Any, Mapping

from .interfaces import Agent, AgentResult, Workspace
from .models import ModelConfig, model_options
from .prompts import bind_prompt


@dataclass
class BoundAgent:
    adapter: Agent
    options: Mapping[str, Any]
    model: ModelConfig | None = None
    method_version: str = "legacy-unversioned"

    def effective_options(self) -> dict:
        return model_options(self.adapter, self.model, self.options)

    def run_controlled(self, prompt, workspace, budget):
        if "turn_control" not in getattr(self.adapter, "capabilities", ()):
            raise ValueError("agent does not support same-session turn control")
        return self.run(prompt, workspace, {"turn_control": budget.options()})

    def run_session(self, prompt, workspace, options, callback):
        from .session import require_session

        require_session(self.adapter)
        effective = model_options(self.adapter, self.model, {**self.options, **options})
        if callable(getattr(workspace, "expose_model_endpoint", None)) and not effective.get("record_raw_usage"):
            raise ValueError("isolated model transport requires record_raw_usage=true")
        prompt, workspace = bind_prompt(prompt, workspace, self.method_version)
        return self.adapter.run_session(prompt, workspace, effective, callback)

    def run(
        self, prompt: str, workspace: Workspace, options: Mapping[str, Any]
    ) -> AgentResult:
        if callable(getattr(workspace, "expose_model_endpoint", None)) and not (
                {**self.options, **options}.get("record_raw_usage")):
            raise ValueError("isolated model transport requires record_raw_usage=true")
        effective = model_options(self.adapter, self.model, {**self.options, **options})
        prompt, workspace = bind_prompt(prompt, workspace, self.method_version)
        return self.adapter.run(prompt, workspace, effective)
