"""Optional historical experience-preparation evidence, separate from task totals."""
import json
from pathlib import Path

from .accounting_v2 import api_cost, corrected_v2, request_rows
from .raw_usage import read_raw_usage
from .telemetry import intervals


def preparation_report(root, study):
    root = Path(root).resolve()
    path = root / 'preparation' / 'experience.json'
    missing = {'status': 'unknown', 'api_cost_usd': None, 'amortized_cost_usd': None,
               'reason': 'No measured historical experience preparation ledger supplied'}
    if not path.is_file():
        return missing
    try:
        ledger = json.loads(path.read_text())
        if ledger.get('version') != 1 or ledger.get('kind') != 'historical_experience_preparation':
            raise ValueError('Expected version 1 historical_experience_preparation ledger')
        scopes = ledger.get('configuration_ids', [])
        configs = {c['id']: c for c in study['configurations'] if c['method'] == 'eet'}
        if not scopes or len(set(scopes)) != len(scopes) or any(c not in configs for c in scopes):
            raise ValueError('Preparation amortization requires explicit EET configuration scope')
        entries = ledger.get('records', [])
        if not entries or len({r['id'] for r in entries}) != len(entries):
            raise ValueError('Preparation records must have unique identities')
        usages = []
        evidence = []
        for entry in entries:
            directory = (path.parent / entry['api_records']).resolve()
            if not directory.is_relative_to(path.parent) or not directory.is_dir():
                raise ValueError('Preparation raw API records must be retained under preparation/')
            if str(directory) in evidence:
                raise ValueError('Preparation raw API directory must not be charged twice')
            usages.append(read_raw_usage(directory, case_id=entry['id'], attempt_id='preparation',
                                         call_id=entry['id']))
            evidence.append(str(directory))
        cost = api_cost(usages, study['pricing'])
        selected = sum(configs[c]['selected'] for c in scopes)
        from decimal import Decimal
        amortized = str(Decimal(cost['total_usd']) / selected) if cost.get('complete') and cost.get('total_usd') is not None else None
        return {'status': 'measured_evidence', 'source': str(path), 'evidence': evidence,
                'configuration_ids': scopes, 'amortization_task_runs': selected,
                'api_cost_usd': cost.get('total_usd'), 'amortized_cost_usd': amortized,
                'cost': cost, 'api_usage': corrected_v2(usages), 'requests': request_rows(usages),
                'timing': intervals(path.parent), 'local_compute_pricing': 'unpriced',
                'note': 'Historical preparation only; costs use the study price snapshot. '
                        'Separate from task execution; no evaluation trajectories are used to build experience.'}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {**missing, 'status': 'invalid_evidence', 'reason': str(error), 'source': str(path)}
