"""Persist call/artifact associations before an agent can produce usage."""

from dataclasses import asdict
import json
from pathlib import Path
import time


def write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
    temporary.replace(path)


class RecordingWorkspace:
    def __init__(self, workspace, recorder, call):
        self.workspace, self.recorder, self.call = workspace, recorder, call

    def __getattr__(self, name):
        return getattr(self.workspace, name)

    @property
    def usage_identity(self):
        return {key: self.call[key] for key in ("case_id", "attempt_id", "call_id")}

    def new_artifacts(self):
        artifacts = self.workspace.new_artifacts()
        relative = artifacts.host.resolve().relative_to(self.recorder.directory)
        self.call["artifacts"].append(str(relative))
        self.recorder.save()
        return artifacts


class RecordingAgent:
    """A per-attempt wrapper; the original agent and its result are unchanged."""

    def __init__(self, agent, directory: Path, *, case_id=None):
        self.agent, self.directory = agent, directory.resolve()
        self.case_id = case_id or self.directory.parent.name
        self.calls = []
        self.save()

    def save(self):
        write_json(self.directory / "calls.json", {"version": 2, "calls": self.calls})

    def run(self, prompt, workspace, options):
        return self._record(prompt, workspace, options)

    def run_controlled(self, prompt, workspace, budget):
        return self._record(prompt, workspace, budget, controlled=True)

    def run_session(self, prompt, workspace, options, callback):
        return self._record(prompt, workspace, options, callback=callback)

    def _record(self, prompt, workspace, options, controlled=False, callback=None):
        call = {"id": f"call-{len(self.calls) + 1:04d}", "stage": "started",
                "artifacts": []}
        call.update(case_id=self.case_id, attempt_id=self.directory.name,
                    call_id=call["id"], kind="agent_session", started_at=time.time())
        started = time.monotonic()
        self.calls.append(call)
        self.save()
        try:
            invoke = self.agent.run_controlled if controlled else self.agent.run
            recorded = RecordingWorkspace(workspace, self, call)
            result = (self.agent.run_session(prompt, recorded, options, callback) if callback is not None
                      else invoke(prompt, recorded, options))
            write_json(self.directory / f"{call['id']}.json", asdict(result))
            call["stage"] = "returned"
            return result
        except BaseException as error:
            call.update(stage="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                        error_type=type(error).__name__)
            try:
                self.save()
            except OSError as save_error:
                error.add_note(f"call journal could not be updated: {type(save_error).__name__}")
            raise
        finally:
            call.update(finished_at=time.time(), wall_seconds=time.monotonic()-started)
            self.save()
