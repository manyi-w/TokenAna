"""Run the independent native session build; no subprocess protocol translation."""
from contextlib import ExitStack
import json
from pathlib import PurePosixPath
from shlex import join, quote
import subprocess
import time

from src.components import ConfigError
from src.interfaces import AgentResult
from src.model_channel import recording_model_channel
from src.patches import capture_patch
from src.session_channel import session_server
from src.workspaces import execution_timeout
from .controlled_runner import _arguments

VERSION = 'codex-session-compatible-v1'


def validate_options(options):
    if not PurePosixPath(options.get('session_executable', '')).is_absolute() or options.get('session_version') != VERSION:
        raise ConfigError('Codex session requires absolute prepared session_executable and session_version=' + VERSION)
    if not options.get('record_raw_usage'):
        raise ConfigError('Codex session requires raw usage recording')
    timeout = options.get('session_timeout', 120)
    if type(timeout) not in (float, int) or not 0 < timeout <= 3600:
        raise ConfigError('session_timeout must be positive and at most 3600 seconds')


def run(prompt, workspace, options, callback):
    validate_options(options)
    artifacts = workspace.new_artifacts()
    host, target = artifacts.host, PurePosixPath(artifacts.execution)
    (host / 'prompt.txt').write_text(prompt)
    (host / 'session-request.json').write_text(json.dumps({'version': VERSION, 'timeout': options.get('session_timeout', 120)}))
    (host / 'session-version.json').write_text(json.dumps({'version': VERSION, 'native_history': 'codex-home', 'wire_protocol': options.get('wire_api', 'responses')}))
    (host / 'codex-home').mkdir()
    errors, trace, process, control = [], [], None, None
    budget = options.get('turn_control')
    if budget:
        from .summary import VERSION as CONTROL_VERSION, PROFILES
        profiles = [name for name, pair in PROFILES.items() if pair == (budget['initial'], budget['final'])]
        if len(profiles) != 1:
            raise ConfigError('Codex accepts only approved turn budget tiers')
        (host / 'control-request.json').write_text(json.dumps({'version': CONTROL_VERSION, 'profile': profiles[0]}))
    started = time.monotonic()
    timeout = execution_timeout(workspace, options)
    if type(timeout) not in (int, float) or not 0 < timeout < float('inf'):
        raise ConfigError('Codex session requires finite positive execution timeout')
    with ExitStack() as stack:
        session = stack.enter_context(session_server(artifacts, callback, identity=getattr(workspace, 'usage_identity', {})))
        def ready():
            admitted = (host / 'session-hook.ready').is_file() and not session.termination and not session.finished
            if budget:
                from .summary import control_ready
                admitted = admitted and control_ready(host, budget)
            return admitted
        def attribution(request, headers):
            purpose = next((value for key, value in headers.items() if key.lower() == 'x-tokenana-purpose'), 'unknown')
            return {'purpose': purpose if purpose in ('main', 'agent_auxiliary') else 'unknown'}
        endpoint = stack.enter_context(recording_model_channel(workspace, artifacts, options['model_base_url'],
            protocol=options.get('wire_api', 'responses'), provider=options.get('usage_provider'),
            request_guard=ready, attribution_resolver=attribution, timeout=options.get('proxy_timeout', 60)))
        argv = ['timeout', '--signal=TERM', '--kill-after=5s', f'{timeout}s', 'env',
                'TOKENANA_SESSION_DIRECTORY=' + str(target), 'CODEX_HOME=' + str(target / 'codex-home')]
        if budget:
            argv.append('TOKENANA_CONTROL_DIRECTORY=' + str(target))
        argv += _arguments({**options, 'controlled_executable': options['session_executable']}, endpoint)
        script = (join(argv) + ' "$(cat ' + quote(str(target / 'prompt.txt')) + ')" > ' +
                  quote(str(target / 'trace.jsonl')) + ' 2> ' + quote(str(target / 'stderr.txt')))
        try:
            process = subprocess.run(workspace.launch_command(['bash', '-c', 'exec ' + script]),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout + 15)
            (host / 'transport-stdout.txt').write_text(process.stdout)
            (host / 'transport-stderr.txt').write_text(process.stderr)
            if process.returncode:
                errors.append(f'Codex exit code {process.returncode}')
        except subprocess.SubprocessError as error:
            errors.append(type(error).__name__)
    (host / 'process.json').write_text(json.dumps({'returncode': process.returncode if process else None}))
    patch = ''
    if process is not None:
        try:
            patch = capture_patch(workspace, artifacts, timeout=options.get('diff_timeout', 60))
        except Exception as error:
            errors.append('patch capture failed: ' + type(error).__name__)
    try:
        trace = [json.loads(line) for line in (host / 'trace.jsonl').read_text().splitlines() if line.strip()]
        if any(row.get('type') in ('error', 'turn.failed') for row in trace) or not any(row.get('type') == 'turn.completed' for row in trace):
            errors.append('No successful native Codex completion')
        outcome = json.loads((host / 'session-outcome.json').read_text())
        if not outcome.get('complete') or outcome.get('termination'):
            errors.append('Incomplete or terminated method session')
        if budget:
            from .summary import load_outcome
            control, _, _, _, _ = load_outcome(host, budget)
            if not control['success']:
                errors.append('Controlled generation incomplete')
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append('Missing native/session evidence: ' + type(error).__name__)
    eligible = not errors and bool(patch.strip())
    (host / 'diagnostic.diff').write_text(patch)
    (host / 'patch.diff').write_text(patch if eligible else '')
    tokens = sum(row.get('usage', {}).get('input_tokens', 0) + row.get('usage', {}).get('output_tokens', 0)
                 for row in trace if row.get('type') == 'turn.completed')
    return AgentResult('codex', prompt, '', tokens, 0, time.monotonic() - started, trace,
                       '; '.join(errors) or None, artifacts, eligible, control)
