"""Executed only in the prepared Trae image; subclass hooks, never monkey patches."""

import asyncio
import json
import os
from pathlib import Path
import sys

from trae_agent.agent.trae_agent import TraeAgent
from trae_agent.agent.agent_basics import AgentState
from trae_agent.utils.config import Config
from trae_agent.utils.llm_clients.llm_basics import LLMMessage
from trae_agent.utils.trajectory_recorder import TrajectoryRecorder
from trae_agent.utils.cli.simple_console import SimpleCLIConsole
from trae_agent.utils.llm_clients.llm_client import LLMClient
from trae_agent.utils.llm_clients.anthropic_client import AnthropicClient
from trae_agent.utils.lake_view import LakeView
from trae_agent.utils.llm_clients.openai_compatible_base import OpenAICompatibleClient, ProviderConfig
from types import SimpleNamespace


class DirectChatProvider(ProviderConfig):
    def create_client(self, api_key, base_url, api_version):
        import openai
        return openai.OpenAI(api_key=api_key, base_url=base_url)

    def get_service_name(self):
        return os.environ["TOKENANA_PROVIDER"]

    def get_provider_name(self):
        return self.get_service_name()

    def get_extra_headers(self):
        return {}

    def supports_tool_calling(self, model_name):
        return True


class CachedAnthropic(AnthropicClient):
    def _create_anthropic_response(self, model_config, tool_schemas):
        # Stable system prefix only; preserve native messages, tools and retry handling.
        if isinstance(self.system_message, str):
            self.system_message = [{"type": "text", "text": self.system_message,
                                    "cache_control": {"type": "ephemeral"}}]
        return super()._create_anthropic_response(model_config, tool_schemas)


class SelectedClient(LLMClient):
    def __init__(self, config):
        super().__init__(config)
        if config.model_provider.provider == "anthropic":
            self.client = CachedAnthropic(config)
        elif os.environ.get("TOKENANA_PROTOCOL") == "chat_completions":
            self.client = OpenAICompatibleClient(config, DirectChatProvider())
            self.provider = SimpleNamespace(value=os.environ["TOKENANA_PROVIDER"])


class SelectedTrae(TraeAgent):
    def __init__(self, config):
        super().__init__(config)
        self._llm_client = SelectedClient(config.model)


class SelectedLakeview(LakeView):
    def __init__(self, config):
        super().__init__(config)
        self.lakeview_llm_client = SelectedClient(config.model)


class SelectedConsole(SimpleCLIConsole):
    def __init__(self, config):
        super().__init__(lakeview_config=config)
        if config:
            self.lake_view = SelectedLakeview(config)


class ControlledTrae(SelectedTrae):
    def __init__(self, config, budget, output):
        super().__init__(config)
        self.budget, self.output = budget, output
        self.native_step_limit = self._max_steps
        self._max_steps = min(self.native_step_limit, budget["initial"])
        self.control = {"initial_budget": budget["initial"], "final_budget": budget["final"],
                        "active_budget": self._max_steps, "used_turns": 0, "extensions": [],
                        "termination_reason": "running"}
        self.save_control()

    def save_control(self):
        temporary = self.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.control, indent=2), encoding="utf-8")
        temporary.replace(self.output)
        with self.output.with_name('control-history.jsonl').open('a') as history:
            history.write(json.dumps(self.control) + '\n')

    def record_overhead(self, started):
        import time
        with self.output.with_name('timing.jsonl').open('a') as stream:
            stream.write(json.dumps({'version': 1, 'event': 'end', 'phase': 'control_callback',
                'utc_seconds': time.time(), 'seconds': time.perf_counter()-started}) + '\n')

    async def _run_llm_step(self, step, messages, execution):
        import time
        started = time.perf_counter()
        self.control["used_turns"] = step.step_number
        self.save_control()
        reminder = LLMMessage(role="user", content=(
            f"Turn budget: this is turn {step.step_number} of {self._max_steps}. "
            f"You have {self._max_steps - step.step_number + 1} turns including this one. "
            "Prioritize completing the repository repair and use task_done when finished."))
        self.record_overhead(started)
        return await super()._run_llm_step(step, [*messages, reminder], execution)

    async def _finalize_step(self, step, messages, execution):
        await super()._finalize_step(step, messages, execution)
        import time
        started = time.perf_counter()
        if (step.step_number == self.budget["initial"] and not execution.success
                and execution.agent_state != AgentState.ERROR and not self.control["extensions"]
                and min(self.native_step_limit, self.budget["final"]) > self._max_steps):
            self.control["extensions"].append({"after_turn": step.step_number,
                                                "from": self._max_steps, "to": min(self.native_step_limit, self.budget["final"])})
            self._max_steps = min(self.native_step_limit, self.budget["final"])
            self.control["active_budget"] = self._max_steps
            messages.append(LLMMessage(role="user", content=(
                f"Your turn budget is extended once to {self._max_steps}. "
                "Continue in this same session; no further extension is available.")))
        self.save_control()
        self.record_overhead(started)


async def run(directory, root):
    config = Config.create(config_file=str(directory / "config.yaml")).resolve_config_values()
    request = json.loads((directory / "control-request.json").read_text())
    budget = request.get("budget")
    prompt = (directory / "prompt.txt").read_text(encoding="utf-8")
    agent = (ControlledTrae(config.trae_agent, budget, directory / "control.json") if budget else
             SelectedTrae(config.trae_agent))
    console = SelectedConsole(config.lakeview)
    agent.set_cli_console(console)
    agent.set_trajectory_recorder(TrajectoryRecorder(str(directory / "trajectory.json")))
    os.chdir(root)
    agent.new_task(prompt, {"project_path": root, "issue": prompt, "must_patch": "true" if budget else "false",
                            "base_commit": request.get("patch_base_commit", ""),
                            "patch_path": str(directory / "native.patch.diff")})
    try:
        execution = await agent.execute_task()
        if execution.agent_state not in (AgentState.COMPLETED, AgentState.ERROR):
            execution.agent_state = AgentState.ERROR
            console.update_status(agent_execution=execution)
        await console.start()
        reason = ("completed" if execution.success else
                  "execution_error" if any(step.error for step in execution.steps) else
                  "native_limit" if budget and agent.native_step_limit < budget["final"]
                  and agent.control["used_turns"] >= agent.native_step_limit else
                  "budget_exhausted" if budget and agent.control["used_turns"] >= agent.max_steps else
                  "execution_error")
        if budget:
            agent.control.update(termination_reason=reason, success=execution.success,
                             tool_calls=sum(len(step.tool_calls or []) for step in execution.steps),
                             duration_sec=execution.execution_time)
    except BaseException:
        if budget:
            agent.control["termination_reason"] = "interrupted_or_error"
        raise
    finally:
        if budget:
            agent.save_control()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
