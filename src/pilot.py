"""Portable DeepSWE matrix launcher. Host side uses only the standard library."""
import argparse
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import replace
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
from .pilot_reporting import summarize, run_directory, refresh, print_summary
from .pricing import load_pricing, saved_pricing
from .study import DATASETS, METHODS as SUPPORTED_METHODS, MODELS as SUPPORTED_MODELS, supported

ROOT = Path(__file__).resolve().parents[1]
METHODS = ('run_free', 'turn_control', 'agent_diet', 'eet')
AGENTS = ('codex', 'mini', 'trae', 'opencode')
MODELS = ('gpt-5.6-sol', 'claude-opus-5', 'deepseek-v4.1-flash', 'qwen3.8-max')
AGENT_DIR = {'mini': 'mini_swe_agent', **{a: a for a in AGENTS if a != 'mini'}}
BUDGET = dict(zip(MODELS, ('deepswe_gpt', 'deepswe_claude', 'deepswe_deepseek', 'deepswe_qwen')))
LOCAL = ROOT / 'config/local/deepswe-pilot'


def parser():
    p = argparse.ArgumentParser(description='Run fixed experiments, retaining all attempts and token accounts.')
    p.add_argument('--study', type=Path, help='Fixed study TOML; bypass per-arm next-N and cross-run skipping')
    p.add_argument('--dataset', choices=tuple(DATASETS), help='Dataset for an ad-hoc run (default: deepswe)')
    for flag in ('method', 'model', 'agent'):
        p.add_argument('--' + flag, help='Comma-separated selection')
    cases = p.add_mutually_exclusive_group()
    cases.add_argument('--case', help='Comma-separated task IDs')
    cases.add_argument('--count', '--cases', dest='cases', type=int,
                       help='Next N unrecorded tasks per model/agent/method (default: 1)')
    p.add_argument('--jobs', type=int, default=1)
    output = p.add_mutually_exclusive_group()
    output.add_argument('--output', type=Path)
    output.add_argument('--resume', type=Path)
    output.add_argument('--summarize', type=Path, help='Refresh saved reports offline; no generation or evaluation')
    p.add_argument('--pricing', type=Path, help='Price TOML for a new run or explicit offline repricing')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--rerun', action='store_true', help='Run selected combinations again despite saved pilot history')
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
                if method == 'baseline' and agent == 'mini' and model in (
                        'gpt-5.6-sol', 'claude-opus-5', 'qwen3.8-max'):
                    skipped.append(f'{method}/{agent}/{model}: mini baseline excluded from ad-hoc runs')
                    continue
                if not supported(method, agent, model):
                    skipped.append(f'{method}/{agent}/{model}: outside the supported method/agent/model matrix')
                    continue
                for case in cases:
                    rows.append(dict(method=method, agent=agent, model=model, case=case,
                                     id=f'{method}__{agent}__{model}__{case}'))
    if not rows:
        raise ValueError('Selection contains no supported method/agent/model combinations')
    return rows, skipped


