"""Native paper agents hosted by the shared task/recovery/retention executor."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .accounting import OriginalCase
from .interfaces import AgentResult, MethodResult
from .raw_usage import read_case_usage
from .records import write_json

ROOT = Path(__file__).resolve().parents[1]


class NativeMethod:
    version = 'rq1-native-v1'
    resume_policy = 'never_regenerate'
    original_trace_formats = ('rq1-native',)

    def run(self, task, agent, workspace, options):
        return MethodResult([agent.run(task.problem_statement, workspace,
            {'task': dict(instance_id=task.instance_id, repo=task.repo,
                          base_commit=task.base_commit, problem_statement=task.problem_statement)})])

    def original_accounting(self, cases):
        from .accounting_trace import calc, metric, missing
        names = {c.method_data.get('rq1_method') for c in cases} - {None}
        if len(names) != 1:
            return dict(rule='rq1-native-unavailable', cases_counted=0, note='Native evidence unavailable.',
                metrics={k: metric(missing('原生轨迹未保存'), complete=False) for k in ('input', 'output', 'total')})
        method = names.pop()
        if method in ('agent_diet', 'attn_compress'):
            from importlib import import_module
            cls = getattr(import_module('methods.' + method + '.adapter'),
                          'AgentDiet' if method == 'agent_diet' else 'AttnCompress')
            return cls().original_accounting(cases)
        selected = [c for c in cases if c.trace is not None and
                    (method != 'run_free' or (c.patch and c.patch.strip()))]
        from .accounting_trace import constant
        denominator = constant(len(selected), 'src/rq1_native.py:original_accounting', '原筛选后任务数')
        metrics = {}
        for key in ('input', 'output'):
            values = [node for c in selected for node in c.method_data.get('native_calculation', {}).get(key, [])]
            metrics[key] = metric(calc('sum', *values), denominator,
                                  floor=method == 'run_free', mean_defined=method == 'run_free')
        metrics['total'] = metric(calc('sum', *(metrics[k]['calculation']['sum'] for k in ('input', 'output'))),
                                  denominator, floor=method == 'run_free', mean_defined=method == 'run_free')
        return dict(rule='rq1-' + method + '-original', cases_counted=len(selected), metrics=metrics,
                    note='原统计口径；完整 API 覆盖由 corrected-v2-api 单独判断。')


class NativeAgent:
    original_trace_format = 'rq1-native'
    raw_usage_protocols = ('anthropic_messages', 'chat_completions', 'gemini_generate_content')
    @staticmethod
    def read_case_usage(directory, **identity):
        usage = read_case_usage(directory, **identity)
        try:
            meta = json.loads((directory / 'rq1.json').read_text())
            if meta['method'] != 'run_free':
                return usage
            responses = set()
            path = directory / 'trace.jsonl'
            for line in path.read_text().splitlines() if path.exists() else []:
                try:
                    event = json.loads(line)
                    if event.get('type') == 'assistant':
                        responses.add(event.get('message', {}).get('id'))
                except ValueError:
                    continue
        except (OSError, ValueError, KeyError):
            return usage
        from dataclasses import replace
        observations = [replace(o, purpose='main' if o.observation_id.rsplit('/', 1)[-1] in responses
                                else 'unknown') for o in usage.observations]
        main_requests = {o.operation_id for o in observations if o.purpose == 'main'}
        operations = [replace(o, purpose='main' if o.operation_id in main_requests else 'unknown')
                      for o in usage.operations]
        return replace(usage, observations=observations, operations=operations)

    def plan(self, options):
        return {'missing_options': [k for k in ('method', 'spec', 'endpoint') if k not in options]}

    def validate_raw_usage(self, options):
        if not options.get('record_raw_usage'):
            raise ValueError('RQ1 requires raw API recording')

    def run(self, prompt, workspace, options):
        if options['method'] == 'turn_control':
            from agents.trae.adapter import Trae
            settings = {**options['trae'], 'model': options['spec']['model_id'],
                'model_base_url': options['endpoint'], 'model_api_key_env': options['api_key_env'],
                'model_protocol': 'gemini_generate_content', 'model_provider': 'google',
                'record_raw_usage': True, 'turn_control': {'initial': 29, 'final': 45}}
            result = Trae().run(prompt, workspace, settings)
            write_json(result.artifacts.host / 'rq1.json', {'method': 'turn_control', 'spec': options['spec']})
            return result
        artifacts = workspace.new_artifacts()
        host = artifacts.host
        write_json(host / 'rq1.json', {'method': options['method'], 'spec': options['spec']})
        if options['method'] == 'run_free':
            return _run_free(workspace, artifacts, options)
        return _run_expert(workspace, artifacts, options)

    def read_original_case(self, directory, case_id):
        from .accounting_trace import atom
        meta = json.loads((directory / 'rq1.json').read_text())
        method = meta['method']
        patch = (directory / 'patch.diff').read_text() if (directory / 'patch.diff').exists() else None
        data = {'rq1_method': method, '_source': str(directory / 'native-trajectory.json')}
        if method in ('agent_diet', 'attn_compress'):
            path = directory / 'native-trajectory.json'
            document = json.loads(path.read_text())
            data.update(metrics=document['metrics'],
                        source_error='APIStatusError' if document['result']['gen'] == "err-<class 'openai.APIStatusError'>" else None)
            trace = [{'type': 'turn.completed', 'usage': {
                'input_tokens': document['metrics']['prompt_tokens'],
                'output_tokens': document['metrics']['completion_tokens']}}]
        else:
            path = directory / ('trace.jsonl' if method == 'run_free' else 'trajectory.json')
            trace = []
            values = {'input': [], 'output': []}
            if method == 'run_free':
                for i, line in enumerate(path.read_text().splitlines()):
                    try:
                        event = json.loads(line)
                        if event.get('type') != 'assistant':
                            continue
                        usage = event.get('message', {}).get('usage', {})
                        trace.append(event)
                        for key in values:
                            values[key].append(atom(usage.get(key + '_tokens', 0), str(path),
                                f'line[{i}]/message/usage/{key}_tokens', description='原 Claude reader 不加缓存'))
                    except (ValueError, AttributeError):
                        continue
            else:
                document = json.loads(path.read_text())
                if not document.get('end_time'):
                    return OriginalCase(case_id, None, patch, method_data=data)
                # Project the approved modern controller onto the original Gemini
                # reader: prompt plus visible output + separately reported thoughts.
                for i, entry in enumerate(document['llm_interactions']):
                    usage = entry.get('response', {}).get('usage')
                    if usage is None:
                        continue
                    trace.append(entry)
                    for key in values:
                        values[key].append(atom(usage.get(key + '_tokens'), str(path),
                            f'/llm_interactions/{i}/response/usage/{key}_tokens'))
                # Native GoogleClient omits thoughts; add them from the SAME
                # selected responses, not from auxiliary or failed HTTP retries.
                from .rq1_report import selected_turn_usage
                values = selected_turn_usage(directory, document)
            data['native_calculation'] = values
        return OriginalCase(case_id, trace, patch, method_data=data)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run_free(workspace, artifacts, options):
    """Reuse the original prompt/caller, adapting only its Docker transport.

    The common executor owns the already-prepared container and retains it
    before cleanup. The original Claude command (including its CLI flag
    fallback) is executed unchanged apart from the artifact mount path.
    """
    from .model_channel import recording_model_channel
    source = ROOT / 'methods/run_free/upstream'
    caller_module = _load(source / 'experiments/agent_caller.py', '_rq1_run_free_caller')
    builder = _load(source / 'experiments/prompt_builder.py', '_rq1_run_free_prompt')
    prompt = builder.PromptBuilder.build_prompt(options['task'], 'run_free', 2, 'claude_code')
    host, target = artifacts.host, artifacts.execution
    (host / 'final-prompt.txt').write_text(prompt)
    subprocess.run(['docker', 'cp', str(source / 'docker'), workspace.container + ':/workspace/docker'], check=True,
                   stdout=subprocess.DEVNULL)
    class Caller(caller_module.AgentCaller):
        def _get_docker_image(self, instance_id):
            return 'rq1-prepared-task-image'

        def _build_claude_command(self, prompt, trace_path):
            command = super()._build_claude_command(prompt, trace_path)
            script = command[-1].replace('/workspace/output/', target.rstrip('/') + '/')
            argv = ['docker', 'exec']
            for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDE_MODEL'):
                argv += ['-e', key]  # values inherited, never written to argv/logs
            return [*argv, workspace.container, 'bash', '-c', script]
    with recording_model_channel(workspace, artifacts, options['endpoint'], protocol='anthropic_messages',
                                 provider='anthropic', attribution={'purpose': 'main'}, timeout=300) as endpoint:
        names = ('ANTHROPIC_BASE_URL', 'CLAUDE_MODEL', 'ANTHROPIC_API_KEY')
        previous = {k: os.environ.get(k) for k in names}
        try:
            os.environ.update(ANTHROPIC_BASE_URL=endpoint, CLAUDE_MODEL=options['spec']['model_id'],
                              ANTHROPIC_API_KEY=os.environ[options['api_key_env']])
            trace = Caller('claude_code', options['task']['instance_id'], 'run_free').call(
                prompt, options['spec'].get('timeout_seconds', 1200), str(host / 'trace.jsonl'))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    write_json(host / 'native-result.json', vars(trace))
    return AgentResult(**vars(trace), artifacts=artifacts)


def _run_expert(workspace, artifacts, options):
    from .usage_proxy import recording_proxy
    host = artifacts.host
    source = ROOT / 'methods' / options['method'] / 'upstream/code/trae_agent'
    # Host interpreter runs the original agent; original tools run in the
    # prepared task container. No dependencies are installed by this adapter.
    subprocess.run(['docker', 'cp', str(source / 'tools'), workspace.container + ':/home/swe-bench/tools'],
                   check=True, stdout=subprocess.DEVNULL)
    settings = {**options, 'container': workspace.container, 'root': workspace.root,
                'source': str(source), 'output': str(host)}
    started = time.monotonic()
    with ExitStack() as stack:
        def association(request, headers):
            key = next((v for k, v in headers.items() if k.lower() == 'x-tokenana-call'), None)
            return {'parent_call_id': key} if key else {}
        protocol = options['model_protocol']
        settings['endpoint'] = stack.enter_context(recording_proxy(host / 'api-records', options['endpoint'],
            protocol=protocol, provider='google', attribution={**workspace.usage_identity, 'purpose': 'main'},
            attribution_resolver=association, timeout=300))
        if options['method'] == 'agent_diet':
            settings['helper_endpoint'] = stack.enter_context(recording_proxy(host / 'auxiliary-records/helper', options['helper_endpoint'],
                protocol='chat_completions', provider='openai',
                attribution={**workspace.usage_identity, 'purpose': 'method_auxiliary'}, attribution_resolver=association, timeout=300))
        else:
            settings['official_endpoint'] = stack.enter_context(recording_proxy(host / 'auxiliary-records/google-official',
                'https://generativelanguage.googleapis.com', protocol=protocol, provider='google',
                attribution={**workspace.usage_identity, 'purpose': 'main'}, attribution_resolver=association, timeout=300))
        settings['identity'] = workspace.usage_identity
        write_json(host / 'native-request.json', settings)
        with (host / 'stdout.txt').open('w') as out, (host / 'stderr.txt').open('w') as err:
            process = subprocess.run([sys.executable, '-B', '-m', 'src.rq1_expert', str(host / 'native-request.json')],
                cwd=ROOT, stdout=out, stderr=err)
    document = json.loads((host / 'native-trajectory.json').read_text()) if (host / 'native-trajectory.json').exists() else {}
    metrics = document.get('metrics', {})
    termination = document.get('result', {}).get('gen', '')
    error = (termination if termination.startswith('err-') or termination == 'task_failed' else
             f'native runner exit {process.returncode}' if process.returncode else None)
    return AgentResult('trae', options['task']['problem_statement'], '', metrics.get('cost_tokens', 0),
        metrics.get('tot_step', 0), time.monotonic() - started, [document] if document else [],
        error=error, artifacts=artifacts, control={'native_generation_result': termination})
