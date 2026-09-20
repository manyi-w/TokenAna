"""Optional native mini subclass; imported only in a prepared execution environment."""
from copy import deepcopy
import json
from pathlib import Path
import time

# When staged in the artifact directory these imports resolve to hand-written copies.
try:
    from tokenana_session_channel import SessionClient
    from tokenana_mini_mapping import NativeHistory
except ModuleNotFoundError:
    from src.session_channel import SessionClient
    from agents.mini_swe_agent.session_mapping import NativeHistory


class SessionMiniMixin:
    def __init__(self, *args, session_path, session_timeout=60, **kwargs):
        self.session_client = SessionClient(session_path, session_timeout)
        self.native_history = NativeHistory()
        self.original_messages = []
        self.session_started = False
        self.session_finished = False
        self.pending_reminders = []
        super().__init__(*args, **kwargs)
        if self.config.mode != 'yolo' or self.config.confirm_exit:
            raise ValueError('session mini requires --yolo --exit-immediately')

    def add_messages(self, *messages):
        self.original_messages.extend(deepcopy(messages))
        return super().add_messages(*messages)

    def _event(self, kind, **details):
        decision = self.session_client.request(kind, history=self.native_history.encode(self.messages),
                                               details={'used_turns': self.n_calls, **details})
        self.messages = self.native_history.decode(decision['history'])
        if decision.get('reminder'):
            self.pending_reminders.append(decision['reminder'])
        if decision.get('terminate') and kind != 'finish':
            raise self.session_stop({'role': 'exit', 'content': 'MethodTerminated',
                'extra': {'exit_status': 'MethodTerminated', 'submission': '',
                          'method_reason': decision['terminate']}})

    def query(self):
        if not self.session_started:
            self.session_started = True
            self._event('initialize')
        if (0 < self.config.step_limit <= self.n_calls
                or 0 < self.config.cost_limit <= self.cost
                or 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time)):
            return super().query()
        self._event('before_model')
        # Reminders after a tool call are deferred until all native results are present.
        for reminder in self.pending_reminders:
            self.add_messages(self.model.format_message(role='user', content=reminder))
        self.pending_reminders.clear()
        prepared = self.model._prepare_messages_for_api(deepcopy(self.messages))
        self.session_client.request('model_input', request={
            'model': self.model.config.model_name, 'prepared_messages': prepared,
            'note': 'Native prepared model input; exact SDK wire request is in api-records/request.body'})
        try:
            response = super().query()
        except BaseException as error:
            self._event('after_model', error_type=type(error).__name__)
            raise
        self._event('after_model')
        return response

    def execute_actions(self, message):
        try:
            return super().execute_actions(message)
        finally:
            # Native InteractiveAgent formats all outputs (including unexecuted tools)
            # in its finally block before this hook runs; do not re-execute any tool.
            self._event('after_tool')

    def save(self, path, *extra):
        data = super().save(path, *extra)
        if path:
            original = deepcopy(data)
            original['messages'] = deepcopy(self.original_messages)
            target = Path(path).with_name('original-trajectory.json')
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(original), encoding='utf-8')
            temporary.replace(target)
        return data

    def run(self, *args, **kwargs):
        try:
            return super().run(*args, **kwargs)
        finally:
            if self.session_started:
                self._event('finish')
                self.session_finished = True
            self.save(self.config.output_path)


def make_agent_classes():
    from minisweagent.agents.interactive import InteractiveAgent
    from minisweagent.exceptions import LimitsExceeded
    from tokenana_mini_control import ControlledMini

    class MethodTerminated(LimitsExceeded):
        pass
    class SessionMini(SessionMiniMixin, InteractiveAgent):
        session_stop = MethodTerminated
    class ControlledSessionMini(ControlledMini, SessionMiniMixin, InteractiveAgent):
        session_stop = MethodTerminated

        def run(self, *args, **kwargs):
            try:
                return super().run(*args, **kwargs)
            finally:
                self.control['tool_calls'] = sum(len(m.get('extra', {}).get('actions', []))
                                                 for m in self.original_messages)
                self.save_control()
    return SessionMini, ControlledSessionMini


# The native CLI dynamically imports this module to resolve --agent-class.
if __name__ == 'tokenana_mini_session':
    SessionMini, ControlledSessionMini = make_agent_classes()

if __name__ == '__main__':
    from minisweagent.run.mini import app
    app()