def fixed_cases():
    path = ROOT / 'config/studies/deepswe-113.txt'
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
    if raw.get('methods'):
        settings['methods'] = raw['methods']
    if raw.get('verified_evaluation'):
        settings['verified_evaluation'] = raw['verified_evaluation']
    if 'retention_mode' in raw:
        from .retention import retention_mode
        settings['retention_mode'] = retention_mode(raw)
    # Keep legacy in-memory credential references stable when adding models.
    for index, name in enumerate((*MODELS, 'agent_diet_auxiliary', 'Qwen3-Coder-Next')):
        if name == 'agent_diet_auxiliary':
            item = dict(raw.get(name, {}))
        else:
            candidates = [v for v in raw.get('models', {}).values() if v.get('name') == name]
            if name not in MODELS and not candidates:
                continue
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
    if any(r['method'] == 'turn_control' and r['model'] not in BUDGET and not r.get('baseline_id')
           and not settings.get('methods', {}).get('turn_control') for r in rows):
        errors.append('turn_control: freeze a matching baseline budget for the common-model group before execution')
    for method in ('attn_compress', 'swe_pruner_pro', 'eet'):
        if any(r['method'] == method for r in rows):
            adapter = load_adapter(load_component(ROOT / 'methods' / method, 'method', {}))
            try:
                adapter.validate_options(settings.get('methods', {}).get(method, {}))
            except ValueError as error:
                errors.append(f'{method}: {error}')
    for name in sorted({r['model'] for r in rows}):
        item = settings['models'].get(name, {})
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
    dataset = row.get('dataset', 'deepswe')
    method = row['method']
    if method == 'turn_control':
        if row.get('method_options'):
            options = row['method_options']
        elif dataset == 'deepswe' and settings.get('methods', {}).get(method):
            options = settings['methods'][method]
        else:
            options = {'budget_profile': (BUDGET[row['model']] if dataset == 'deepswe' else
                       {'gpt-5.6-sol': 'gpt', 'claude-opus-5': 'claude'}[row['model']])}
    elif row['method'] == 'agent_diet':
        helper = settings['agent_diet_auxiliary']
        options = dict(helper_model='gpt-5-mini', helper_base_url=helper['base_url'],
                       helper_api_key_env=helper['api_key_env'])
    elif method in ('attn_compress', 'swe_pruner_pro', 'eet'):
        options = dict(settings.get('methods', {}).get(method, {}))
        if method == 'eet' and dataset == 'deepswe':
            options['retrieval_scope'] = 'cross_repository'
    if method == 'run_free' and row.get('language', 'python') != 'python':
        method = 'run_free_multilingual'
    return ExperimentConfig(path,
        load_component(ROOT / 'methods' / method, 'method', options),
        load_component(ROOT / 'agents' / AGENT_DIR[row['agent']], 'agent', agent_options(row['agent'], model, settings)),
        load_component(ROOT / 'datasets' / DATASETS[dataset], 'dataset', {'task_ids': [row['case']]}),
        ModelConfig(**{k: model[k] for k in ('name', 'provider', 'model_id', 'protocol', 'base_url', 'api_key_env')}))


def freeze_profiles(rows, settings, budgets=None, *, library_root=None):
    """Share resolved options across tasks; keep multilingual method selection explicit."""
    from .config import config_dict
    profiles = {}
    for row in rows:
        key = '__'.join((row.get('dataset', 'deepswe'), row['method'], row['agent'], row['model'],
                         'multilingual' if row['method'] == 'run_free' and row.get('language', 'python') != 'python'
                         else 'default'))
        row['profile_id'] = key
        if key not in profiles:
            resolved = row
            if row['method'] == 'turn_control' and row.get('baseline_id'):
                budget = (budgets or {}).get(row['baseline_id'])
                if budget is None:
                    continue
                resolved = {**row, 'method_options': {'frozen_budget': budget}}
            config = config_dict(experiment(resolved, settings, ROOT / 'experiments/paper.toml'))
            if row['method'] == 'eet' and library_root is not None:
                import shutil
                from methods.eet.adapter import MINI, TRAE
                options = config['method']['options']
                library = Path(options.get('experience_library') or (
                    TRAE / 'prompt/extracted_experiences_summarized_merged.jsonl' if row['agent'] == 'trae' else
                    MINI / 'experience/extracted_experiences_summarized.jsonl'))
                library_root.mkdir(parents=True, exist_ok=True)
                target = library_root / (key + '.jsonl')
                if not target.exists():
                    shutil.copyfile(library, target)
                options['experience_library'] = str(target)
            config.pop('path')
            config['dataset']['options'].pop('task_ids')
            profiles[key] = config
    return profiles


