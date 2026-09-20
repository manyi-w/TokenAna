"""Run only TokenAna CLI --dry-run for all checked-in method matrix templates."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for config in sorted((root / 'experiments/method_matrix').glob('*.toml')):
        process = subprocess.run([sys.executable, str(root / 'tokenAna.py'), 'run', str(config), '--dry-run'],
                                 cwd=root, text=True, capture_output=True, timeout=60, check=True)
        plan = json.loads(process.stdout)
        if plan['status'] != 'plan_only' or plan['runtime_ready']:
            raise ValueError('unexpected execution-ready dry run')
        (args.output / (config.stem + '.json')).write_text(process.stdout)
        rows.append({'config': config.name, 'task_count': plan['task_count'],
                     'model': plan['model_compatibility']['status'],
                     'method': plan['method_compatibility'], 'accounting': plan['accounting_compatibility']['status']})
    report = {'status': 'offline_planning_only', 'logical_combinations': 48, 'configurations': len(rows),
              'real_components_verified': False, 'rust_compiled': False, 'plans': rows}
    (args.output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'output': str(args.output.resolve()), 'configurations': len(rows), 'status': report['status']}))


if __name__ == '__main__':
    main()
