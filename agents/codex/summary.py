"""Strict control evidence and original native summaries; never read HTTP usage here."""

import json
import re

from src.accounting import FinalSummary

VERSION = "codex-sampling-boundary-v1"
PROFILES = {"gpt": (50, 67), "claude": (52, 64), "gemini": (29, 45)}
PROFILES['deepswe_gpt'] = (53, 74)
REMINDER = re.compile(r"<tokenana_budget>\nThis is main-loop interaction (\d+) of (\d+)\.")
TOOLS = {"function_call", "custom_tool_call", "local_shell_call", "tool_search_call",
         "web_search_call", "image_generation_call"}


def read_jsonl(path):
    data = path.read_text(encoding="utf-8")
    if not data or not data.endswith("\n"):
        raise ValueError(f"missing or truncated JSONL: {path.name}")
    records = [json.loads(line) for line in data.splitlines() if line.strip()]
    if not records or any(not isinstance(record, dict) for record in records):
        raise ValueError(f"invalid JSONL objects: {path.name}")
    return records


def _integer(value):
    return type(value) is int and value >= 0


def read_control(directory, budget=None):
    request = json.loads((directory / "control-request.json").read_text())
    if (not isinstance(request, dict) or request.get("version") != VERSION
            or request.get("profile") not in PROFILES):
        raise ValueError("invalid Codex control request")
    initial, final = PROFILES[request["profile"]]
    if budget is not None and budget != {"initial": initial, "final": final}:
        raise ValueError("Codex budget does not match the call")
    records = read_jsonl(directory / "control-events.jsonl")
    first = records[0]
    turn_id = first.get("turn_id")
    if first.get("event") != "initialized" or not isinstance(turn_id, str) or not turn_id:
        raise ValueError("missing Codex control initialization")
    used, limit, extended, exhausted = 0, initial, False, False
    for index, record in enumerate(records):
        event = record.get("event")
        if exhausted:
            raise ValueError("control events after budget stop")
        if index == 0:
            pass
        elif event == "extended" and not extended and used == initial:
            extended, limit = True, final
        elif event == "before_sampling" and used < limit:
            used += 1
        elif event == "budget_exhausted" and extended and used == final:
            exhausted = True
        else:
            raise ValueError("invalid Codex sampling/extension sequence")
        expected = {"version": VERSION, "event": event,
                    "turn_id": turn_id if index == 0 else None,
                    "used_turns": used, "initial_budget": initial,
                    "final_budget": final, "current_budget": limit, "extended": extended}
        if (record != expected or type(record.get("extended")) is not bool
                or any(not _integer(record.get(k)) for k in
                       ("used_turns", "initial_budget", "final_budget", "current_budget"))):
            raise ValueError("inconsistent Codex control event")
    return {"version": VERSION, "turn_id": turn_id, "used_turns": used,
            "initial_budget": initial, "final_budget": final, "current_budget": limit,
            "extended": extended, "budget_exhausted": exhausted}


def control_ready(directory, budget):
    # Only initialization gates upstream access. This is not an HTTP turn counter.
    # Read just the immutable first line: a concurrent append must not reject retries.
    try:
        with (directory / "control-events.jsonl").open(encoding="utf-8") as stream:
            line = stream.readline()
        if not line.endswith("\n"):
            return False
        record = json.loads(line)
        return (record.get("version") == VERSION and record.get("event") == "initialized"
                and record.get("initial_budget") == budget["initial"]
                and record.get("final_budget") == budget["final"]
                and record.get("used_turns") == 0 and record.get("extended") is False
                and isinstance(record.get("turn_id"), str) and bool(record["turn_id"]))
    except (OSError, ValueError, AttributeError, UnicodeError):
        return False


def _root_rollout(directory, thread_id):
    candidates = []
    home = directory / "codex-home"
    for path in sorted((home / "sessions").rglob("*.jsonl")):
        # Subagent rollouts are retained, but do not belong to this native summary.
        with path.open(encoding="utf-8") as stream:
            first = json.loads(stream.readline())
        payload = first.get("payload", {})
        if first.get("type") == "session_meta" and payload.get("id") == thread_id:
            if payload.get("parent_thread_id") or payload.get("source") != "exec":
                raise ValueError("controlled rollout is not a native Exec root")
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError("no unique native root rollout (compressed/missing history is unsupported)")
    return read_jsonl(candidates[0])


