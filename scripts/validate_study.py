"""Run independent offline test modules with isolated temp roots; no API or Docker."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.jobs < 1 or sys.version_info[:3] != (3, 12, 14):
        parser.error('positive jobs and tokenAna Python 3.12.14 required')
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    modules = sorted((root/'tests').glob('test_*.py'))
    def run(path):
        with tempfile.TemporaryDirectory(prefix='tokenana-validation-') as tmp:
            process = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', path.name, '-v'],
                cwd=root, env={**os.environ, 'TMPDIR': tmp, 'PYTHONDONTWRITEBYTECODE': '1'}, text=True, capture_output=True)
        (args.output/(path.stem+'.log')).write_text(process.stdout+process.stderr)
        return {'module': path.name, 'returncode': process.returncode, 'kind': 'offline fixtures'}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, modules))
    (args.output/'validation.json').write_text(json.dumps({'jobs': args.jobs, 'results': results}, indent=2))
    print(json.dumps(results, indent=2))
    return int(any(r['returncode'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
