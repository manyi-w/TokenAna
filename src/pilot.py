"""Portable DeepSWE matrix launcher. Host side uses only the standard library."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import csv
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import subprocess
import sys
import tomllib
from uuid import uuid4

from .records import write_json
from .telemetry import span, totals

ROOT = Path(__file__).resolve().parents[1]
METHODS = ('run_free', 'turn_control', 'agent_diet', 'eet')
AGENTS = ('codex', 'mini', 'trae', 'opencode')
MODELS = ('gpt-5.6-sol', 'claude-opus-5', 'deepseek-v4.1-flash', 'qwen3.8-max')
AGENT_DIR = {'mini': 'mini_swe_agent', **{a: a for a in AGENTS if a != 'mini'}}
BUDGET = dict(zip(MODELS, ('deepswe_gpt', 'deepswe_claude', 'deepswe_deepseek', 'deepswe_qwen')))
DEFAULT_CASE = 'adaptix-name-mapping-aliases'
LOCAL = ROOT / 'config/local/deepswe-pilot'


def parser():
    p = argparse.ArgumentParser(description='Run DeepSWE locally, retaining all attempts and both token accounts.')
    for flag in ('method', 'model', 'agent'):
        p.add_argument('--' + flag, help='Comma-separated selection')
    cases = p.add_mutually_exclusive_group()
    cases.add_argument('--case', help='Comma-separated task IDs')
    cases.add_argument('--cases', type=int, help='First N tasks from the fixed Python task list')
    p.add_argument('--jobs', type=int, default=1)
    output = p.add_mutually_exclusive_group()
    output.add_argument('--output', type=Path)
    output.add_argument('--resume', type=Path)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--_worker', type=Path, help=argparse.SUPPRESS)
    return p


def selection(value, allowed, label):
    values = list(allowed) if value is None else value.split(',')
    if not values or any(v not in allowed for v in values):
        raise ValueError(f'Invalid {label}; choose from: ' + ','.join(allowed))
    return list(dict.fromkeys(values))


def matrix(methods, agents, models, cases):
    rows, skipped = [], []
    for method in methods:
        for agent in agents:
            for model in models:
                if agent == 'codex' and model != MODELS[0]:
                    skipped.append(f'{method}/{agent}/{model}: Codex only supports the selected GPT model')
                    continue
                for case in cases:
                    rows.append(dict(method=method, agent=agent, model=model, case=case,
                                     id=f'{method}__{agent}__{model}__{case}'))
    if not rows:
        raise ValueError('Selection contains no valid combinations (Codex requires gpt-5.6-sol)')
    return rows, skipped


def fixed_cases():
    path = ROOT / 'config/deepswe-pilot-cases.txt'
    return [line for line in path.read_text().splitlines() if line and not line.startswith('#')]


def read_settings():
    # Never render the raw TOML, parser errors or credential-bearing values.
    try:
        raw = tomllib.loads((LOCAL / 'settings.toml').read_text())
    except (OSError, ValueError):
        raise ValueError('Cannot parse config/local/deepswe-pilot/settings.toml') from None
    secrets = {}
    secret_path = LOCAL / 'secrets.env'
    if secret_path.exists():
        for line in secret_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.removeprefix('export ').partition('=')
            if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                raise ValueError('Invalid variable name in secrets.env')
            words = shlex.split(value, comments=True)
            if len(words) > 1:
                raise ValueError('Quote values containing spaces in secrets.env')
            secrets[key] = words[0] if words else ''
    settings = {'models': {}, 'images': raw.get('images', {}),
                'runtime_paths': raw.get('runtime_paths', {})}
    for index, name in enumerate((*MODELS, 'agent_diet_auxiliary')):
        if name == 'agent_diet_auxiliary':
            item = dict(raw.get(name, {}))
        else:
            candidates = [v for v in raw.get('models', {}).values() if v.get('name') == name]
            item = dict(candidates[0]) if candidates else {}
        key = item.get('api_key_env', '')
        # Legacy filled templates accidentally contained literal keys here.
        # Use a private in-memory reference, never copy that value into run files.
        if key and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            variable = f'TOKENANA_PILOT_KEY_{index}'
            secrets[variable] = key
            item['api_key_env'] = variable
        if name == 'agent_diet_auxiliary':
            settings[name] = item
        else:
            settings['models'][name] = item
    return settings, {**secrets, **{k: os.environ[k] for k in secrets if k in os.environ}}


def preflight(settings, credentials, rows):
    from .models import ModelConfig, model_plan
    from .loading import load_adapter
    from .components import load_component
    errors, notes = [], []
    for name in sorted({r['model'] for r in rows}):
        item = settings['models'][name]
        for key in ('provider', 'model_id', 'protocol', 'base_url', 'api_key_env'):
            if not item.get(key):
                errors.append(f'{name}: missing {key}')
        key = item.get('api_key_env', '')
        if not credentials.get(key, os.environ.get(key)):
            errors.append(f'{name}: credential {key or "reference"} is empty')
        if all(item.get(k) for k in ('provider', 'model_id', 'protocol', 'base_url', 'api_key_env')):
            model = ModelConfig(**{k: item[k] for k in ('name', 'provider', 'model_id', 'protocol', 'base_url', 'api_key_env')})
            for agent in sorted({r['agent'] for r in rows if r['model'] == name}):
                adapter = load_adapter(load_component(ROOT / 'agents' / AGENT_DIR[agent], 'agent', {}))
                report = model_plan(adapter, model, agent_options(agent, item, settings))
                errors.extend(f'{agent}/{name}: {e}' for e in report['errors'])
        if any(r['agent'] == 'opencode' and r['model'] == name for r in rows):
            if not item.get('opencode_context_limit') or not item.get('opencode_output_limit'):
                notes.append(f'{name}: OpenCode experiment limits default to context=131072, output=8192; these are experiment caps, not provider specifications')
    if any(r['method'] == 'agent_diet' for r in rows):
        helper = settings['agent_diet_auxiliary']
        if not helper.get('base_url') or helper.get('model_id') != 'gpt-5-mini':
            errors.append('AgentDiet requires helper base_url and model_id=gpt-5-mini')
        key = helper.get('api_key_env', '')
        if not credentials.get(key, os.environ.get(key)):
            errors.append('AgentDiet helper credential is empty')
    notes.append('Images are built/downloaded on execution; dry-run does not contact Docker or model services.')
    return errors, notes


def agent_options(agent, model, settings):
    paths = settings.get('runtime_paths', {})
    options = dict(executable=paths.get(agent + '_executable') or '/opt/tokenana/' + {'mini': 'mini', 'trae': 'trae-cli'}.get(agent, agent),
                   record_raw_usage=True, session_timeout=900, proxy_timeout=600,
                   timeout=5400, python_executable=paths.get('python_executable') or '/opt/tokenana/bin/python')
    if agent == 'codex':
        options.update(controlled_executable=paths.get('codex_controlled_executable') or '/opt/tokenana/codex-controlled',
                       control_version='codex-sampling-boundary-v1',
                       session_executable=paths.get('codex_controlled_executable') or '/opt/tokenana/codex-controlled',
                       session_version='codex-session-compatible-v1')
    elif agent == 'opencode':
        options.update(config_root=paths.get('opencode_config_root') or '/opt/tokenana/opencode-config',
                       context_limit=model.get('opencode_context_limit') or 131072,
                       output_limit=model.get('opencode_output_limit') or 8192)
    elif agent == 'mini':
        options['cost_tracking'] = 'ignore_errors'
    return options


def experiment(row, settings, path):
    from .components import load_component
    from .config import ExperimentConfig
    from .models import ModelConfig
    model = settings['models'][row['model']]
    options = {}
    if row['method'] == 'turn_control':
        options = {'budget_profile': BUDGET[row['model']]}
    elif row['method'] == 'agent_diet':
        helper = settings['agent_diet_auxiliary']
        options = dict(helper_model='gpt-5-mini', helper_base_url=helper['base_url'],
                       helper_api_key_env=helper['api_key_env'])
    return ExperimentConfig(path,
        load_component(ROOT / 'methods' / row['method'], 'method', options),
        load_component(ROOT / 'agents' / AGENT_DIR[row['agent']], 'agent', agent_options(row['agent'], model, settings)),
        load_component(ROOT / 'datasets/deepswe', 'dataset', {'task_ids': [row['case']]}),
        ModelConfig(**{k: model[k] for k in ('name', 'provider', 'model_id', 'protocol', 'base_url', 'api_key_env')}))


def worker(payload_path):
    from .execution import run_experiment
    from .evaluation import evaluate
    from .analysis import analyze_run
    payload = json.loads(payload_path.read_text())
    row, settings = payload['row'], payload['settings']
    directory = payload_path.parent / 'run'
    config = experiment(row, settings, payload_path)
    runtime = payload['runtime']
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    result = {'status': 'interrupted'}
    try:
        with span(payload_path.parent, 'case_total', case_id=row['case']):
            run_experiment(config, runtime, directory, resume=(directory / 'state.json').exists())
            with span(payload_path.parent, 'evaluation', case_id=row['case']):
                evaluate(directory, 'local', execute=True)
            with span(payload_path.parent, 'analysis'):
                analysis = analyze_run(directory)
        state = json.loads((directory / 'state.json').read_text())
        # Interrupted/non-submitted results are not silently marked successful.
        saved = state['tasks'].get(row['case'], {})
        evaluation = state.get('evaluation', {})
        summary_path = evaluation.get('report_summary')
        evaluation_report = json.loads(Path(summary_path).read_text()) if summary_path else {}
        native_report_path = evaluation_report.get('report')
        native_report = json.loads(Path(native_report_path).read_text()) if native_report_path else {}
        result = {'status': 'completed' if saved.get('stage') == 'collected' else 'interrupted',
                  'resolved': saved.get('resolved'), 'agent_error': saved.get('agent_error'),
                  'evaluation': evaluation_report, 'analysis_directory': analysis['output']}
        if saved.get('agent_error'):
            result['status'] = 'agent_failed'
        if native_report.get('error_instances', 0):
            result['status'] = 'evaluation_failed'
        elif result['status'] == 'completed' and native_report.get('cases', {}).get(row['case'], {}).get('status') == 'not_submitted':
            result['status'] = 'not_submitted'
    except BaseException as error:
        result = {'status': 'failed', 'error_type': type(error).__name__, 'error': str(error)}
        raise
    finally:
        result['timing_seconds'] = totals(payload_path.parent)
        write_json(payload_path.parent / 'result.json', result)
    return 0 if result['status'] == 'completed' else 1


def summarize(output, rows):
    records = []
    for row in rows:
        parent = output / 'combinations' / row['id']
        result = json.loads((parent / 'result.json').read_text()) if (parent / 'result.json').exists() else {'status': 'pending'}
        report_path = Path(result['analysis_directory']) / 'accounting.json' if result.get('analysis_directory') else parent / 'run/accounting.json'
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        records.append({**row, **result, 'directory': str(parent),
                        'original': report.get('original_token_accounting'),
                        'corrected': report.get('corrected_token_accounting'),
                        'overhead': report.get('overhead'), 'timing_seconds': totals(parent)})
    write_json(output / 'summary.json', {'version': 1, 'cases': records,
        'timing_note': 'Nested and overlapping spans are not additive. Method callbacks include auxiliary API waits. Snapshot overhead is separate. Raw timing.jsonl and HTTP metadata support recalibration.'})
    columns = ['method', 'agent', 'model', 'case', 'status', 'resolved', 'directory']
    columns += [f'{account}_{metric}' for account in ('original', 'corrected') for metric in ('input', 'output', 'total')]
    columns += ['timing_seconds', 'overhead']
    with (output / 'summary.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            flat = {k: record.get(k) for k in columns[:7]}
            for account in ('original', 'corrected'):
                for metric in ('input', 'output', 'total'):
                    flat[f'{account}_{metric}'] = (record[account] or {}).get('metrics', {}).get(metric, {}).get('sum')
            for key in ('timing_seconds', 'overhead'):
                flat[key] = json.dumps(record.get(key), ensure_ascii=False)
            writer.writerow(flat)
    return records


def main(argv=None):
    args = parser().parse_args(argv)
    if args._worker:
        return worker(args._worker)
    if args.jobs < 1:
        raise ValueError('--jobs must be positive')
    settings, credentials = read_settings()
    output = (args.resume or args.output or ROOT / 'runs' / ('deepswe-pilot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))).resolve()
    if any(',' in str(path) or ':' in str(path) for path in (ROOT, output)):
        raise ValueError('Docker mount paths must not contain commas or colons')
    if args.resume:
        if any(v is not None for v in (args.method, args.model, args.agent, args.case, args.cases)):
            raise ValueError('--resume uses the saved selection; only --jobs may change')
        manifest = json.loads((output / 'pilot.json').read_text())
        rows, skipped = manifest['rows'], []
        if settings != manifest['settings']:
            raise ValueError('Settings differ from saved run; restore settings or start a new output directory')
    else:
        cases = fixed_cases()
        if args.case:
            from datasets.deepswe.tasks import select_tasks
            requested = list(dict.fromkeys(args.case.split(',')))
            selected = select_tasks(task_ids=requested)
            if {r.task.instance_id for r in selected} != set(requested):
                raise ValueError('Unknown --case task ID')
            cases = requested
        elif args.cases is not None:
            if not 1 <= args.cases <= len(cases):
                raise ValueError(f'--cases must be between 1 and {len(cases)}')
            cases = cases[:args.cases]
        else:
            cases = [DEFAULT_CASE]
        rows, skipped = matrix(selection(args.method, METHODS, 'method'), selection(args.agent, AGENTS, 'agent'),
                               selection(args.model, MODELS, 'model'), cases)
        if 'run_free' in {r['method'] for r in rows}:
            from datasets.deepswe.tasks import select_tasks
            if any(r.language != 'python' for r in select_tasks(task_ids=cases)):
                raise ValueError('run_free requires Python tasks; select Python cases or another method')
        manifest = dict(version=1, rows=rows, settings=settings, host_system=platform.system(),
                        relaxed_storage=platform.system() == 'Darwin',
                        platform='linux/amd64', cases=cases)
    errors, notes = preflight(settings, credentials, rows)
    print(f'Output: {output}\nCombinations: {len(rows)}; jobs: {args.jobs}', flush=True)
    for row in rows:
        print(f"  {row['method']} / {row['agent']} / {row['model']} / {row['case']}")
    for message in skipped:
        print('SKIP:', message)
    for message in notes:
        print('NOTE:', message)
    for message in errors:
        print('MISSING:', message)
    if errors or args.dry_run:
        return 2 if errors else 0
    # Snapshot only sanitized configuration. Never serialize process environment.
    if not args.resume:
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / 'pilot.json', manifest)
    elif platform.system() != manifest['host_system']:
        raise ValueError('Resume on the original host platform; start a new output for Linux measurements')
    with (output / '.pilot.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('This run is already active') from None
        from .pilot_images import prepare_images
        if args.resume:
            ensure_stopped(output)
        images = prepare_images(output, rows, settings, jobs=args.jobs)
        volume = 'tokenana-channel-' + uuid4().hex
        subprocess.run(['docker', 'volume', 'create', volume], check=True, stdout=subprocess.DEVNULL)
        with (output / 'channel-volumes.jsonl').open('a') as stream:
            stream.write(json.dumps({'name': volume, 'retained': True}) + '\n')
        active, failures = {}, []
        import threading
        active_lock = threading.Lock()
        stopping = threading.Event()
        def run_one(row):
            if stopping.is_set():
                return
            parent = output / 'combinations' / row['id']
            if (parent / 'result.json').exists():
                result = json.loads((parent / 'result.json').read_text())
                if result['status'] in ('completed', 'agent_failed', 'not_submitted'):
                    if result['status'] != 'completed':
                        failures.append(row['id'])
                    return
            parent.mkdir(parents=True, exist_ok=True)
            item = settings['models'][row['model']]
            keys = [item['api_key_env']]
            helper = settings['agent_diet_auxiliary']
            if row['method'] == 'agent_diet':
                keys.append(helper['api_key_env'])
            runtime = dict(network='none', retain_files=True, never_regenerate=True, relaxed_storage=manifest['relaxed_storage'],
                images={row['case']: images[row['agent']][row['case']]},
                verifier_images={row['case']: images['verifier'][row['case']]},
                verifier_python=settings.get('runtime_paths', {}).get('verifier_python') or '/opt/tokenana/bin/python',
                model_channel={'kind': 'unix_socket', 'python': '/opt/tokenana/bin/python'},
                environment_names=[item['api_key_env']])
            if row['agent'] == 'opencode':
                runtime['opencode_config_root'] = agent_options(row['agent'], item, settings)['config_root']
            payload = parent / 'payload.json'
            write_json(payload, dict(row=row, settings=settings, runtime=runtime))
            name = 'tokenana-controller-' + uuid4().hex
            command = ['docker', 'run', '--name', name, '--init', '--platform', 'linux/amd64',
                '-v', f'{ROOT}:{ROOT}:ro', '-v', f'{output}:{output}', '-v', '/var/run/docker.sock:/var/run/docker.sock',
                '-v', f'{volume}:/tc', '-w', str(ROOT), '-e', 'PYTHONDONTWRITEBYTECODE=1',
                '-e', 'TOKENANA_CHANNEL_ROOT=/tc', '-e', f'TOKENANA_CHANNEL_VOLUME={volume}']
            for key in keys:
                command += ['-e', key]
            command += [images['controller'], '/opt/tokenana/bin/python', '-B', '-m', 'src.pilot', '--_worker', str(payload)]
            env = {**os.environ, **{k: credentials.get(k, os.environ.get(k, '')) for k in keys}}
            print('START:', row['id'], flush=True)
            with (parent / 'launcher.log').open('a') as log:
                with active_lock:
                    if stopping.is_set():
                        return
                    write_json(parent / 'controller.json', {'name': name, 'returncode': None, 'retained': True})
                    process = subprocess.Popen(command, stdout=log, stderr=log, env=env)
                    active[name] = process
                try:
                    code = process.wait()
                finally:
                    with active_lock:
                        active.pop(name, None)
            if code:
                failures.append(row['id'])
                if not (parent / 'result.json').exists():
                    write_json(parent / 'result.json', {'status': 'failed', 'returncode': code,
                               'error': 'Controller exited before saving a result; inspect launcher.log'})
            # Controller contains no task state; retain it on failures for diagnosis.
            write_json(parent / 'controller.json', {'name': name, 'returncode': code, 'retained': bool(code)})
            if not code:
                subprocess.run(['docker', 'rm', name], check=True, stdout=subprocess.DEVNULL)
            print('END:', row['id'], 'exit', code, flush=True)
        pool = ThreadPoolExecutor(max_workers=args.jobs)
        futures = []
        try:
            for row in rows:
                futures.append(pool.submit(run_one, row))
            for future in as_completed(futures):
                future.result()
                summarize(output, rows)
        except BaseException:
            stopping.set()
            for future in futures:
                future.cancel()
            with active_lock:
                names = list(active)
            for name in names:
                # SIGINT gives worker finally blocks a chance to archive containers.
                subprocess.run(['docker', 'kill', '--signal', 'SIGINT', name], capture_output=True)
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            summarize(output, rows)
            # Keep the volume; interrupted containers may still depend on it.
            write_json(output / 'channel-volume.json', {'name': volume, 'retained': True})
    print(f'Results: {output / "summary.csv"}\nResume: bash scripts/run-deepswe-pilot.sh --resume {shlex.quote(str(output))}')
    return 1 if failures else 0


def ensure_stopped(output):
    """Never start another generation while an interrupted task may still run."""
    names = set()
    for path in output.rglob('container.jsonl'):
        for line in path.read_text().splitlines():
            try:
                names.add(json.loads(line)['container'])
            except (ValueError, KeyError):
                continue
    for path in output.rglob('controller.json'):
        names.add(json.loads(path.read_text())['name'])
    for name in names:
        result = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', name],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip() == 'true':
            raise ValueError(f'Interrupted container is still running: {name}. Stop it before --resume.')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(2)
