import unittest
from src.study_statistics import paired_bootstrap, pareto
from src.study_report import aggregate, comparisons
from src.budgets import percentile


class StatisticsTests(unittest.TestCase):
    def test_repository_paired_reproducible(self):
        base = {'a': {'repo': 'r1', 'value': 100}, 'b': {'repo': 'r1', 'value': 200}, 'c': {'repo': 'r2', 'value': 300}}
        method = {k: {**v, 'value': v['value']/2} for k, v in base.items()}
        result = paired_bootstrap({'baseline': base, 'method': method}, 'baseline', samples=10000)
        self.assertEqual(result['method']['ci95'], [.5, .5])
        self.assertEqual(result['method']['rank_probabilities'], {'1': 1.0})
        self.assertEqual(result, paired_bootstrap({'baseline': dict(reversed(list(base.items()))), 'method': method}, 'baseline'))

    def test_zero_baseline(self):
        result = paired_bootstrap({'baseline': {'a': {'repo': 'r', 'value': 0}}}, 'baseline', samples=10)
        self.assertIsNone(result['baseline']['savings'])
        self.assertEqual(result['baseline']['ci95'], [None, None])

    def test_nonmatching_tasks_refused(self):
        with self.assertRaises(ValueError):
            paired_bootstrap({'baseline': {}, 'method': {'a': {'repo': 'r', 'value': 1}}}, 'baseline')

    def test_pareto(self):
        points = [dict(id='a', resource=1, resolved=.5), dict(id='b', resource=2, resolved=.8),
                  dict(id='c', resource=3, resolved=.4)]
        self.assertEqual(pareto(points), ['a', 'b'])

    def test_native_percentile(self):
        self.assertEqual(percentile([1, 2, 10, 20], .75), 12.5)

    def test_partial_metrics_not_complete_rank(self):
        config = dict(id='m', baseline_id=None, selected=2, group='g', dataset='deepswe', agent='mini', model='q', method='run_free')
        rows = [dict(configuration_id='m', case=str(i), attempted=True, model_called=True, evaluated=True,
            resolved=False, method_triggered=False, tokens=20 if i == 0 else None, cost_usd=None,
            seconds=1, original_tokens=20, comparison_issues=[]) for i in range(2)]
        study = {'configurations': [config], 'analysis': {}}
        totals = aggregate(study, rows)
        self.assertIsNone(totals[0]['tokens'])
        self.assertEqual(totals[0]['tokens_known_subtotal'], 20)
        self.assertEqual(totals[0]['seconds'], 2)
        ranks, _, _ = comparisons(study, totals, rows)
        self.assertIsNone(next(r for r in ranks if r['metric'] == 'tokens')['rank'])
        self.assertEqual(next(r for r in ranks if r['metric'] == 'seconds')['rank'], 1)


if __name__ == '__main__':
    unittest.main()
