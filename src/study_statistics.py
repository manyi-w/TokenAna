"""Paired repository-cluster resampling; no model-repeat interpretation."""
from collections import Counter, defaultdict
import random


def paired_records(records):
    """Align every selected method task with its baseline, retaining missing pairs."""
    index = {(r['configuration_id'], r['case']): r for r in records}
    return [(row, index.get((row['baseline_id'], row['case']))) for row in records]


def pair_outcome(row, baseline):
    if baseline is None or row['resolved'] is None or baseline['resolved'] is None:
        return 'unknown'
    return ('both_success' if row['resolved'] and baseline['resolved'] else
            'method_only_success' if row['resolved'] else
            'baseline_only_success' if baseline['resolved'] else 'both_failed')


def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    x = (len(values) - 1) * q
    lo = int(x)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (x - lo)


def paired_bootstrap(cases, baseline, *, samples=10000, seed=20260921):
    """cases maps method -> task -> {repo, value}, over identical complete sets."""
    if type(samples) is not int or samples < 1:
        raise ValueError('samples must be a positive integer')
    tasks = set(cases[baseline])
    if any(set(v) != tasks for v in cases.values()):
        raise ValueError('Bootstrap requires identical task sets')
    repos = defaultdict(list)
    for task, item in sorted(cases[baseline].items()):
        if any(values[task]['repo'] != item['repo'] or values[task]['value'] is None or values[task]['value'] < 0 for values in cases.values()):
            raise ValueError('Bootstrap requires matching repositories and known nonnegative values')
        repos[item['repo']].append(task)
    buckets = {method: [sum(values[t]['value'] for t in task_ids) for task_ids in repos.values()]
               for method, values in cases.items()}
    if not buckets or not repos:
        return {}
    actual = {method: sum(values) for method, values in buckets.items()}
    estimates = {method: [] for method in buckets}
    ranks = {method: Counter() for method in buckets}
    generator = random.Random(seed)
    for _ in range(samples):
        weights = Counter(generator.randrange(len(repos)) for _ in repos)
        totals = {method: sum(values[i] * weight for i, weight in weights.items()) for method, values in buckets.items()}
        for method, value in totals.items():
            ranks[method][1 + sum(v < value for v in totals.values())] += 1
            if totals[baseline] != 0:
                estimates[method].append(1 - value / totals[baseline])
    return {method: dict(savings=1 - value / actual[baseline] if actual[baseline] else None,
        ci95=[quantile(estimates[method], .025), quantile(estimates[method], .975)],
        rank_probabilities={str(rank): count / samples for rank, count in sorted(ranks[method].items())},
        valid_ratio_samples=len(estimates[method]), samples=samples, seed=seed,
        note='Paired repository-cluster bootstrap; does not measure model repeated-sampling variance.')
        for method, value in actual.items()}


def pareto(points):
    """Resource minimized, resolved rate maximized; ties remain on the frontier."""
    return [p['id'] for p in points if not any(q['resource'] <= p['resource'] and q['resolved'] >= p['resolved']
            and (q['resource'] < p['resource'] or q['resolved'] > p['resolved']) for q in points)]
