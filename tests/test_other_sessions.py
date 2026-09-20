"""Hand-written Trae/OpenCode/Codex boundaries tested without native dependencies."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import tempfile
import unittest

from src.interfaces import ArtifactDirectory
from src.session import Message, SessionDecision, StepSummary
from src.session_channel import SessionClient, session_server
from src.source_declarations import declarations
from agents.mini_swe_agent.session_mapping import NativeHistory
from agents.trae.adapter import Trae, _config
from agents.codex.adapter import Codex

ROOT = Path(__file__).resolve().parents[1]


class OtherSessionTests(unittest.TestCase):
    def test_native_wire_roundtrip_keeps_ids_and_tool_pairs(self):
        history = [
            {'type': 'message', 'role': 'system', 'content': [{'type': 'input_text', 'text': 'rules'}]},
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'task'}]},
            {'type': 'function_call', 'call_id': 'a', 'name': 'bash', 'arguments': '{"command":"cat x"}'},
            {'type': 'function_call', 'call_id': 'b', 'name': 'bash', 'arguments': '{"command":"cat y"}'},
            {'type': 'function_call_output', 'call_id': 'a', 'output': 'large x'},
            {'type': 'function_call_output', 'call_id': 'b', 'output': 'large y'}]
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            def compress(event):
                if event.kind == 'before_model':
                    return SessionDecision(history=[replace(m, text='short') if m.tool_result else m for m in event.history])
            with session_server(ArtifactDirectory(root, str(root)), compress):
                client = SessionClient(root / 'session-channel')
                first = client.request('initialize', native_history=history)
                changed = client.request('before_model', native_history=first['native_history'])
                self.assertEqual(changed['native_history'][-1]['output'], 'short')
                client.request('model_input', request=changed['native_history'])
                client.request('finish', native_history=changed['native_history'])
            events = [json.loads(p.read_text()) for p in sorted((root / 'session').glob('*.json')) if 'model-input' not in p.name]
            self.assertEqual([m['id'] for m in events[0]['event']['history']], [m['id'] for m in events[1]['event']['history']])
            self.assertTrue(json.loads((root / 'session-outcome.json').read_text())['complete'])

    def test_anthropic_separate_calls_and_result_text_roundtrip(self):
        native = [{'role': 'user', 'content': 'task'}, {'role': 'assistant', 'content': 'thinking'},
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'a', 'name': 'bash', 'input': {'command': 'cat a'}}]},
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'b', 'name': 'bash', 'input': {'command': 'cat b'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a', 'content': 'first'}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'b', 'content': 'second'}]}]
        mapper = NativeHistory()
        mapped = mapper.encode(native)
        from src.session import validate_history
        validate_history([Message(**{**m, 'tool_calls': tuple(m['tool_calls'])}) for m in mapped])
        mapped[-1]['text'] = 'compressed'
        result = mapper.decode(mapped)
        self.assertEqual(result[-1]['content'][0]['content'], 'compressed')
        self.assertEqual(result[:-1], native[:-1])

    def test_trae_client_uses_actual_replaced_history_once_per_retry(self):
        class Native:
            def __init__(self):
                self.message_history = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'task'},
                                        {'role': 'assistant', 'content': 'large context'}]
            def _create_response(self):
                return [dict(m) for m in self.message_history]
            def chat(self):
                first = self._create_response()
                second = self._create_response()
                return first, second
        ns = declarations(ROOT / 'agents/trae/session_runner.py', ['SessionClientMixin', 'SessionChat'],
                          {'NativeHistory': NativeHistory, 'OpenAICompatibleClient': Native})
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            def callback(event):
                if event.kind == 'before_model':
                    return SessionDecision(history=[replace(m, text='compressed') if m.editable else m for m in event.history])
            with session_server(ArtifactDirectory(root, str(root)), callback):
                client = ns['SessionChat'](channel=SessionClient(root / 'session-channel'))
                first, second = client.chat()
                client.event('finish')
            self.assertEqual(first, second)
            self.assertEqual(first[-1]['content'], 'compressed')
            self.assertEqual(len(list((root / 'session').glob('*model-input.json'))), 1)

    def test_trae_lakeview_has_distinct_explicit_channel(self):
        options = {'model_provider':'openai', 'model':'fixture'}
        config = _config(options, 'http://main', 'http://aux')
        self.assertEqual(config['models']['lakeview']['model_provider'], 'selected_lakeview')
        self.assertEqual(config['model_providers']['selected_lakeview']['base_url'], 'http://aux')

    def test_missing_native_executors_fail_closed(self):
        with self.assertRaises(ValueError):
            Codex().validate_session_options({'record_raw_usage': True})
        with self.assertRaises(ValueError):
            Trae().validate_session_options({'record_raw_usage': True})

    def test_opencode_plugin_mapping_and_actual_transform(self):
        # Execute only our dependency-free plugin in Node, with an in-memory native client
        # and the real host file channel. No OpenCode binary, SDK, ports or models.
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'session-request.json').write_text('{"timeout":5}')
            plugin = (ROOT / 'agents/opencode/session.mjs').as_uri()
            script = f'''import plugin from {json.dumps(plugin)};
const user = {{info:{{id:"u",role:"user",sessionID:"s"}},parts:[{{id:"ut",messageID:"u",sessionID:"s",type:"text",text:"task"}}]}};
const assistant = {{info:{{id:"a",role:"assistant",sessionID:"s",agent:"build",time:{{completed:1}}}},parts:[{{id:"tool",messageID:"a",sessionID:"s",type:"tool",callID:"call",tool:"bash",state:{{status:"completed",input:{{command:"cat x"}},output:"large context"}}}}]}};
const pending = {{info:{{id:"next",role:"assistant",sessionID:"s",agent:"build",time:{{}}}},parts:[]}};
const client = {{session:{{get:async()=>({{data:{{id:"s"}}}}), messages:async()=>({{data:[user,assistant,pending]}})}}}};
const hooks = await plugin({{client}});
const output = {{messages:[user,assistant]}};
await hooks["experimental.chat.messages.transform"]({{}}, output);
if (output.messages[1].parts[0].state.output !== "short") throw new Error("history not replaced");
await hooks["experimental.chat.messages.transform"]({{}}, {{messages:[user,assistant]}});
const headers = {{headers:{{}}}};
await hooks["chat.headers"]({{sessionID:"s",agent:"build"}}, headers);
if (headers.headers["x-tokenana-purpose"] !== "main") throw new Error("wrong attribution");
await hooks.event({{event:{{type:"session.idle",properties:{{sessionID:"s"}}}}}});
'''
            def callback(event):
                if event.kind == 'before_model':
                    return SessionDecision(history=[replace(m, text='short') if m.tool_result else m for m in event.history])
            with session_server(ArtifactDirectory(root, str(root)), callback):
                result = subprocess.run(['node', '--input-type=module', '-e', script],
                    env={**os.environ, 'TOKENANA_SESSION_DIRECTORY': str(root)}, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads((root / 'session-outcome.json').read_text())['complete'])


if __name__ == '__main__':
    unittest.main()
