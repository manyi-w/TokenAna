"""Read mini's native final exit/usage evidence without using HTTP usage as a substitute."""

from src.accounting import FinalSummary


def _integer(value):
    return type(value) is int and value >= 0


def final_summary(trajectory):
    info, messages = trajectory["info"], trajectory["messages"]
    last = messages[-1] if messages else {}
    status = info.get("exit_status")
    finished = (bool(status) and last.get("role") == "exit"
                and last.get("extra", {}).get("exit_status") == status)
    actions = [m.get("extra", {}).get("actions", []) for m in messages]
    function_calls = sum(map(len, actions)) if all(isinstance(a, list) for a in actions) else None
    responses = [m for m in messages if "response" in m.get("extra", {})
                 or m.get("role") == "assistant" or m.get("object") == "response"]
    stats = info.get("model_stats")
    calls = stats.get("api_calls") if isinstance(stats, dict) else None
    inputs, outputs = [], []
    for message in responses:
        response = message.get("extra", {}).get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            usage = message.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        incoming = usage.get("input_tokens", usage.get("prompt_tokens"))
        outgoing = usage.get("output_tokens", usage.get("completion_tokens"))
        inputs.append(incoming if _integer(incoming) else None)
        outputs.append(outgoing if _integer(outgoing) else None)
    # A failed/missing native response must not become a measured zero.
    complete = _integer(calls) and calls == len(responses)
    incoming = sum(inputs) if complete and all(v is not None for v in inputs) else None
    outgoing = sum(outputs) if complete and all(v is not None for v in outputs) else None
    return FinalSummary(finished, function_calls, incoming, outgoing,
                        "trajectory.json:info.exit_status,model_stats.api_calls,messages")


def validate_control(control, budget, info):
    if not isinstance(control, dict) or control.get("version") != "mini-query-boundary-v1":
        raise ValueError("control must be an object")
    initial, final = budget["initial"], budget["final"]
    stats = info.get("model_stats")
    calls = stats.get("api_calls") if isinstance(stats, dict) else None
    used, active = control.get("used_turns"), control.get("active_budget")
    if (control.get("initial_budget") != initial or control.get("final_budget") != final
            or not _integer(used) or not _integer(active) or not used <= active <= final
            or not _integer(calls) or used != calls):
        raise ValueError("control budgets or native call count disagree")
    extensions = control.get("extensions")
    expected = [{"after_turn": initial, "from": initial, "to": final}]
    if extensions == []:
        if active != initial or used > initial:
            raise ValueError("invalid unextended budget")
    elif extensions != expected or final <= initial or active != final or used < initial:
        raise ValueError("invalid one-time extension")
    reason = control.get("termination_reason")
    if reason not in {"completed", "budget_exhausted", "native_limit", "wall_time_limit",
                      "execution_error", "interrupted_or_error"}:
        raise ValueError("control has no final termination reason")
    if (reason == "completed") != (info.get("exit_status") == "Submitted"):
        raise ValueError("control completion disagrees with native exit")
    if reason == "budget_exhausted" and (used != final or info.get("exit_status") != "TurnBudgetExceeded"):
        raise ValueError("invalid exhausted budget")
