"""Optional same-session turn control contract, independent of agent implementation."""

from dataclasses import asdict, dataclass
from typing import Protocol
from .interfaces import AgentResult, Workspace


@dataclass(frozen=True)
class TurnBudget:
    initial: int
    final: int

    def __post_init__(self):
        if type(self.initial) is not int or type(self.final) is not int or not 0 < self.initial <= self.final:
            raise ValueError("turn budget requires 0 < initial <= final integers")

    def options(self):
        return asdict(self)


class ControlledAgent(Protocol):
    def run_controlled(self, prompt: str, workspace: Workspace, budget: TurnBudget) -> AgentResult: ...


def validate_method(method, agent, options, agent_options=None):
    required = getattr(method, "required_capabilities", ())
    missing = set(required) - set(getattr(agent, "capabilities", ()))
    if missing:
        raise ValueError("agent lacks method capabilities: " + ", ".join(sorted(missing)))
    validate = getattr(method, "validate_options", None)
    if validate:
        validate(options)
    validate_agent = getattr(method, "validate_agent_options", None)
    if callable(validate_agent) and agent_options is not None:
        validate_agent(options, agent_options)
    if "turn_control" in required and agent_options is not None:
        if getattr(agent, "control_runtime", "python") == "python" and not agent_options.get("python_executable"):
            raise ValueError("turn_control requires agent.options.python_executable in the prepared image")
        validate_control = getattr(agent, "validate_control_options", None)
        if callable(validate_control):
            validate_control(agent_options)
