"""Optional native-session contract. Adapters map native messages without losing fields.

Only explicitly exposed text is editable. Protocol payloads (including tool IDs,
arguments, signatures and opaque blocks) stay in native and are never rewritten.
No dependencies on an agent or method are loaded here.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .records import write_json


EVENTS = frozenset({"initialize", "before_model", "after_model", "after_tool", "finish"})
CAPABILITIES = frozenset({"events", "replace_history", "state", "reminder", "terminate"})


@dataclass(frozen=True)
class Message:
    id: str
    role: str
    text: str
    native: Mapping[str, Any] = field(default_factory=dict)
    editable: bool = False
    tool_calls: tuple[str, ...] = ()
    tool_result: str | None = None


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    history: Sequence[Message]
    state: Mapping[str, Any]
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepSummary:
    """Replace a complete editable span with an adapter-created assistant message."""
    message_ids: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class SessionDecision:
    history: Sequence[Message] | None = None
    state: Mapping[str, Any] | None = None
    reminder: str | None = None
    terminate: str | None = None
    summaries: tuple[StepSummary, ...] = ()


class SessionCallback(Protocol):
    def __call__(self, event: SessionEvent) -> SessionDecision | None: ...


def validate_history(history, *, pending_tools=False):
    ids, calls, results = set(), set(), set()
    pending = set()
    for message in history:
        if not isinstance(message, Message) or not message.id or message.id in ids:
            raise ValueError("history requires unique message IDs")
        if not isinstance(message.text, str):
            raise ValueError("message text must be a string")
        ids.add(message.id)
        if message.tool_result is not None:
            if message.tool_result not in pending or message.tool_result in results:
                raise ValueError("orphaned or duplicate tool result")
            pending.remove(message.tool_result)
            results.add(message.tool_result)
        elif pending and not (message.role == "assistant" or
                              (pending_tools and message.role == "exit")):
            raise ValueError("tool results must precede the next message")
        for call in message.tool_calls:
            if not isinstance(call, str) or not call or call in calls:
                raise ValueError("duplicate or invalid tool call ID")
            calls.add(call)
            pending.add(call)
    if pending and not pending_tools:
        raise ValueError("tool call is missing its result")


def validate_replacement(before, after, *, pending_tools=False):
    validate_history(after, pending_tools=pending_tools)
    if pending_tools:
        pending = lambda history: ({call for m in history for call in m.tool_calls}
                                   - {m.tool_result for m in history if m.tool_result})
        if pending(before) != pending(after):
            raise ValueError("replacement removed an in-flight tool call")
    originals = {message.id: message for message in before}
    retained = []
    for message in after:
        old = originals.get(message.id)
        if old is None:
            raise ValueError("replacement cannot invent native messages; use reminder")
        retained.append(message.id)
        if any(getattr(old, key) != getattr(message, key) for key in
               ("role", "native", "editable", "tool_calls", "tool_result")):
            raise ValueError("replacement modified immutable protocol fields")
        if not old.editable and old.text != message.text:
            raise ValueError("replacement modified protected text")
    if retained != [message.id for message in before if message.id in retained]:
        raise ValueError("replacement reordered native messages")
    if any(not message.editable and message.id not in retained for message in before):
        raise ValueError("replacement removed a protected message")


class Session:
    """One adapter-owned session; journal before/after snapshots before returning.

    before_model's returned history is the actual input the adapter must map back
    into its native request. Reminders/termination are applied by the native adapter,
    never by an HTTP rewrite. Tools may be pending after a response/tool event, but
    must be paired before another model request. A failed callback aborts the call.
    """

    def __init__(self, callback: SessionCallback, directory: Path):
        self.callback, self.directory = callback, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.state = {}
        self.started = self.finished = False
        self.sequence = 0
        self.termination = None
        self.last_kind = None

    def record_model_input(self, native_request):
        """Native adapter calls after applying history/reminder, before sending.

        Supply the request payload only, never authentication headers or secrets.
        This complements the agent's unchanged original trajectory.
        """
        if not self.started or self.finished or self.termination or self.last_kind != "before_model":
            raise ValueError("model input is not allowed in this session state")
        path = self.directory / f"{self.sequence:06d}-model-input.json"
        if path.exists():
            raise ValueError("model input already recorded for this event")
        write_json(path, deepcopy(native_request))

    def emit(self, kind, history, **details):
        if kind not in EVENTS or self.finished:
            raise ValueError("invalid event or session already finished")
        if kind == "before_model" and self.termination:
            raise ValueError("session termination already requested")
        if (kind == "initialize") == self.started:
            raise ValueError("initialize must occur exactly once before other events")
        pending = kind in {"after_model", "after_tool", "finish"}
        before = deepcopy(list(history))
        validate_history(before, pending_tools=pending)
        self.sequence += 1
        path = self.directory / f"{self.sequence:06d}.json"
        event = SessionEvent(kind, before, deepcopy(self.state), deepcopy(details))
        record = {"version": 1, "event": asdict(event), "status": "started"}
        write_json(path, record)
        try:
            from .telemetry import span
            with span(self.directory, 'method_callback', sequence=self.sequence, kind=kind):
                decision = self.callback(deepcopy(event)) or SessionDecision()
            if not isinstance(decision, SessionDecision):
                raise ValueError("callback must return SessionDecision or None")
            after = deepcopy(before if decision.history is None else list(decision.history))
            validate_replacement(before, after, pending_tools=pending)
            for index, summary in enumerate(decision.summaries):
                if not isinstance(summary, StepSummary) or not summary.message_ids or not isinstance(summary.text, str):
                    raise ValueError("invalid step summary")
                positions = [i for i, message in enumerate(after) if message.id in summary.message_ids]
                if (len(positions) != len(summary.message_ids) or
                        positions != list(range(positions[0], positions[-1] + 1)) or
                        any(not after[i].editable for i in positions)):
                    raise ValueError("summary requires a contiguous editable step")
                removed = after[positions[0]:positions[-1] + 1]
                if ({call for message in removed for call in message.tool_calls} !=
                        {message.tool_result for message in removed if message.tool_result}):
                    raise ValueError("summary must replace complete tool pairs")
                synthetic = Message(f"summary-{self.sequence}-{index}", "assistant", summary.text,
                                    {"summary": True}, True)
                after[positions[0]:positions[-1] + 1] = [synthetic]
            validate_history(after, pending_tools=pending)
            for name in ("reminder", "terminate"):
                value = getattr(decision, name)
                if value is not None and (not isinstance(value, str) or not value):
                    raise ValueError(f"{name} must be a nonempty string")
            state = deepcopy(self.state if decision.state is None else dict(decision.state))
            applied = SessionDecision(after, state, decision.reminder, decision.terminate)
            record.update(status="applied", decision=asdict(applied))
            write_json(path, record)
            self.state = deepcopy(state)
            self.started = True
            self.finished = kind == "finish"
            self.termination = self.termination or decision.terminate
            self.last_kind = kind
            return applied
        except BaseException as error:
            record.update(status="failed", error_type=type(error).__name__)
            write_json(path, record)
            raise


def require_session(agent, required=CAPABILITIES):
    missing = set(required) - set(getattr(agent, "session_capabilities", ()))
    if missing or not callable(getattr(agent, "run_session", None)):
        raise ValueError("agent session integration unavailable: " + ", ".join(sorted(missing or {"run_session"})))
