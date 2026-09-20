"""Original Codex analysis policy, separate from the unchanged agent caller."""

from typing import Sequence

from src.accounting import OriginalCase


def original_accounting(cases: Sequence[OriginalCase]) -> dict:
    """Match analysis/common/data_loader.py's Codex patch filter and floor mean.

    Callers supply the original patch.diff text, not a collected text fallback.
    No error/success/resolved filter is applied. One selected attempt per case.
    """
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("original accounting requires one selected attempt per case")
    selected = [case for case in cases if case.trace is not None and case.patch
                and case.patch.strip()]
    incoming = outgoing = 0
    for case in selected:
        for event in case.trace:
            # The original loader ignores malformed records and missing usage.
            try:
                if event.get("type") == "turn.completed":
                    usage = event.get("usage", {})
                    incoming += usage.get("input_tokens", 0)
                    outgoing += usage.get("output_tokens", 0)
            except (AttributeError, TypeError):
                continue
    count = len(selected)
    return {
        "rule": "run_free-codex-original-v1", "cases_counted": count,
        "metrics": {
            name: {"sum": value, "mean": value // count if count else None,
                   "complete": True, "reasons": []}
            for name, value in (("input", incoming), ("output", outgoing),
                                ("total", incoming + outgoing))
        },
        "note": "Original semantics: nonempty raw patch, trace present, floor mean; completeness means policy reproduction, not measured coverage.",
    }
