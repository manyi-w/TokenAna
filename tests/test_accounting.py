"""Offline accounting contracts; no agents, containers or model services."""

from dataclasses import replace
import json
import unittest

from methods.run_free.adapter import RunFree
from src.accounting import (CaseUsage, OriginalCase, UsageObservation,
                            comparison_report, corrected_accounting,
                            normalize_openai_usage, render_accounting)


def usage(case, attempt="1", response="r1", *, inp=100, out=20):
    raw = {"input_tokens": inp, "output_tokens": out,
           "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
           "output_tokens_details": {"reasoning_tokens": 5}}
    return UsageObservation(case, attempt, "call1", response,
                            normalize_openai_usage(raw), f"{case}/{attempt}/trace:1", raw)


class AccountingTests(unittest.TestCase):
    def test_all_cases_attempts_and_details(self):
        success = usage("success")
        failed = usage("failed")
        retry = usage("failed", "2")
        # No success/patch field is available to the corrected policy.
        result = corrected_accounting([
            CaseUsage("success", True, [success]),
            CaseUsage("failed", True, [failed, retry, retry]),
            CaseUsage("not_started", False),
        ])
        self.assertEqual(result["cases_counted"], 2)
        expected = {"total": 360, "input": 300, "output": 60,
                    "cache_read": 120, "cache_write": 30, "reasoning": 15,
                    "ordinary_input": 150, "ordinary_output": 45}
        for key, value in expected.items():
            self.assertEqual(result["metrics"][key],
                             {"sum": value, "mean": value / 2,
                              "complete": True, "reasons": []})

    def test_missing_details_do_not_erase_known_total(self):
        raw = {"input_tokens": 100, "output_tokens": 20}
        item = replace(usage("a"), metrics=normalize_openai_usage(raw), raw_usage=raw)
        result = corrected_accounting([CaseUsage("a", True, [item])])["metrics"]
        self.assertTrue(result["total"]["complete"])
        self.assertEqual(result["total"]["sum"], 120)
        for key in ("cache_read", "cache_write", "reasoning"):
            self.assertIsNone(result[key]["sum"])
            self.assertIsNone(result[key]["mean"])
            self.assertFalse(result[key]["complete"])

    def test_partial_failed_case_keeps_denominator(self):
        result = corrected_accounting([
            CaseUsage("a", True, [usage("a")]),
            CaseUsage("timeout", True, coverage_complete=False, issues=["truncated"]),
            CaseUsage("unknown", None),
        ])
        self.assertEqual(result["cases_counted"], 2)
        self.assertEqual(result["unknown_call_cases"], ["unknown"])
        self.assertEqual(result["metrics"]["input"]["mean"], 50)
        for metric in result["metrics"].values():
            self.assertFalse(metric["complete"])
            self.assertTrue(metric["reasons"])

    def test_zero_usage_and_zero_denominator(self):
        zeros = UsageObservation("a", "1", "1", "r", dict.fromkeys(
            ("input", "output", "total", "cache_read", "cache_write", "reasoning"), 0), "trace:1")
        result = corrected_accounting([CaseUsage("a", True, [zeros])])
        self.assertEqual(result["cases_counted"], 1)
        self.assertEqual(result["metrics"]["total"]["mean"], 0)
        empty = corrected_accounting([CaseUsage("b", False)])
        self.assertIsNone(empty["metrics"]["total"]["mean"])
        self.assertEqual(empty["metrics"]["total"]["sum"], 0)

    def test_conflicting_duplicate_and_scope_validation(self):
        item = usage("a")
        with self.assertRaisesRegex(ValueError, "conflicting"):
            corrected_accounting([CaseUsage("a", True, [item, usage("a", inp=110)])])
        with self.assertRaisesRegex(ValueError, "another case"):
            corrected_accounting([CaseUsage("b", True, [item])])
        with self.assertRaisesRegex(ValueError, "one CaseUsage"):
            corrected_accounting([CaseUsage("a", False), CaseUsage("a", False)])
        with self.assertRaisesRegex(ValueError, "confirmed"):
            corrected_accounting([CaseUsage("a", False, [item])])

    def test_additional_metric_and_metric_specific_coverage(self):
        item = usage("a")
        item = replace(item, metrics={**item.metrics, "audio_input": 7},
                       issues={"cache_write": "agent may default missing API field to zero"})
        result = corrected_accounting([CaseUsage("a", True, [item])])
        self.assertEqual(result["metrics"]["audio_input"]["sum"], 7)
        self.assertTrue(result["metrics"]["total"]["complete"])
        self.assertFalse(result["metrics"]["cache_write"]["complete"])

    def test_invalid_usage_is_not_silently_normalized(self):
        for raw in (
            {"input_tokens": True}, {"output_tokens": -1},
            {"input_tokens": 1, "output_tokens": 2, "total_tokens": 4},
            {"input_tokens": 1, "input_tokens_details": {"cached_tokens": 2}},
            {"output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 2}},
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_openai_usage(raw)

    def test_original_filter_floor_mean_and_full_comparison(self):
        def trace(inp, out):
            return [{"type": "turn.completed", "usage": {
                "input_tokens": inp, "output_tokens": out, "cached_input_tokens": 40}}]
        original = [OriginalCase("a", trace(100, 20), "diff"),
                    OriginalCase("b", trace(101, 21), " diff "),
                    OriginalCase("failed", trace(100, 20), " \n"),
                    OriginalCase("missing", None, "diff")]
        corrected = [CaseUsage("a", True, [usage("a")]),
                     CaseUsage("b", True, [usage("b", inp=101, out=21)]),
                     CaseUsage("failed", True, [usage("failed")]),
                     CaseUsage("missing", None)]
        report = comparison_report(RunFree(), original, corrected)
        old = report["original_token_accounting"]
        self.assertEqual(old["cases_counted"], 2)
        self.assertEqual(old["metrics"]["total"]["sum"], 242)
        self.assertEqual(old["metrics"]["input"]["mean"], 100)
        self.assertEqual(report["corrected_token_accounting"]["metrics"]["total"]["sum"], 362)
        self.assertEqual(json.loads(json.dumps(report)), report)
        rendered = render_accounting(report)
        for label in ("original token accounting", "corrected token accounting",
                      "input |", "output |", "cache_read |", "cache_write |", "reasoning |",
                      "incomplete", "not accounted"):
            self.assertIn(label, rendered)

    def test_contract_and_original_empty(self):
        with self.assertRaisesRegex(ValueError, "original_accounting"):
            comparison_report(object(), [], [])
        original = RunFree().original_accounting([])
        self.assertIsNone(original["metrics"]["total"]["mean"])
        item = OriginalCase("a", [], "diff")
        with self.assertRaises(ValueError):
            RunFree().original_accounting([item, item])


if __name__ == "__main__":
    unittest.main()
