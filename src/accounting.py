"""Framework accounting over explicitly normalized, disjoint usage observations.

Trace adapters select non-overlapping observations (response deltas OR a session
aggregate), preserve raw files, and report coverage gaps. This module never guesses
trace semantics, retries a model, or substitutes a caller's estimated token count.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


METRICS = ("total", "input", "output", "cache_read", "cache_write", "reasoning")


@dataclass(frozen=True)
class UsageObservation:
    """One disjoint usage scope, with stable identity across report rebuilds.

    observation_id identifies a response, or an adapter-selected aggregate scope.
    Unknown metrics are None. Additional metrics must have known additive semantics.
    raw_usage and source locate the evidence; neither is used as an estimated total.
    """

    case_id: str
    attempt_id: str
    call_id: str
    observation_id: str
    metrics: Mapping[str, int | None]
    source: str
    raw_usage: Mapping[str, Any] = field(default_factory=dict)
    issues: Mapping[str, str] = field(default_factory=dict)
    purpose: str = "unknown"
    model: str | None = None
    parent_call_id: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True)
class UsageOperation:
    """One HTTP attempt or service operation, even when no usage was returned."""

    case_id: str
    attempt_id: str
    call_id: str
    operation_id: str
    purpose: str = "unknown"
    model: str | None = None
    parent_call_id: str | None = None
    kind: str = "llm"
    duration_sec: float | None = None
    complete: bool = True
    issues: Sequence[str] = ()


@dataclass(frozen=True)
class CaseUsage:
    case_id: str
    llm_called: bool | None
    observations: Sequence[UsageObservation] = ()
    # Missing requests / truncated traces make every metric a known subtotal.
    coverage_complete: bool = True
    issues: Sequence[str] = ()
    operations: Sequence[UsageOperation] = ()


@dataclass(frozen=True)
class FinalSummary:
    """Agent-native final summary, independent of submission eligibility or success."""

    finished: bool
    function_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    source: str


@dataclass(frozen=True)
class OriginalCase:
    """One explicitly selected attempt per case for the method's old policy."""

    case_id: str
    trace: Sequence[Mapping[str, Any]] | None
    patch: str | None
    final_summary: FinalSummary | None = None
    method_data: Mapping[str, Any] = field(default_factory=dict)


class OriginalAccounting(Protocol):
    def original_accounting(self, cases: Sequence[OriginalCase]) -> dict: ...


def _count(value: Any, name: str) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name}: expected a non-negative integer or None")
    return value


def normalize_openai_usage(raw: Mapping[str, Any]) -> dict[str, int | None]:
    """OpenAI Responses usage; absent details remain unknown, never default zero.

    Codex trace field mapping belongs to its adapter, not this API normalizer.
    """
    incoming = raw.get("input_tokens_details") or {}
    outgoing = raw.get("output_tokens_details") or {}
    if not isinstance(incoming, Mapping) or not isinstance(outgoing, Mapping):
        raise ValueError("usage details must be objects")
    metrics = {
        "input": raw.get("input_tokens"), "output": raw.get("output_tokens"),
        "cache_read": incoming.get("cached_tokens"),
        "cache_write": incoming.get("cache_write_tokens"),
        "reasoning": outgoing.get("reasoning_tokens"),
    }
    metrics = {key: _count(value, key) for key, value in metrics.items()}
    inp, out = metrics["input"], metrics["output"]
    metrics["total"] = inp + out if inp is not None and out is not None else None
    reported = _count(raw.get("total_tokens"), "total_tokens")
    if reported is not None and metrics["total"] is not None and reported != metrics["total"]:
        raise ValueError("reported total disagrees with input + output")
    read, write, reasoning = (metrics[key] for key in ("cache_read", "cache_write", "reasoning"))
    if inp is not None:
        if any(value is not None and value > inp for value in (read, write)):
            raise ValueError("cache detail exceeds input")
        if read is not None and write is not None:
            if read + write > inp:
                raise ValueError("cache read + write exceeds input")
            metrics["ordinary_input"] = inp - read - write
    if out is not None and reasoning is not None:
        if reasoning > out:
            raise ValueError("reasoning exceeds output")
        metrics["ordinary_output"] = out - reasoning
    return metrics


