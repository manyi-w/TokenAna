import json
from pathlib import Path
import tempfile
import unittest
from src.content_diagnostics import inspect_requests, tool_category
from src.telemetry import intervals
from src.local_compute import validate_backend_telemetry


class EvidenceTests(unittest.TestCase):
    def test_structural_repeat_and_unknown_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(2):
                p = root/'api-records'/str(i)
                p.mkdir(parents=True)
                (p/'metadata.json').write_text(json.dumps({'started_at': i, 'forwarded_at': i}))
                (p/'request.body').write_text(json.dumps({'messages': [
                    {'id': 'task', 'role': 'user', 'content': 'Fix a bug'},
                    {'role': 'assistant', 'tool_calls': [{'id': 't', 'function': {'name': 'read_file', 'arguments': '{"path":"a"}'}}]},
                    {'role': 'tool', 'tool_call_id': 't', 'content': 'class A: pass'}]}))
            data = inspect_requests(root)
            self.assertEqual(data['requests'][0]['repeated_characters'], 0)
            self.assertGreater(data['requests'][1]['repeated_characters'], 0)
            self.assertIn('repository_code', [r['category'] for r in data['segments']])
            self.assertNotIn('tokens', data['segments'][0])
        self.assertEqual(tool_category('bash', {'command': 'cat x | python y'}), 'unclassified')

    def test_nested_parallel_and_interrupted_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for id, start, end in [('a', 1, 5), ('b', 2, 3), ('c', 4, 8)]:
                rows.extend([dict(id=id, phase='generation', event='start', utc_seconds=start),
                             dict(id=id, phase='generation', event='end', utc_seconds=end, seconds=end-start)])
            (root/'timing.jsonl').write_text('\n'.join(map(json.dumps, rows)))
            self.assertEqual(intervals(root)['phases']['generation']['seconds'], 7)
            with (root/'timing.jsonl').open('a') as stream:
                stream.write('\n'+json.dumps(dict(id='open', phase='generation', event='start', utc_seconds=9)))
            result = intervals(root)['phases']['generation']
            self.assertIsNone(result['seconds'])
            self.assertEqual(result['known_seconds'], 7)

    def test_backend_requests_are_not_forward_counts(self):
        with self.assertRaises(ValueError):
            validate_backend_telemetry({'input_tokens': 12, 'forward_count': 1}, model='q')
        value = validate_backend_telemetry(dict(version='local-forward-v1', model='q', request_id='r', complete=True,
            forwards=[dict(id='f', input_tokens=10, status='completed')]), model='q')
        self.assertEqual(value['forward_count'], 1)
        self.assertEqual(value['input_tokens'], 10)

    def test_pruner_failure_preserves_backend_and_head_evidence(self):
        from methods.swe_pruner_pro.adapter import SwePrunerPro, MODEL, SERVICE_VERSION
        from src.method_transport import ServiceHTTPError
        from src.session import Message, SessionEvent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {'tokenana': {'version': SERVICE_VERSION, 'backbone': MODEL,
                'head_model': MODEL, 'backend_base_url': 'http://backend'},
                'backend_requests': [{'complete': False, 'submitted_input_tokens': 100,
                    'started_at': 1, 'finished_at': 2, 'duration_sec': 1}],
                'head_compute': {'version': 'local-forward-v1', 'forwards': [], 'complete': False}}
            def failed(*args, **kwargs):
                raise ServiceHTTPError(500, payload)
            callback = SwePrunerPro().callback(None, None,
                {'service_base_url': 'http://pruner', 'backend_base_url': 'http://backend'}, transport=failed)
            history = [Message('a', 'assistant', 'Read source', tool_calls=('call',), native={'message': {
                'tool_calls': [{'id': 'call', 'function': {'name': 'bash', 'arguments': '{"command":"cat x.py"}'}}]}}),
                Message('t', 'tool', 'x'*2100, editable=True, tool_result='call')]
            result = callback(SessionEvent('after_tool', history, {},
                {'artifact_host': str(root), 'artifact_execution': str(root)}))
            self.assertEqual(result.history, history)
            records = [json.loads(p.read_text()) for p in (root/'service-records').glob('*.json')]
            self.assertEqual(len(records), 2)
            self.assertEqual(sum(r['inference'] for r in records), 1)
            self.assertTrue(all('local_compute' in r for r in records))
            self.assertTrue(all(not r['complete'] for r in records))


if __name__ == '__main__':
    unittest.main()