def worker(payload_path):
    from .execution import run_experiment
    from .evaluation import evaluate
    from .analysis import analyze_run
    payload = json.loads(payload_path.read_text())
    row, settings = payload['row'], payload['settings']
    directory = payload_path.parent / 'run'
    if payload.get('profile'):
        from .config import restore_experiment
        config = restore_experiment(payload['profile'], payload_path, task_id=row['case'])
    else:
        config = experiment(row, settings, payload_path)
    if (directory / 'config.json').exists():
        # Moving a finished pilot retains its original path via a symlink. Keep
        # the saved configuration identity while using the new output location.
        config = replace(config, path=Path(json.loads((directory / 'config.json').read_text())['path']))
    runtime = payload['runtime']
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    result = {'status': 'interrupted'}
    try:
        with span(payload_path.parent, 'case_total', case_id=row['case']):
            run_experiment(config, runtime, directory, resume=(directory / 'state.json').exists(),
                           pricing=payload.get('pricing'))
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
        result = {'status': 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                  'error_type': type(error).__name__, 'error': str(error)}
        raise
    finally:
        result['timing_seconds'] = totals(payload_path.parent)
        write_json(payload_path.parent / 'result.json', result)
    return 0 if result['status'] == 'completed' else 1


def select_run(args, settings):
    if args.resume:
        output = args.resume.resolve()
        if any(v is not None for v in (args.dataset, args.method, args.model, args.agent, args.case, args.cases)):
            raise ValueError('--resume uses the saved selection; only --jobs may change')
        manifest = json.loads((output / 'pilot.json').read_text())
        rows, skipped = manifest['rows'], []
        if settings != manifest['settings']:
            raise ValueError('Settings differ from saved run; restore settings or start a new output directory')
    elif args.study:
        from .study import load_study, study_rows
        study = load_study(args.study)
        rows, skipped = study_rows(study), []
        manifest = dict(version=2, study=study, rows=rows, settings=settings,
                        host_system=platform.system(), relaxed_storage=platform.system() == 'Darwin',
                        platform='linux/amd64', cases=list(dict.fromkeys(r['case'] for r in rows)))
        output = args.output.resolve() if args.output else run_directory(rows, ROOT / 'runs')
    else:
        dataset = args.dataset or 'deepswe'
        from .study import task_catalog
        catalog = task_catalog(dataset)
        cases = fixed_cases() if dataset == 'deepswe' else list(catalog)
        if args.case:
            requested = list(dict.fromkeys(args.case.split(',')))
            if set(requested) - catalog.keys():
                raise ValueError('Unknown --case task ID')
            cases = requested
        elif args.cases is not None:
            if args.cases < 1:
                raise ValueError('--count/--cases must be positive')
        rows, skipped = matrix(selection(args.method or ','.join(METHODS), SUPPORTED_METHODS, 'method'),
                               selection(args.agent, AGENTS, 'agent'),
                               selection(args.model or ','.join(MODELS), SUPPORTED_MODELS, 'model'), cases)
        for row in rows:
            row.update(dataset=dataset, language=catalog[row['case']]['language'])
            if dataset != 'deepswe':
                row['id'] = dataset + '__' + row['id']
        manifest = dict(version=1, rows=rows, settings=settings, host_system=platform.system(),
                        relaxed_storage=platform.system() == 'Darwin',
                        platform='linux/amd64', cases=cases)
        if not args.rerun:
            from .pilot_history import filter_history
            roots = {ROOT / 'runs'}
            if args.output:
                roots.add(args.output.resolve().parent)
            rows, history, warnings = filter_history(rows, settings, roots)
            for item in history:
                print(f"SKIP already recorded: {item['row']['id']} [{item['status']}] -> {item['source']}")
            for warning in warnings:
                print('WARNING:', warning)
            if not rows:
                print('All selected combinations are already recorded; no experiment started. Use --resume or --rerun explicitly.')
                return None
            manifest.update(rows=rows, skipped_history=history)
        if not args.case:
            rows = next_rows(rows, args.cases or 1)
            manifest.update(rows=rows, cases=list(dict.fromkeys(r['case'] for r in rows)),
                            requested_cases_per_combination=args.cases or 1)
            print(f'Sequential selection: up to {args.cases or 1} remaining cases per model/agent/method; '
                  f'{len(rows)} case combinations selected.')
        manifest['dedup_reservation'] = True
        output = args.output.resolve() if args.output else run_directory(rows, ROOT / 'runs')
    return rows, skipped, manifest, output


