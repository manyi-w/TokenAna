import json
from pathlib import Path
import tempfile
import unittest

from src.accounting import CaseUsage, UsageObservation, UsageOperation
from src.accounting_v2 import corrected_v2, api_cost, request_rows
from src.raw_usage import read_raw_usage
from src.usage_protocols import normalize_usage, UsageEvents


def observation(case='a', call='main', **metrics):
    return UsageObservation(case, 'attempt', call, 'r/response',
        {'input': 100, 'output': 20, 'total': 120, 'cache_read': 50, 'cache_write': 0, 'reasoning': 10, **metrics},
        'response.body', model='model', purpose='main', operation_id='r', billing_context={'provider': 'openai', 'status': 200})


class AccountingTests(unittest.TestCase):
    def test_fixed_denominator_and_duplicates(self):
        item = observation()
        report = corrected_v2([CaseUsage('a', True, [item, item]), CaseUsage('b', False)])
        self.assertEqual(report['metrics']['total']['sum'], 120)
        self.assertEqual(report['metrics']['total']['mean'], 60)

    def test_auxiliary_identity_and_failure(self):
        a, b = observation(), observation(call='helper')
        case = CaseUsage('a', True, [a, b], operations=[UsageOperation('a', 'attempt', 'main', 'failed')])
        value = corrected_v2([case])['metrics']['total']
        self.assertIsNone(value['sum'])
        self.assertEqual(value['known_subtotal'], 240)

    def test_unknown_not_zero(self):
        self.assertIsNone(corrected_v2([CaseUsage('a', None)])['metrics']['input']['sum'])
        self.assertEqual(corrected_v2([CaseUsage('a', False)])['metrics']['input']['sum'], 0)

    def test_per_metric_completeness(self):
        result = corrected_v2([CaseUsage('a', True, [observation(reasoning=None)])])
        self.assertTrue(result['metrics']['total']['complete'])
        self.assertFalse(result['metrics']['reasoning']['complete'])

    def test_conflicting_identity(self):
        with self.assertRaises(ValueError):
            corrected_v2([CaseUsage('a', True, [observation(), observation(input=101, total=121)])])

    def test_cache_and_reasoning_subsets(self):
        value = normalize_usage({'input_tokens': 5, 'cache_read_input_tokens': 50,
            'cache_creation_input_tokens': 10, 'output_tokens': 20}, 'anthropic_messages')
        self.assertEqual(value['total'], 85)
        self.assertIsNone(value['reasoning'])

    def test_stream_snapshots(self):
        events = UsageEvents('anthropic_messages', True)
        events.add({'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 10, 'output_tokens': 0}}})
        for n in (2, 5):
            events.add({'type': 'message_delta', 'usage': {'output_tokens': n}})
        events.add({'type': 'message_stop'})
        self.assertEqual(events.records['m']['output_tokens'], 5)
        self.assertEqual(len(events.records), 1)

    def test_prepared_request_and_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for identity, meta, body in [('prepared', {'forwarded_at': None}, None),
                ('failed', {'forwarded_at': 1, 'status': 503, 'response_complete': True}, {'error': 'unavailable'}),
                ('success', {'forwarded_at': 2, 'status': 200, 'response_complete': True},
                 {'id': 'm', 'usage': {'input_tokens': 10, 'output_tokens': 2}})]:
                p = root / identity
                p.mkdir()
                (p/'metadata.json').write_text(json.dumps({'protocol': 'responses', 'model': 'model', **meta}))
                if body:
                    (p/'response.body').write_text(json.dumps(body))
            case = read_raw_usage(root, case_id='a', attempt_id='1', call_id='main', forwarded_only=True)
            self.assertEqual(len(case.operations), 3)
            self.assertEqual(len(case.observations), 1)
            self.assertIsNone(corrected_v2([case])['metrics']['total']['sum'])
            self.assertEqual(corrected_v2([case])['metrics']['total']['known_subtotal'], 12)
            self.assertEqual(sum(r['request_state'] == 'prepared' for r in request_rows([case])), 1)

    def test_no_cache_discount_tariff(self):
        pricing = {'version': 1, 'currency': 'USD', 'cny_per_usd': '7', 'models': [dict(
            name='model', provider='openai', aliases=['model'], currency='USD', region='test', source='fixture',
            rule='cache_read', input='10', output='20', cache_read='1')]}
        result = api_cost([CaseUsage('a', True, [observation()])], pricing)
        self.assertAlmostEqual(float(result['total_usd']), .00095)
        self.assertAlmostEqual(float(result['no_cache_discount']['total_usd']), .0014)


if __name__ == '__main__':
    unittest.main()
