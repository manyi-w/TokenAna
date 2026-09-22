"""Unmodified agent execution, without a token-saving method or callback."""

from src.interfaces import MethodResult
from src.method_sessions import source_metrics


class Baseline:
    version = "baseline-native-v1"
    original_trace_formats = ("codex-jsonl",)
    resume_policy = "never_regenerate"

    def validate_options(self, options):
        if options:
            raise ValueError("baseline has no method options")

    def run(self, task, agent, workspace, options):
        self.validate_options(options)
        return MethodResult(calls=[agent.run(task.problem_statement, workspace, {})])

    def original_accounting(self, cases):
        report = source_metrics(cases, rule="baseline-native-v1")
        report["note"] = ("Native agent usage reference, not a saving-method author's rule. "
                          "Method-specific baseline projections are separate analyses.")
        return report