def case_runtime(row, settings, manifest, images, parent, *, resume=False):
    item = settings['models'][row['model']]
    runtime = dict(network='none', retain_files=True, never_regenerate=True, relaxed_storage=manifest['relaxed_storage'],
        images={row['case']: images[row['agent']][row['case']]},
        verifier_images={row['case']: images['verifier'][row['case']]} if row.get('dataset', 'deepswe') == 'deepswe' else {},
        verifier_python=settings.get('runtime_paths', {}).get('verifier_python') or '/opt/tokenana/bin/python',
        model_channel={'kind': 'unix_socket', 'python': settings.get('runtime_paths', {}).get('python_executable') or '/opt/tokenana/bin/python'},
        environment_names=[item['api_key_env']])
    runtime['retention_mode'] = manifest.get('retention_mode', 'full')
    if row.get('dataset') == 'verified':
        runtime['command_prefix'] = []
        runtime['verified_evaluation'] = {**settings.get('verified_evaluation', {}),
            'image': images['verified_verifier']}
    if row['agent'] == 'opencode':
        runtime['opencode_config_root'] = agent_options(row['agent'], item, settings)['config_root']
    saved_runtime = parent / 'run/runtime.json'
    if resume and saved_runtime.exists():
        # Resume with the exact runtime snapshot; never rewrite old evidence.
        runtime = json.loads(saved_runtime.read_text())
    return runtime


def run_phases(rows, manifest, settings, output, run_one, *, jobs, stop):
    pool = ThreadPoolExecutor(max_workers=jobs)
    futures = set()
    try:
        # Bound submissions, so a fatal future cannot race thousands of queued
        # jobs into execution before the coordinator observes the exception.
        phases = ([r for r in rows if r['method'] == 'baseline'],
                  [r for r in rows if r['method'] != 'baseline']) if manifest.get('study') else (rows,)
        for phase_index, phase in enumerate(phases):
            if manifest.get('study') and phase_index == 1:
                from .budgets import freeze_budget
                budgets = {}
                for row in phase:
                    if row['method'] == 'turn_control' and row.get('baseline_id') and row['profile_id'] not in manifest['profiles']:
                        key = row['baseline_id']
                        if key not in budgets:
                            budgets[key] = freeze_budget(output, rows, configuration_id=key)
                new_profiles = freeze_profiles(phase, settings, budgets, library_root=output / 'inputs')
                for key, value in new_profiles.items():
                    manifest['profiles'].setdefault(key, value)
                write_json(output / 'pilot.json', manifest)
            pending = iter(phase)
            for _ in range(jobs):
                row = next(pending, None)
                if row is not None:
                    futures.add(pool.submit(run_one, row))
            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
                summarize(output, rows)
                for _ in done:
                    row = next(pending, None)
                    if row is not None:
                        futures.add(pool.submit(run_one, row))
    except BaseException:
        stop()
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        summarize(output, rows)


