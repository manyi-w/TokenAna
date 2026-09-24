"""Framework accounting over explicitly normalized, disjoint usage observations.

Trace adapters select non-overlapping observations (response deltas OR a session
aggregate), preserve raw files, and report coverage gaps. This module never guesses
trace semantics, retries a model, or substitutes a caller's estimated token count.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


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
    billing_context: Mapping[str, Any] = field(default_factory=dict)
    calculation: Mapping[str, Any] = field(default_factory=dict)


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
    inference: bool = True
    billing_context: Mapping[str, Any] = field(default_factory=dict)


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
    calculation: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OriginalCase:
    """One explicitly selected attempt per case for the method's old policy."""

    case_id: str
    trace: Sequence[Mapping[str, Any]] | None
    patch: str | None
    final_summary: FinalSummary | None = None
    method_data: Mapping[str, Any] = field(default_factory=dict)


def _count(value: Any, name: str) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name}: expected a non-negative integer or None")
    return value


def normalize_openai_usage(raw: Mapping[str, Any]) -> dict[str, int | None]:
    from .usage_protocols import normalize_usage
    return normalize_usage(raw, 'responses')


def _validated_observations(cases):
    """Validate and index evidence without calculating either accounting policy."""
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("provide one CaseUsage per case, containing all attempts")
    names = list(METRICS)
    by_case = {}
    for case in cases:
        unique = by_case[case.case_id] = {}
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
            if previous and {
                    k: v for k, v in asdict(previous).items() if k not in ('source', 'calculation')} != {
                    k: v for k, v in asdict(item).items() if k not in ('source', 'calculation')}:
                raise ValueError('Conflicting response evidence')
            unique.setdefault(identity, item)
    return names, by_case


def render_accounting(report: Mapping[str, Any]) -> str:
    """Render every metric, including unknown and additional metrics."""
    original, corrected = (report[key] for key in
                           ("original_token_accounting", "corrected_v2_api"))
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
    from .pricing import render_cost
    lines.append(render_cost(report.get('corrected_v2_cost')))
    return "\n".join(lines)
