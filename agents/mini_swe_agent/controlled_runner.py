"""Optional query-boundary subclass, executed only in the prepared mini image."""

import json
from pathlib import Path
import time

from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.exceptions import LimitsExceeded


class TurnBudgetExceeded(LimitsExceeded):
    pass


class ControlledMini(InteractiveAgent):
    def __init__(self, *args, control_budget, control_path, **kwargs):
        super().__init__(*args, **kwargs)
        initial, final = control_budget["initial"], control_budget["final"]
        if type(initial) is not int or type(final) is not int or not 0 < initial <= final:
            raise ValueError("invalid turn budget")
        if self.config.mode != "yolo" or self.config.confirm_exit:
            raise ValueError("controlled mini requires --yolo --exit-immediately")
        self.control_path = Path(control_path)
        self.control = {"version": "mini-query-boundary-v1", "initial_budget": initial, "final_budget": final,
                        "active_budget": initial, "used_turns": 0, "extensions": [],
                        "termination_reason": "running"}
        self.save_control()

    def save_control(self, *, pending=False):
        self.control["used_turns"] = self.n_calls + int(pending)
        temporary = self.control_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.control, indent=2), encoding="utf-8")
        temporary.replace(self.control_path)
        with self.control_path.with_name('control-history.jsonl').open('a') as history:
            history.write(json.dumps(self.control) + '\n')

    def record_overhead(self, started):
        with self.control_path.with_name('timing.jsonl').open('a') as stream:
            stream.write(json.dumps({'version': 1, 'event': 'end', 'phase': 'control_callback',
                'utc_seconds': time.time(), 'seconds': time.perf_counter()-started}) + '\n')

    def query(self):
        # Delegate native limits unchanged; they may stop earlier than the turn budget.
        if (0 < self.config.step_limit <= self.n_calls
                or 0 < self.config.cost_limit <= self.cost
                or 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time)):
            return super().query()
        used = self.n_calls
        started = time.perf_counter()
        if used >= self.control["active_budget"]:
            if not self.control["extensions"] and self.control["final_budget"] > self.control["active_budget"]:
                self.control["extensions"].append({"after_turn": used,
                    "from": self.control["active_budget"], "to": self.control["final_budget"]})
                self.control["active_budget"] = self.control["final_budget"]
                self.add_messages(self.model.format_message(role="user", content=(
                    f"Your turn budget is extended once to {self.control['active_budget']}. "
                    "Continue in this same session; no further extension is available.")))
            else:
                self.record_overhead(started)
                raise TurnBudgetExceeded({"role": "exit", "content": "TurnBudgetExceeded",
                    "extra": {"exit_status": "TurnBudgetExceeded", "submission": ""}})
        active = self.control["active_budget"]
        self.add_messages(self.model.format_message(role="user", content=(
            f"Turn budget: this is turn {used + 1} of {active}. "
            f"You have {active - used} turns including this one. "
            "Complete the repository repair and any required Git commit, then use the native "
            "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT command when finished.")))
        self.save_control(pending=True)
        self.record_overhead(started)
        try:
            # The native method counts before model.query; retries/tools are not new turns.
            return super().query()
        finally:
            started = time.perf_counter()
            self.save_control()
            self.record_overhead(started)

    def run(self, *args, **kwargs):
        try:
            result = super().run(*args, **kwargs)
            status = result.get("exit_status")
            self.control.update(termination_reason={
                "Submitted": "completed", "TurnBudgetExceeded": "budget_exhausted",
                "LimitsExceeded": "native_limit", "TimeExceeded": "wall_time_limit",
            }.get(status, "execution_error"), success=status == "Submitted")
            return result
        except BaseException:
            self.control.update(termination_reason="interrupted_or_error", success=False)
            raise
        finally:
            self.control.update(tool_calls=sum(len(m.get("extra", {}).get("actions", []))
                                                for m in self.messages),
                                duration_sec=time.time() - self._start_time)
            self.save_control()
            # Preserve native trajectory serialization and final exit metadata.
            self.save(self.config.output_path)


if __name__ == "__main__":
    from minisweagent.run.mini import app
    app()
