"""Native session contract with in-memory messages only."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from src.session import Message, Session, SessionDecision, validate_replacement
from src.agents import BoundAgent


class SessionTests(unittest.TestCase):
    def history(self):
        return [Message('system', 'system', 'rules'),
                Message('a', 'assistant', '', {'arguments': {'x': 1}}, True, ('t1', 't2')),
                Message('r1', 'tool', 'large result', {}, True, tool_result='t1'),
                Message('r2', 'tool', 'second result', {}, True, tool_result='t2')]

    def test_context_state_actual_input_and_finish(self):
        history = self.history()
        def callback(event):
            if event.kind == 'before_model':
                changed = list(event.history)
                changed[2] = replace(changed[2], text='small')
                return SessionDecision(changed, {'compressed': True}, 'remaining: 2')
        with tempfile.TemporaryDirectory() as root:
            session = Session(callback, Path(root) / 'session')
            session.emit('initialize', history)
            decision = session.emit('before_model', history)
            self.assertEqual(decision.history[2].text, 'small')
            self.assertEqual(history[2].text, 'large result')
            self.assertTrue(session.state['compressed'])
            payload = {'messages': [m.text for m in decision.history] + [decision.reminder]}
            session.record_model_input(payload)
            self.assertEqual(json.loads((session.directory / '000002-model-input.json').read_text()), payload)
            session.emit('after_model', decision.history)
            session.emit('after_tool', decision.history)
            session.emit('finish', decision.history)
            with self.assertRaises(ValueError):
                session.emit('before_model', decision.history)

    def test_pairs_protected_fields_and_atomic_step_removal(self):
        before = self.history()
        validate_replacement(before, [before[0]])
        for after in (before[:3], [before[0], before[2]], before[1:],
                      [before[0], replace(before[1], native={'arguments': {'x': 2}}), *before[2:]],
                      [replace(before[0], text='rewritten'), *before[1:]],
                      [*before, before[-1]], [before[0], before[3], before[1], before[2]]):
            with self.subTest(after=after), self.assertRaises(ValueError):
                validate_replacement(before, after)

    def test_callback_mutation_cannot_bypass_validation(self):
        def callback(event):
            event.history[1].native['arguments']['x'] = 99
            return SessionDecision(event.history)
        with tempfile.TemporaryDirectory() as root:
            session = Session(callback, Path(root) / 's')
            with self.assertRaisesRegex(ValueError, 'immutable'):
                session.emit('initialize', self.history())
            record = json.loads((session.directory / '000001.json').read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['event']['history'][1]['native']['arguments']['x'], 1)

    def test_termination_blocks_further_model_input(self):
        with tempfile.TemporaryDirectory() as root:
            session = Session(lambda e: SessionDecision(terminate='budget') if e.kind == 'before_model' else None,
                              Path(root) / 's')
            session.emit('initialize', self.history())
            self.assertEqual(session.emit('before_model', self.history()).terminate, 'budget')
            with self.assertRaises(ValueError):
                session.record_model_input({})
            with self.assertRaises(ValueError):
                session.emit('before_model', self.history())
            session.emit('finish', self.history())

    def test_missing_native_capability_never_falls_back(self):
        class Legacy:
            def run(self, *args):
                raise AssertionError('must not run')
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            BoundAgent(Legacy(), {}).run_session('test', object(), {}, lambda e: None)

    def test_recorded_bound_session_and_capability_preflight(self):
        from src.capabilities import accounting_plan
        from src.interfaces import AgentResult, ArtifactDirectory
        from src.records import RecordingAgent
        from src.session import CAPABILITIES
        class Method:
            required_session_capabilities = CAPABILITIES
        self.assertEqual(accounting_plan(Method(), object(), {})['status'], 'blocked')
        class Native:
            session_capabilities = CAPABILITIES
            def run_session(self, prompt, workspace, options, callback):
                artifacts = workspace.new_artifacts()
                session = Session(callback, artifacts.host / 'session')
                history = [Message('user', 'user', prompt)]
                session.emit('initialize', history)
                decision = session.emit('before_model', history)
                session.record_model_input({'messages': [m.text for m in decision.history]})
                session.emit('after_model', decision.history)
                session.emit('finish', decision.history)
                return AgentResult('fake', prompt, 'done', 0, 0, 0, [], artifacts=artifacts)
        class Workspace:
            def __init__(self, root):
                self.root = root
            def new_artifacts(self):
                target = self.root / 'artifacts/call-1'
                target.mkdir(parents=True)
                return ArtifactDirectory(target, '/out/call-1')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            recorder = RecordingAgent(BoundAgent(Native(), {}), root, case_id='case')
            result = recorder.run_session('question', Workspace(root), {}, lambda e: None)
            self.assertEqual(result.output, 'done')
            saved = json.loads((root / 'calls.json').read_text())
            self.assertEqual(saved['calls'][0]['case_id'], 'case')
            self.assertEqual(saved['calls'][0]['stage'], 'returned')
            self.assertEqual(saved['version'], 2)