def load_outcome(directory, budget=None):
    control = read_control(directory, budget)
    trace = read_jsonl(directory / "trace.jsonl")
    threads = [r.get("thread_id") for r in trace if r.get("type") == "thread.started"]
    starts = [r for r in trace if r.get("type") == "turn.started"]
    terminal = [r for r in trace if r.get("type") in {"turn.completed", "turn.failed"}]
    if (len(threads) != 1 or not isinstance(threads[0], str) or len(starts) != 1
            or len(terminal) > 1):
        raise ValueError("Codex native trace is not a single root turn")
    rollout = _root_rollout(directory, threads[0])
    events = [r["payload"] for r in rollout if r.get("type") == "event_msg"]
    native_starts = [r for r in events if r.get("type") == "task_started"]
    ends = [r for r in events if r.get("type") in {"task_complete", "turn_aborted"}]
    if (len(native_starts) != 1 or native_starts[0].get("turn_id") != control["turn_id"]
            or len(ends) > 1 or any(r.get("turn_id") != control["turn_id"] for r in ends)):
        raise ValueError("native turn identity disagrees with the control journal")
    reminders, calls = [], {}
    for record in rollout:
        if record.get("type") != "response_item":
            continue
        item = record["payload"]
        if item.get("type") == "message" and item.get("role") == "developer":
            for part in item.get("content", []):
                reminders.extend((int(n), int(limit)) for n, limit in
                                 REMINDER.findall(part.get("text", "")))
        if item.get("type") in TOOLS:
            identity = item.get("call_id") or item.get("id")
            if not isinstance(identity, str) or not identity:
                raise ValueError("native tool call has no identity")
            key = (item["type"], identity)
            # Native streaming updates may refine status but not create another call.
            calls[key] = item
    expected = [(n, control["initial_budget"] if n <= control["initial_budget"] else
                 control["final_budget"]) for n in range(1, control["used_turns"] + 1)]
    if reminders != expected:
        raise ValueError("native reminders disagree with sampling boundaries")
    process = json.loads((directory / "process.json").read_text())
    ended = (isinstance(process, dict) and type(process.get("returncode")) is int
             and process.get("transport_error") is None)
    native_error = (any(r.get("type") in {"error", "turn.failed"} for r in trace)
                    or any(r.get("error") or r.get("type") == "turn_aborted" for r in ends))
    finished = bool(ended and terminal and ends)
    success = bool(finished and process["returncode"] == 0 and not native_error
                   and terminal[0]["type"] == "turn.completed"
                   and ends[0]["type"] == "task_complete"
                   and control["used_turns"] > 0 and not control["budget_exhausted"])
    outcome = {**control, "thread_id": threads[0], "success": success,
               "termination_reason": "budget_exhausted" if control["budget_exhausted"] else
               "completed" if success else "execution_error"}
    return outcome, trace, rollout, finished, len(calls)


def final_summary(directory):
    control, trace, rollout, finished, calls = load_outcome(directory)
    records = {}
    for row in rollout:
        if row.get("type") != "token_usage_record":
            continue
        value = row["payload"]
        key = value.get("response_id")
        if (not isinstance(key, str) or not key or value.get("thread_id") != control["thread_id"]
                or value.get("turn_id") != control["turn_id"]):
            raise ValueError("invalid native usage identity")
        if key in records and records[key] != value:
            raise ValueError("conflicting native usage record")
        records[key] = value
    updates = [r["payload"].get("info") for r in rollout if r.get("type") == "event_msg"
               and r["payload"].get("type") == "token_count" and r["payload"].get("info") is not None]
    total = updates[-1].get("total_token_usage", {}) if updates else {}
    # The JSONL serializer emits default zero usage if no native update was observed.
    # Require observed records covering sampling; never use that default as evidence.
    coverage = (bool(records) and len(records) == control["used_turns"]
                and not any(r.get("type") == "compacted" for r in rollout))
    completed = [r for r in trace if r.get("type") == "turn.completed"]
    def token(name):
        values = [r.get("usage", {}).get(name) for r in records.values()]
        value = total.get(name)
        if (not coverage or not _integer(value) or not all(_integer(v) for v in values)
                or sum(values) != value):
            return None
        if completed and completed[0].get("usage", {}).get(name) != value:
            return None
        return value
    return FinalSummary(finished, calls, token("input_tokens"), token("output_tokens"),
                        "codex-native-summary-v1: trace.jsonl + root rollout + control-events.jsonl")
