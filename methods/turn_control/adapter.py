"""Approved P50→P75 control and agent-independent native final summaries."""

from src.control import TurnBudget
from src.interfaces import MethodResult


BUDGETS = {"claude": (52, 64), "gemini": (29, 45), "gpt": (50, 67)}
BUDGETS.update({'deepswe_gpt': (53, 74), 'deepswe_claude': (91, 123),
                'deepswe_deepseek': (148, 185), 'deepswe_qwen': (102, 134)})


class TurnControl:
    version = "turn-control-native-summary-v3"
    original_reader = "read_final_summary_case"
    required_capabilities = ("turn_control",)
    resume_policy = "never_regenerate"
    original_trace_formats = ("agent-final-summary-v1",)

    def validate_options(self, options):
        if set(options) == {'frozen_budget'}:
            budget = options['frozen_budget']
            if not isinstance(budget, dict) or budget.get('algorithm') != 'linear-percentile-ceil' or not budget.get('baseline_id'):
                raise ValueError('frozen budget requires baseline identity and original percentile algorithm')
            TurnBudget(budget.get('initial'), budget.get('final'))
            return
        if set(options) != {"budget_profile"} or options["budget_profile"] not in BUDGETS:
            raise ValueError("turn_control requires a known budget_profile: " + ', '.join(BUDGETS))

    def run(self, task, agent, workspace, options):
        self.validate_options(options)
        frozen = options.get('frozen_budget')
        budget = TurnBudget(frozen['initial'], frozen['final']) if frozen else TurnBudget(*BUDGETS[options["budget_profile"]])
        return MethodResult(calls=[agent.run_controlled(task.problem_statement, workspace, budget)])

    def original_accounting(self, cases):
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("final-summary accounting requires one selected attempt per case")
        selected = [case.final_summary for case in cases if case.final_summary is not None
                    and case.final_summary.finished and case.final_summary.function_calls is not None]
        from src.accounting_trace import atom, calc, metric
        metrics = {}
        for key in ("input", "output", "total"):
            values = []
            for summary in selected:
                incoming, outgoing = summary.input_tokens, summary.output_tokens
                if any(value is not None and (type(value) is not int or value < 0)
                       for value in (incoming, outgoing, summary.function_calls)):
                    raise ValueError("invalid native final summary counts")
                a = summary.calculation.get('input') or atom(incoming, summary.source, 'input_tokens', description='原最终摘要输入')
                b = summary.calculation.get('output') or atom(outgoing, summary.source, 'output_tokens', description='原最终摘要输出')
                values.append(a if key == 'input' else b if key == 'output' else calc('sum', a, b))
            known = [value for value in values if value['value'] is not None]
            complete = len(known) == len(values)
            total = calc('sum', *known, description='原最终摘要累加') if known or not selected else atom(None, 'src/accounting_trace.py', 'missing', kind='missing')
            metrics[key] = metric(total, complete=complete, mean_defined=False,
                reasons=[] if complete else ['native final summary has missing token usage'])
        return {"rule": "turn-control-final-summary-compatible-v1", "cases_counted": len(selected),
                "metrics": metrics,
                "note": "Latest final native summary with function-call count, no patch/success filter. "
                        "Single-attempt native-agent compatibility, not historical reproduction. "
                        "Original means intentionally null; original scripts report sums only."}
