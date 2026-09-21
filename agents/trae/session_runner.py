"""Native Trae client/agent subclasses; no upstream method replacement."""
import asyncio
import json
import os
from pathlib import Path
import sys

from controlled_runner import (SelectedTrae, ControlledTrae, SelectedConsole,
    CachedAnthropic, DirectChatProvider, Config, TrajectoryRecorder, AgentState)
from trae_agent.utils.llm_clients.openai_client import OpenAIClient
from trae_agent.utils.llm_clients.openai_compatible_base import OpenAICompatibleClient
from tokenana_session_channel import SessionClient
from tokenana_mini_mapping import NativeHistory


class SessionClientMixin:
    def __init__(self, *args, channel, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel, self.mapping = channel, NativeHistory()
        self.initialized = False
        self.sampling = False
        self.reminders = []

    def event(self, kind, **details):
        result = self.channel.request(kind, history=self.mapping.encode(self.message_history), details=details)
        self.message_history[:] = self.mapping.decode(result['history'])
        if result.get('reminder'):
            self.reminders.append(result['reminder'])
        if result.get('terminate') and kind != 'finish':
            raise RuntimeError('TOKENANA_METHOD_TERMINATED')

    def before(self):
        if self.sampling:  # Native retry belongs to the same agent step.
            return
        if not self.initialized:
            self.event('initialize')
            self.initialized = True
        self.event('before_model')
        self.message_history.extend({'role': 'user', 'content': text} for text in self.reminders)
        self.reminders.clear()
        self.channel.request('model_input', request={'messages': self.message_history,
                             'note': 'Native client input; exact wire payload in api-records'})
        self.sampling = True

    def chat(self, *args, **kwargs):
        self.sampling = False
        try:
            result = super().chat(*args, **kwargs)
        except BaseException as error:
            if self.initialized:
                self.event('after_model', error_type=type(error).__name__)
            raise
        self.event('after_model')
        return result


class SessionOpenAI(SessionClientMixin, OpenAIClient):
    def _create_openai_response(self, api_call_input, model_config, tool_schemas):
        self.before()
        return super()._create_openai_response(self.message_history, model_config, tool_schemas)


class SessionAnthropic(SessionClientMixin, CachedAnthropic):
    def _create_anthropic_response(self, model_config, tool_schemas):
        self.before()
        return super()._create_anthropic_response(model_config, tool_schemas)


class SessionChat(SessionClientMixin, OpenAICompatibleClient):
    def _create_response(self, *args, **kwargs):
        self.before()
        return super()._create_response(*args, **kwargs)


class SessionTraeMixin:
    def __init__(self, *args, channel, system_suffix='', **kwargs):
        self.tokenana_system_suffix = system_suffix
        super().__init__(*args, **kwargs)
        config = self._model_config
        if os.environ['TOKENANA_PROTOCOL'] == 'anthropic_messages':
            client = SessionAnthropic(config, channel=channel)
        elif os.environ['TOKENANA_PROTOCOL'] == 'chat_completions':
            client = SessionChat(config, DirectChatProvider(), channel=channel)
        else:
            client = SessionOpenAI(config, channel=channel)
        self._llm_client.client = client

    def get_system_prompt(self):
        return super().get_system_prompt() + self.tokenana_system_suffix

    async def _finalize_step(self, step, messages, execution):
        await super()._finalize_step(step, messages, execution)
        client = self._llm_client.client
        client.message_history.extend(client.parse_messages(messages))
        messages.clear()  # Already incorporated; do not append twice on next chat.
        if client.initialized:
            client.event('after_tool')

    async def execute_task(self):
        try:
            return await super().execute_task()
        finally:
            client = self._llm_client.client
            if client.initialized:
                client.event('finish')


class SessionTrae(SessionTraeMixin, SelectedTrae):
    pass


class ControlledSessionTrae(SessionTraeMixin, ControlledTrae):
    pass


async def run(directory, root):
    config = Config.create(config_file=str(directory / 'config.yaml')).resolve_config_values()
    request = json.loads((directory / 'control-request.json').read_text())
    budget = request.get('budget')
    channel = SessionClient(directory / 'session-channel', request.get('session_timeout', 60))
    extra = {'channel': channel, 'system_suffix': request.get('eet_system_suffix', '')}
    agent = (ControlledSessionTrae(config.trae_agent, budget, directory / 'control.json', **extra)
             if budget else SessionTrae(config.trae_agent, **extra))
    console = SelectedConsole(config.lakeview)
    agent.set_cli_console(console)
    agent.set_trajectory_recorder(TrajectoryRecorder(str(directory / 'trajectory.json')))
    prompt = (directory / 'prompt.txt').read_text()
    os.chdir(root)
    agent.new_task(prompt, {'project_path': root, 'issue': prompt, 'must_patch': 'false',
                           'patch_path': str(directory / 'native.patch.diff')})
    try:
        execution = await agent.execute_task()
        if execution.agent_state not in (AgentState.COMPLETED, AgentState.ERROR):
            execution.agent_state = AgentState.ERROR
        await console.start()
        if budget:
            agent.control.update(termination_reason='completed' if execution.success else
                'budget_exhausted' if agent.control['used_turns'] >= agent.max_steps else 'execution_error',
                success=execution.success, tool_calls=sum(len(step.tool_calls or []) for step in execution.steps),
                duration_sec=execution.execution_time)
    finally:
        if budget:
            agent.save_control()


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
