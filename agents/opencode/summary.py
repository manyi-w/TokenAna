"""Control integrity and final summaries from an exported native root session."""

import json
from pathlib import Path

from src.accounting import FinalSummary


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plugin_ready(directory, budget):
    try:
        control = _read(directory / "control.json")
        return (control.get("version") == "opencode-message-boundary-v1"
                and control.get("initialized") is True
                and control.get("initial_budget") == budget["initial"]
                and control.get("final_budget") == budget["final"])
    except (OSError, ValueError, AttributeError):
        return False


def _integer(value):
    return type(value) is int and value >= 0


def load_outcome(directory, budget=None):
    control = _read(directory / "control.json")
    if not isinstance(control, dict):
        raise ValueError("invalid control record")
    initial, final = control.get("initial_budget"), control.get("final_budget")
    if not _integer(initial) or not _integer(final) or not 0 < initial <= final:
        raise ValueError("invalid control budget")
    if budget is None:
        budget = {"initial": initial, "final": final}
    if not plugin_ready(directory, budget):
        raise ValueError("plugin initialization or budget does not match")
    used, active, turns = control.get("used_turns"), control.get("active_budget"), control.get("turns")
    if (not _integer(used) or not _integer(active) or not used <= active <= final
            or not isinstance(turns, list) or len(turns) != used):
        raise ValueError("invalid controlled turn count")
    ids = []
    for number, turn in enumerate(turns, 1):
        if (not isinstance(turn, dict) or turn.get("number") != number
                or not isinstance(turn.get("message_id"), str)
                or turn.get("active_budget") != (initial if number <= initial else final)):
            raise ValueError("invalid controlled turn identity")
        ids.append(turn["message_id"])
    if len(set(ids)) != used:
        raise ValueError("duplicate controlled turn IDs")
    extensions = control.get("extensions")
    if extensions == []:
        if active != initial or used > initial:
            raise ValueError("invalid unextended budget")
    elif (extensions != [{"after_turn": initial, "from": initial, "to": final}]
          or final <= initial or active != final or used <= initial):
        raise ValueError("invalid one-time extension")
    exhausted = control.get("termination_reason") == "budget_exhausted"
    if control.get("termination_reason") not in {"running", "budget_exhausted"}:
        raise ValueError("unknown plugin termination reason")
    if exhausted and (used != final or control.get("blocked_message_id") in ids
                      or not isinstance(control.get("blocked_message_id"), str)):
        raise ValueError("invalid pre-sampling budget stop")

    native = _read(directory / "session.json")
    if not isinstance(native, dict) or not isinstance(native.get("info"), dict):
        raise ValueError("missing native root session")
    session = control.get("main_session")
    if not isinstance(session, str) or native["info"].get("id") != session or native["info"].get("parentID"):
        raise ValueError("native session does not match the controlled root")
    messages = native.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("missing native messages")
    indexed, parts = {}, {}
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("info"), dict):
            raise ValueError("invalid native message")
        info = message["info"]
        if (not isinstance(info.get("id"), str) or info.get("sessionID") != session
                or info["id"] in indexed or not isinstance(message.get("parts"), list)):
            raise ValueError("invalid native message identity")
        indexed[info["id"]] = message
        for part in message["parts"]:
            if (not isinstance(part, dict) or not isinstance(part.get("id"), str)
                    or part.get("sessionID") != session or part.get("messageID") != info["id"]):
                raise ValueError("invalid native part identity")
            if part["id"] in parts and parts[part["id"]] != part:
                raise ValueError("conflicting native part")
            parts[part["id"]] = part
    for message_id in ids:
        info = indexed.get(message_id, {}).get("info", {})
        if info.get("role") != "assistant" or info.get("summary") or info.get("agent") != "build":
            raise ValueError("counted turn is not a native main-loop assistant")
    if exhausted and control["blocked_message_id"] not in indexed:
        raise ValueError("budget stop has no native pre-sampling placeholder")
    for part in parts.values():
        message = indexed[part["messageID"]]["info"]
        if (part.get("type") == "step-finish" and message.get("agent") == "build"
                and not message.get("summary") and part["messageID"] not in ids):
            raise ValueError("native main sampling step is absent from the control journal")
        if (exhausted and part["messageID"] == control["blocked_message_id"]
                and part.get("type") in {"step-start", "step-finish", "tool"}):
            raise ValueError("blocked placeholder unexpectedly executed a sampling step")
    assistants = [m for m in messages if m["info"].get("role") == "assistant"]
    users = [m for m in messages if m["info"].get("role") == "user"]
    if not assistants or not users:
        raise ValueError("native session has no conversation")
    last, user = assistants[-1], users[-1]
    info = last["info"]
    completed = bool(info.get("time", {}).get("completed"))
    has_tools = any(p.get("type") == "tool" and not p.get("metadata", {}).get("providerExecuted")
                    and not (p.get("state", {}).get("status") == "error"
                             and p.get("state", {}).get("metadata", {}).get("interrupted") is True)
                    for p in last["parts"])
    native_success = (completed and not info.get("error") and not info.get("summary")
                      and info.get("finish") not in (None, "", "tool-calls", "unknown", "content-filter", "error")
                      and not has_tools and info.get("parentID") == user["info"]["id"])
    process = _read(directory / "process.json")
    ended = isinstance(process, dict) and type(process.get("returncode")) is int
    success = bool(ended and process["returncode"] == 0 and native_success and not exhausted)
    outcome = {**control, "termination_reason": "budget_exhausted" if exhausted else
               "completed" if success else "execution_error", "success": success}
    return outcome, parts, indexed, bool(ended and (completed or exhausted))


def final_summary(directory):
    control, parts, messages, finished = load_outcome(directory)
    counts = {"input": [], "output": []}
    covered = set()
    for part in parts.values():
        if part.get("type") != "step-finish":
            continue
        covered.add(part["messageID"])
        tokens = part.get("tokens")
        tokens = tokens if isinstance(tokens, dict) else {}
        cache = tokens.get("cache")
        cache = cache if isinstance(cache, dict) else {}
        for name, values in (("input", [tokens.get("input"), cache.get("read"), cache.get("write")]),
                             ("output", [tokens.get("output"), tokens.get("reasoning")])):
            counts[name].append(sum(values) if all(_integer(v) for v in values) else None)
    requested = {turn["message_id"] for turn in control["turns"]}
    requested.update(key for key, m in messages.items() if m["info"].get("summary"))
    def total(name):
        values = counts[name]
        return sum(values) if requested <= covered and all(v is not None for v in values) else None
    return FinalSummary(finished, sum(p.get("type") == "tool" for p in parts.values()),
                        total("input"), total("output"), "session.json:messages.parts.step-finish; control.json")
