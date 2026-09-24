"""Original Codex analysis policy, separate from the unchanged agent caller."""

from typing import Sequence

from src.accounting import OriginalCase
from src.accounting_trace import calc, metric, case_source, event_source


def original_accounting(cases: Sequence[OriginalCase]) -> dict:
    """Match analysis/common/data_loader.py's Codex patch filter and floor mean.

    Callers supply the original patch.diff text, not a collected text fallback.
    No error/success/resolved filter is applied. One selected attempt per case.
    """
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("original accounting requires one selected attempt per case")
    selected = [case for case in cases if case.trace is not None and case.patch
                and case.patch.strip()]
    incoming, outgoing = [], []
    for case in selected:
        for index, event in enumerate(case.trace):
            # The original loader ignores malformed records and missing usage.
            try:
                if event.get("type") == "turn.completed":
                    usage = event.get("usage", {})
                    0 + usage.get("input_tokens", 0)
                    incoming.append(event_source(case, index, event, "input_tokens"))
                    0 + usage.get("output_tokens", 0)
                    outgoing.append(event_source(case, index, event, "output_tokens"))
            except (AttributeError, TypeError):
                continue
    count = len(selected)
    denominator = calc('count', *(case_source(c, 'case_id', c.case_id, kind='selection') for c in selected), description='原补丁/轨迹筛选后的任务数')
    incoming, outgoing = calc('sum', *incoming, description='原 input 累加'), calc('sum', *outgoing, description='原 output 累加')
    return {
        "rule": "run_free-codex-original-v1", "cases_counted": count,
        "metrics": {name: metric(value, denominator, floor=True) for name, value in
                    (("input", incoming), ("output", outgoing), ("total", calc('sum', incoming, outgoing, description='原 input + output')))},
        "selection": [dict(case_id=c.case_id, included=c in selected,
            reason='轨迹存在且原补丁非空' if c in selected else '原规则排除缺少轨迹或空补丁') for c in cases],
        "note": "Original semantics: nonempty raw patch, trace present, floor mean; completeness means policy reproduction, not measured coverage.",
    }