def corrected_accounting(cases: Sequence[CaseUsage]) -> dict:
    """Sum every known metric, using the same distinct-case denominator.

    Exact repeated observations are idempotent. Conflicting identities fail
    explicitly rather than silently choosing a value. Success is not an input.
    """
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("provide one CaseUsage per case, containing all attempts")
    names = list(METRICS)
    unique = {}
    for case in cases:
        if case.llm_called is not None and type(case.llm_called) is not bool:
            raise ValueError("llm_called must be True, False or None")
        if case.observations and case.llm_called is not True:
            raise ValueError("usage observations require a confirmed LLM call")
        for item in case.observations:
            if item.case_id != case.case_id:
                raise ValueError("observation belongs to another case")
            if not all((item.case_id, item.attempt_id, item.call_id, item.observation_id)):
                raise ValueError("usage identities must be non-empty")
            for key, value in item.metrics.items():
                _count(value, key)
                if key not in names:
                    names.append(key)
            inp, out = item.metrics.get("input"), item.metrics.get("output")
            total = item.metrics.get("total")
            if inp is not None and out is not None and total != inp + out:
                raise ValueError("normalized total must equal input + output")
            identity = (item.case_id, item.attempt_id, item.call_id, item.observation_id)
            previous = unique.get(identity)
            if previous and (dict(previous.metrics) != dict(item.metrics)
                             or dict(previous.issues) != dict(item.issues)
                             or (previous.purpose, previous.model, previous.parent_call_id, previous.operation_id)
                             != (item.purpose, item.model, item.parent_call_id, item.operation_id)):
                raise ValueError(f"conflicting usage identity: {identity}")
            unique.setdefault(identity, item)
    denominator = sum(case.llm_called is True for case in cases)
    unknown_cases = [case.case_id for case in cases if case.llm_called is None]
    metrics = {}
    for name in names:
        values = []
        reasons = []
        for case in cases:
            observations = [item for item in unique.values() if item.case_id == case.case_id]
            if case.llm_called is None:
                reasons.append(f"{case.case_id}: LLM call status unknown")
            if case.llm_called is True and not observations:
                reasons.append(f"{case.case_id}: no usage observations")
            if not case.coverage_complete:
                reasons.append(f"{case.case_id}: incomplete trace coverage")
            reasons.extend(f"{case.case_id}: {reason}" for reason in case.issues)
            for item in observations:
                value = item.metrics.get(name)
                if value is None:
                    reasons.append(f"{item.source}: {name} unknown")
                else:
                    values.append(value)
                if name in item.issues:
                    reasons.append(f"{item.source}: {item.issues[name]}")
        known_sum = sum(values) if values else (0 if not denominator and not reasons else None)
        metrics[name] = {
            "sum": known_sum,
            "mean": known_sum / denominator if known_sum is not None and denominator else None,
            "complete": not reasons,
            "reasons": list(dict.fromkeys(reasons)),
        }
    return {
        "rule": "corrected-v1", "cases_counted": denominator,
        "cases_selected": len(cases), "unknown_call_cases": unknown_cases,
        "metrics": metrics,
    }


def comparison_report(method: OriginalAccounting, original_cases: Sequence[OriginalCase],
                      cases: Sequence[CaseUsage]) -> dict:
    """JSON-ready two-policy report; neither policy overwrites AgentResult."""
    if not callable(getattr(method, "original_accounting", None)):
        raise ValueError("method must implement original_accounting")
    return {"original_token_accounting": method.original_accounting(original_cases),
            "corrected_token_accounting": corrected_accounting(cases)}


def render_accounting(report: Mapping[str, Any]) -> str:
    """Render every metric, including unknown and additional metrics."""
    original, corrected = (report[key] for key in
                           ("original_token_accounting", "corrected_token_accounting"))
    names = list(dict.fromkeys([*METRICS, *original["metrics"], *corrected["metrics"]]))
    lines = ["Metric | original token accounting | corrected token accounting",
             f"Cases counted | {original['cases_counted']} | {corrected['cases_counted']}"]
    for name in names:
        cells = []
        for accounting in (original, corrected):
            metric = accounting["metrics"].get(name)
            if metric is None:
                cells.append("not accounted")
                continue
            value = lambda item: "null" if item is None else str(item)
            cell = f"sum={value(metric['sum'])}, mean={value(metric['mean'])}"
            cell += ", complete" if metric["complete"] else ", incomplete"
            if metric.get("reasons"):
                cell += " (" + "; ".join(metric["reasons"]) + ")"
            cells.append(cell)
        lines.append(f"{name} | " + " | ".join(cells))
    context = report.get("accounting_context")
    if context:
        lines.append(f"Method version: {context.get('method_version')}; "
                     f"patch rules: {context.get('patch_rules')}; "
                     f"native compatibility: {context.get('original_agent_variant')}")
        if context.get("native_summary_sources"):
            lines.append("Native summary sources: " + "; ".join(context["native_summary_sources"]))
    if "overhead" in report:
        from .overhead import render_overhead
        lines.append(render_overhead(report["overhead"]))
    return "\n".join(lines)
