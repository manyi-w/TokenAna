"""Four RQ1 launchers, using the shared executor and official evaluator."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tomllib
from urllib.parse import urlsplit

from .components import Component
from .config import ExperimentConfig
from .records import write_json

ROOT = Path(__file__).resolve().parents[1]
METHODS = ('run_free', 'turn_control', 'attn_compress', 'agent_diet')


def specification(method):
    spec = tomllib.loads((ROOT / 'RQ1' / method / 'experiment.toml').read_text())
    ids = (ROOT / 'RQ1' / method / 'source-tasks.txt').read_text().splitlines()
    source = json.loads((ROOT / spec['task_source']).read_text())
    if spec.get('task_selection') == 'first-n-in-source-order':
        source = source[:spec['source_count']]
    expected = [v['instance_id'] if isinstance(v, dict) else v for v in source]
    if ids != expected or len(ids) != spec['requested_count'] or len(set(ids)) != len(ids):
        raise ValueError('Frozen RQ1 task population/order changed')
    return spec, ids


def paper_pricing(method, spec):
    from .pricing import validate_pricing
    original = spec.get('original_price_usd_per_million', {})
    if method == 'run_free':
        # Paper reports tokens, not dollar prices. This separately labelled
        # Sonnet tariff is supplemental; paper cost remains NOT REPORTED.
        original = {'input': '3', 'output': '15', 'cache_read': '0.3',
                    'cache_write_5m': '3.75', 'cache_write_1h': '6'}
    base = dict(name=spec['model_id'], provider='anthropic' if method == 'run_free' else 'google',
        aliases=[spec['model_id']], currency='USD', region='paper-frozen',
        rule='cache_ttl' if method == 'run_free' else 'cache_read',
        source='RQ1/' + method + '/experiment.toml#original_price_usd_per_million')
    base.update({k: v for k, v in original.items() if not k.startswith('helper_')})
    for key in ('cache_price_source', 'cache_price_note'):
        if key in spec:
            base[key] = spec[key]
    if method == 'agent_diet':
        base.update(long_context_threshold=200000, long_input_multiplier='2', long_output_multiplier='1.5',
            tier_source='https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-pro',
            tier_note='Supplemental stable-model tiers; original author rates and original formula remain separate.')
    if method == 'run_free':
        base['source'] = 'https://platform.claude.com/docs/en/about-claude/pricing#claude-sonnet-45'
        base['region'] = 'supplemental-tariff-paper-does-not-report-dollar-cost'
    models = [base]
    if method == 'agent_diet':
        models.append(dict(name=spec['helper_model_id'], provider='openai', aliases=[spec['helper_model_id']],
            currency='USD', region='paper-frozen', rule='cache_read', source=base['source'],
            **{k.removeprefix('helper_'): v for k, v in original.items() if k.startswith('helper_')}))
    return validate_pricing(dict(version=1, currency='USD', cny_per_usd='1', models=models))


def prepare(method, settings_path):
    spec, ids = specification(method)
    settings = tomllib.loads(settings_path.read_text())
    selected = dict(settings.get(method, {}))
    errors = []
    allowed = {'endpoint', 'api_key_env', 'helper_endpoint', 'helper_api_key_env', 'official_keys_env',
               'compression_endpoint', 'python_executable', 'executable'}
    if selected.keys() - allowed:
        errors.append('Unknown method settings; credentials must only be supplied through named environment variables')
    required = ['endpoint', 'api_key_env']
    if method == 'agent_diet':
        required += ['helper_endpoint', 'helper_api_key_env']
    if method == 'attn_compress':
        required += ['compression_endpoint']
    for key in required:
        if not selected.get(key):
            errors.append(method + '.' + key + ' is required in RQ1 settings')
    for key in ('endpoint', 'helper_endpoint', 'compression_endpoint'):
        if key in selected:
            url = urlsplit(selected[key])
            if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                    or url.query or url.fragment):
                errors.append(key + ' must be an HTTP(S) base URL without credentials/query')
    for key in ('api_key_env', 'helper_api_key_env'):
        name = selected.get(key)
        if name and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            errors.append(key + ' must be an environment variable NAME')
            continue
        if name and not os.environ.get(name):
            errors.append('Missing credential environment variable: ' + name)
    images = dict(settings.get('images', {}).get(method, {}))
    from .rq1_images import DEFAULT_VERIFIER, image_reference
    settings['verifier_image'] = settings.get('verifier_image') or DEFAULT_VERIFIER
    try:
        for value in [settings['verifier_image'], *(v for v in images.values() if v)]:
            image_reference(value)
    except ValueError as error:
        errors.append(str(error))
    if shutil.which('docker'):
        try:
            info = subprocess.run(['docker', 'info', '--format', '{{.OSType}}'],
                                  capture_output=True, text=True, timeout=20)
            if info.returncode or info.stdout.strip() != 'linux':
                errors.append('Local Linux Docker daemon unavailable')
        except subprocess.TimeoutExpired:
            errors.append('Docker daemon check timed out')
    else:
        errors.append('Docker CLI unavailable')
    if method in ('agent_diet', 'attn_compress'):
        modules = ['openai', 'docker', 'pexpect', 'tiktoken', 'lz4'] + (['google.genai', 'httpx'] if method == 'attn_compress' else [])
        for module in modules:
            try:
                present = importlib.util.find_spec(module) is not None
            except ModuleNotFoundError:
                present = False
            if not present:
                errors.append('Missing prepared native agent dependency: ' + module)
    if method == 'attn_compress':
        from urllib.request import urlopen
        try:
            with urlopen(selected['compression_endpoint'].rstrip('/') + '/health', timeout=5) as response:
                health = json.load(response)
            if (health.get('version') != 'tokenana-attn-forward-v1' or
                    Path(health.get('model', '')).name.lower() != 'qwen3-4b-instruct-2507' or
                    health.get('max_tokens') != 300000):
                errors.append('Compression service must be observed Qwen3-4B-Instruct-2507, max_tokens=300000')
        except Exception:
            errors.append('Prepared compression service health is unavailable')
    options = {**selected, 'method': method, 'spec': spec, 'record_raw_usage': True,
        'model_protocol': 'anthropic_messages' if method == 'run_free' else
                          'chat_completions' if method == 'agent_diet' else 'gemini_generate_content'}
    if method == 'turn_control':
        options['trae'] = {k: selected.get(k, '/opt/tokenana/bin/' + ('python' if k == 'python_executable' else 'trae-cli'))
                           for k in ('python_executable', 'executable')}
        options['trae'].update(max_steps=45, generation_parameters={'temperature': 0.0, 'max_tokens': 8192})
    if method == 'attn_compress':
        options.setdefault('official_keys_env', 'GEMINI_API_KEYS')
    config = ExperimentConfig(ROOT / 'RQ1' / method / 'experiment.toml',
        Component('method', method, ROOT / 'RQ1/native', {}, 'NativeMethod'),
        Component('agent', 'rq1-native-' + method, ROOT / 'RQ1/native', options, 'NativeAgent'),
        Component('dataset', 'swe_bench_verified', ROOT / 'datasets/swe_bench_verified', {'task_ids': ids}, 'SweBenchVerified'))
    runtime = {'network': 'none', 'command_prefix': [], 'images': images,
        'model_channel': {'kind': 'unix_socket', 'python': settings.get('channel_python', '/opt/tokenana/bin/python')},
        'environment_names': [selected[k] for k in ('api_key_env', 'helper_api_key_env') if k in selected],
        'retain_files': True, 'retention_mode': 'research', 'never_regenerate': True,
        'measurement_jobs': 1,
        'verified_evaluation': {'image': settings.get('verifier_image'),
            'python': settings.get('verifier_python', '/opt/tokenana/bin/python'), 'jobs': 4}}
    from .model_channel import validate_channel
    try:
        validate_channel(runtime, supported=True)
    except ValueError as error:
        errors.append(str(error))
    return config, runtime, paper_pricing(method, spec), errors


def verify_images(config, runtime):
    """No model requests: exercise frozen executable/dependency prerequisites."""
    method = config.method.name
    selected = config.agent.options
    for image in sorted(set(runtime['images'].values())):
        if method == 'run_free':
            command = ['bash', '-lc', 'claude --version']
        elif method == 'turn_control':
            command = [selected['trae']['python_executable'], '-B', '-c',
                       'import trae_agent.agent.trae_agent; from google import genai; import openai; import anthropic']
        else:
            command = ['/home/swe-bench/conda_envs/py312/bin/python3', '-B', '-c',
                       'import sys; assert sys.version_info[:2] == (3, 12)']
        result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', command[0], image, *command[1:]],
                                capture_output=True, text=True, timeout=60)
        if result.returncode or (method == 'run_free' and not re.search(r'(?<![\d.])1\.0\.16(?![\d.])', result.stdout)):
            raise ValueError('Prepared task image has missing/wrong native runtime: ' + image)
        relay = runtime['model_channel']['python']
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', relay, image,
                        '-B', '-c', 'import socket; assert hasattr(socket,"AF_UNIX")'], check=True, capture_output=True, timeout=60)
    verifier = runtime['verified_evaluation']
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', verifier['python'], verifier['image'],
                    '-B', '-c', 'import swebench.harness.run_evaluation'], check=True, capture_output=True, timeout=60)


def restore(run, method):
    snapshot = json.loads((run / 'config.json').read_text())
    if snapshot['method']['name'] != method:
        raise ValueError('Run belongs to a different RQ1 method')
    parts = {k: Component(**{**snapshot[k], 'path': Path(snapshot[k]['path'])}) for k in ('method', 'agent', 'dataset')}
    config = ExperimentConfig(Path(snapshot['path']), **parts)
    return config, json.loads((run / 'runtime.json').read_text()), json.loads((run / 'pricing.json').read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('method', choices=METHODS)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--check', action='store_true')
    actions.add_argument('--run', action='store_true')
    actions.add_argument('--resume', type=Path)
    actions.add_argument('--report', type=Path)
    parser.add_argument('--settings', type=Path, default=Path(os.environ.get('RQ1_SETTINGS', ROOT / 'RQ1/settings.toml')))
    args = parser.parse_args()
    from .rq1_report import report_run
    if args.report:
        import fcntl
        report_path = args.report.resolve()
        with (report_path.parent / ('.' + report_path.name + '.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.exit(2, 'This RQ1 run is active; rebuild after it stops.\n')
            report_run(report_path, args.method)
        return
    if args.resume:
        run = args.resume.resolve()
        config, runtime, pricing = restore(run, args.method)
    else:
        if not args.settings.is_file():
            parser.exit(2, 'Configure RQ1/settings.toml (or use --settings); no experiment started.\n')
        config, runtime, pricing, errors = prepare(args.method, args.settings.resolve())
        if errors:
            parser.exit(2, '\n'.join('NOT READY: ' + e for e in errors) + '\nNo experiment started.\n')
        if args.check:
            print('Local prerequisites passed. --run will pull/build missing task images and the official evaluator. '
                  'Image runtime, model access and formal execution remain unverified.')
            return
        run = ROOT / 'RQ1/runs' / args.method / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    from .execution import run_experiment
    from .evaluation import evaluate
    import fcntl
    run.parent.mkdir(parents=True, exist_ok=True)
    with (run.parent / ('.' + run.name + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, 'This RQ1 run is already active.\n')
        preparation = None
        try:
            if not args.resume:
                from .rq1_images import prepare_images
                # The shared executor creates the run directory itself. Keep
                # preparation failures/logs separately, then retain them with
                # the run as soon as the executor has initialized it.
                preparation = run.parent / '.preparation' / run.name
                runtime = prepare_images(preparation, config, runtime)
            verify_images(config, runtime)
            run_experiment(config, runtime, run, resume=bool(args.resume), pricing=pricing)
            evaluate(run, 'local', execute=True)
        finally:
            if preparation and preparation.exists() and run.exists():
                shutil.move(str(preparation), str(run / 'image-preparation'))
            if (run / 'state.json').exists():
                report_run(run, args.method)
    print('RQ1 report: ' + str(run / 'rq1-report/summary.md'))


if __name__ == '__main__':
    main()
