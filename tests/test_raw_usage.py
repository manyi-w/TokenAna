"""Raw API recording fixtures only; never bind sockets or execute an agent."""

import json
from pathlib import Path
import tempfile
import unittest

from src.raw_usage import read_raw_usage
from src.accounting import corrected_accounting


class RawUsageTests(unittest.TestCase):
    def test_raw_sse_retries_duplicates_and_truncation(self):
        raw = {"input_tokens": 100, "output_tokens": 20,
               "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
               "output_tokens_details": {"reasoning_tokens": 5}, "new_field": 3}
        event = {"type": "response.completed", "response": {"id": "r", "usage": raw}}
        encoded = ('data: ' + json.dumps(event) + '\n\n').encode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, body in (("request1", encoded * 2), ("retry", encoded + b'data: {')):
                directory = root / name
                directory.mkdir()
                (directory / 'metadata.json').write_text(json.dumps({
                    "response_complete": True,
                    "response_headers": [["Content-Type", "text/event-stream"]]}))
                (directory / 'response.body').write_bytes(body)
            case = read_raw_usage(root, case_id='a', attempt_id='1', call_id='1')
            self.assertEqual(len(case.observations), 2)
            self.assertEqual(case.observations[0].raw_usage, raw)
            report = corrected_accounting([case])
            self.assertEqual(report['metrics']['total']['sum'], 240)
            self.assertEqual(report['metrics']['cache_read']['sum'], 80)
            self.assertFalse(report['metrics']['total']['complete'])
            self.assertEqual((root / 'request1' / 'response.body').read_bytes(), encoded * 2)

    def test_missing_or_malformed_usage_is_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(read_raw_usage(root, case_id='a', attempt_id='1', call_id='1').llm_called)
            directory = root / 'request'
            directory.mkdir()
            (directory / 'metadata.json').write_text('[]')
            case = read_raw_usage(root, case_id='a', attempt_id='1', call_id='1')
            self.assertFalse(case.coverage_complete)
            self.assertIsNone(corrected_accounting([case])['metrics']['total']['sum'])

    def test_terminal_sse_requires_blank_line(self):
        from src.raw_usage import _events
        with self.assertRaises(ValueError):
            list(_events(b'data: {}\n', 'text/event-stream'))
        self.assertEqual(list(_events(b'data: {}\n\n', 'text/event-stream')), [{}])