def run_matrix(output, rows, manifest, settings, credentials, prices, *, jobs, resume):
    with (output / '.pilot.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('This run is already active') from None
        from .pilot_images import prepare_images
        if resume:
            ensure_stopped(output)
        images = prepare_images(output, rows, settings, jobs=jobs)
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
                    if result['status'] != 'completed' or result.get('controller_cleanup_error'):
                        failures.append(row['id'])
                    return
            parent.mkdir(parents=True, exist_ok=True)
            item = settings['models'][row['model']]
            keys = [item['api_key_env']]
            helper = settings['agent_diet_auxiliary']
            if row['method'] == 'agent_diet':
                keys.append(helper['api_key_env'])
            runtime = case_runtime(row, settings, manifest, images, parent, resume=resume)
            payload = parent / 'payload.json'
            write_json(payload, dict(row=row, settings=settings, runtime=runtime, pricing=prices,
                                    profile=manifest.get('profiles', {}).get(row.get('profile_id'))))
            name = 'tokenana-controller-' + uuid4().hex
            command = ['docker', 'run', '--name', name, '--init', '--platform', 'linux/amd64',
                '-v', f'{ROOT}:{ROOT}:ro', '-v', f'{output}:{output}', '-v', '/var/run/docker.sock:/var/run/docker.sock',
                '-v', f'{volume}:/tc', '-w', str(ROOT), '-e', 'PYTHONDONTWRITEBYTECODE=1',
                '-e', 'TOKENANA_CHANNEL_ROOT=/tc', '-e', f'TOKENANA_CHANNEL_VOLUME={volume}']
            for key in keys:
                command += ['-e', key]
            if row['method'] == 'swe_pruner_pro':
                head = settings['methods']['swe_pruner_pro']['head']
                command += ['-v', f'{head}:{head}:ro']
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
                    from .pilot_progress import Progress
                    progress = Progress(parent)
                    def poll_progress():
                        try:
                            progress.poll()
                        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                            print('Progress read unavailable:', type(error).__name__, flush=True)
                    while True:
                        poll_progress()
                        try:
                            code = process.wait(timeout=2)
                            poll_progress()
                            break
                        except subprocess.TimeoutExpired:
                            continue
                finally:
                    with active_lock:
                        if process.poll() is not None:
                            active.pop(name, None)
            if code:
                failures.append(row['id'])
                if not (parent / 'result.json').exists():
                    write_json(parent / 'result.json', {'status': 'failed', 'returncode': code,
                               'error': 'Controller exited before saving a result; inspect launcher.log'})
            # Controller contains no task state; retain it on failures for diagnosis.
            retained = True
            if not code:
                try:
                    subprocess.run(['docker', 'rm', name], check=True, stdout=subprocess.DEVNULL, timeout=120)
                    retained = False
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    # The experiment result remains valid; keep cleanup failure visible.
                    result = json.loads((parent / 'result.json').read_text())
                    result['controller_cleanup_error'] = type(error).__name__
                    write_json(parent / 'result.json', result)
                    failures.append(row['id'])
            write_json(parent / 'controller.json', {'name': name, 'returncode': code, 'retained': retained})
            print('END:', row['id'], 'exit', code, flush=True)
        def stop():
            stopping.set()
            with active_lock:
                names = list(active)
            for name in names:
                # SIGINT lets workers archive task containers before exiting.
                subprocess.run(['docker', 'kill', '--signal', 'SIGINT', name], capture_output=True)
        try:
            run_phases(rows, manifest, settings, output, run_one, jobs=jobs, stop=stop)
        finally:
            # Interrupted containers may still depend on the volume.
            write_json(output / 'channel-volume.json', {'name': volume, 'retained': True})
    return failures


def main(argv=None):
    with ExitStack() as stack:
        return _main(argv, stack)


def _main(argv, stack):
    args = parser().parse_args(argv)
    if args.study and any(v is not None for v in
            (args.dataset, args.method, args.model, args.agent, args.case, args.cases, args.resume, args.summarize, args.pricing)):
        raise ValueError('--study fixes the matrix and prices; do not combine it with ad-hoc selectors or resume')
    if args.study and args.rerun:
        raise ValueError('A study has one generation per task; use --resume for recovery')
    if args.rerun and (args.resume or args.summarize):
        raise ValueError('--rerun is only for a new run, not --resume/--summarize')
    if args._worker:
        return worker(args._worker)
    if args.summarize:
        if args.dry_run or any(v is not None for v in (args.dataset, args.method, args.model, args.agent, args.case, args.cases)):
            raise ValueError('--summarize uses saved selections and cannot combine with --dry-run')
        records = refresh(args.summarize, args.pricing)
        print_summary(records)
        print(f'Refreshed {len(records)} combinations: {args.summarize / "summary.md"}')
        return 0
    if args.resume and args.pricing:
        raise ValueError('Resume preserves saved pricing; use --summarize --pricing to reprice offline')
    if args.jobs < 1:
        raise ValueError('--jobs must be positive')
    history_lock = None
    if not args.resume and not args.dry_run:
        (ROOT / 'runs').mkdir(parents=True, exist_ok=True)
        history_lock = stack.enter_context((ROOT / 'runs/.pilot-history.lock').open('a'))
        fcntl.flock(history_lock, fcntl.LOCK_EX)
    settings, credentials = read_settings()
    selected = select_run(args, settings)
    if selected is None:
        return 0
    rows, skipped, manifest, output = selected
    if any(',' in str(path) or ':' in str(path) for path in (ROOT, output)):
        raise ValueError('Docker mount paths must not contain commas or colons')
    if manifest.get('study'):
        measurement_jobs = manifest['study']['execution'].get('measurement_jobs', 1)
        if args.jobs != measurement_jobs:
            raise ValueError('Paper execution requires the frozen measurement_jobs; validation concurrency is separate')
        if args.resume and manifest.get('measurement_jobs') != measurement_jobs:
            raise ValueError('Saved study has no matching frozen measurement concurrency')
        manifest['measurement_jobs'] = measurement_jobs
    if not args.resume:
        from .retention import retention_mode
        manifest['retention_mode'] = retention_mode({'retention_mode': settings.get('retention_mode', 'research')})
    prices = (saved_pricing(output) if args.resume else manifest['study']['pricing'] if args.study
              else load_pricing(args.pricing) if args.pricing else load_pricing())
    errors, notes = preflight(settings, credentials, rows)
    if manifest.get('study') and any(r['method'] == 'turn_control' and r.get('baseline_id') for r in rows):
        notes.append('Baseline phase precedes methods; turn_control budgets are frozen from matching native summaries.')
    print(f'Output: {output}\nCombinations: {len(rows)}; jobs: {args.jobs}', flush=True)
    if manifest.get('study'):
        for config in manifest['study']['configurations']:
            print(f"  {config['id']}: {config['selected']} fixed tasks")
    else:
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
        if args.output:
            output.mkdir(parents=True, exist_ok=False)
        else:
            output = run_directory(rows, ROOT / 'runs', create=True)
            print(f'Reserved output: {output}', flush=True)
        if manifest.get('study'):
            manifest['profiles'] = freeze_profiles(rows, settings, library_root=output / 'inputs')
        write_json(output / 'pricing.json', prices)
        write_json(output / 'pilot.json', manifest)
        if manifest.get('study'):
            write_json(output / 'study.json', manifest['study'])
        from .pilot_history import register_output
        register_output(ROOT / 'runs', output)
        # Publish the reservation before another launcher inspects history.
        if history_lock:
            fcntl.flock(history_lock, fcntl.LOCK_UN)
    elif platform.system() != manifest['host_system']:
        raise ValueError('Resume on the original host platform; start a new output for Linux measurements')
    failures = run_matrix(output, rows, manifest, settings, credentials, prices,
                          jobs=args.jobs, resume=bool(args.resume))
    summary = json.loads((output / 'summary.json').read_text())
    cost = summary['corrected_v2_cost']
    print_summary(summary['cases'])
    print(f"Estimated cost USD: {cost['total_usd']}; known subtotal: {cost['known_subtotal_usd']}; complete={cost['complete']}")
    print(f'Results: {output / "summary.md"}\nResume: bash scripts/run-experiments.sh --resume {shlex.quote(str(output))}')
    return 1 if failures else 0


def next_rows(rows, count):
    """Preserve fixed dataset order, taking N pending tasks per experiment arm."""
    counts, selected = {}, []
    for row in rows:
        key = (row.get('dataset', 'deepswe'), row['method'], row['agent'], row['model'])
        if counts.get(key, 0) < count:
            selected.append(row)
            counts[key] = counts.get(key, 0) + 1
    return selected


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
