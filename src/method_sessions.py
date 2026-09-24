"""Utilities for optional method callbacks, without importing unselected methods."""
from dataclasses import replace
import json
from pathlib import Path

from .interfaces import ArtifactDirectory, MethodResult
from .records import write_json


class SessionMethod:
    required_session_capabilities = ('events', 'replace_history', 'state', 'reminder', 'terminate')
    original_trace_formats = ('codex-jsonl',)
    resume_policy = 'never_regenerate'

    def enrich_original(self, case, directory):
        path = directory / 'method-state.json'
        if not path.exists():
            raise ValueError('method accounting state is missing')
        return replace(case, method_data=json.loads(path.read_text()))

    def run(self, task, agent, workspace, options):
        from contextlib import nullcontext
        from .telemetry import span
        root = getattr(getattr(workspace, 'artifacts', None), 'host', None)
        with span(root, 'method_prepare') if root is not None else nullcontext():
            self.validate_options(options)
            callback = self.callback(task, workspace, options)
        return MethodResult(calls=[agent.run_session(task.problem_statement, workspace, {}, callback)])


def artifacts(event):
    return ArtifactDirectory(Path(event.details['artifact_host']), event.details['artifact_execution'])


def save_state(event, name, state):
    from uuid import uuid4
    host = artifacts(event).host
    versions = host / 'method-states'
    versions.mkdir(exist_ok=True)
    value = {'method': name, **state}
    write_json(versions / (uuid4().hex + '.json'), value)
    write_json(host / 'method-state.json', value)


def tool_call(message):
    native = message.native.get('message', {})
    if native.get('object') == 'response':
        return [{'id': item['call_id'], 'function': {'name': item['name'], 'arguments': item['arguments']}}
                for item in native.get('output', []) if item.get('type') == 'function_call']
    calls = native.get('tool_calls') or []
    if calls:
        return calls
    if native.get('type') == 'function_call':
        return [{'id': native['call_id'], 'function': {'name': native['name'], 'arguments': native['arguments']}}]
    if native.get('type') == 'custom_tool_call':
        return [{'id': native['call_id'], 'function': {'name': native['name'], 'arguments': json.dumps({'input': native['input']})}}]
    blocks = native.get('content')
    if isinstance(blocks, list):
        calls = [{'id': b['id'], 'function': {'name': b['name'],
                   'arguments': json.dumps(b['input']) if not isinstance(b['input'], str) else b['input']}}
                 for b in blocks if b.get('type') == 'tool_use']
    part = message.native.get('part', {})
    if part.get('type') == 'tool':
        calls = [{'id': part['callID'], 'function': {'name': part['tool'], 'arguments': json.dumps(part['state']['input'])}}]
    return calls


def chat_steps(history):
    """Project whole native assistant/tool steps into the author's serializer shape."""
    steps, ids, call_names = [], [], {}
    for message in history:
        if message.role == 'assistant':
            calls = tool_call(message)
            for call in calls:
                function = call['function']
                try:
                    arguments = json.loads(function.get('arguments', '{}'))
                except ValueError:
                    arguments = {}
                call_names[call['id']] = (function['name'], arguments)
            # Flattened Responses/Anthropic tool calls belong to one assistant step.
            if not steps or (steps[-1] and any(m['role'] != 'assistant' for m in steps[-1])):
                steps.append([]); ids.append([])
            steps[-1].append({'role': 'assistant', 'content': message.text, 'tool_calls': calls})
            ids[-1].append(message.id)
        elif steps and message.role in ('tool', 'user'):
            steps[-1].append({'role': message.role, 'content': message.text,
                             'tool_call_id': message.tool_result,
                             'agent_caller': call_names.get(message.tool_result, ('unknown', {}))})
            ids[-1].append(message.id)
    return steps, ids


def source_metrics(cases, *, rule, patch_filter=False, author_overhead=None):
    from .accounting_trace import calc, metric, event_source, case_source
    selected = [c for c in cases if c.trace is not None and (not patch_filter or (c.patch and c.patch.strip()))]
    denominator = calc('count', *(case_source(c, 'case_id', c.case_id, kind='selection') for c in selected),
                       description='符合 original 筛选的任务数')
    nodes = {}
    for key in ('input', 'output'):
        nodes[key] = calc('sum', *(event_source(case, index, event, key + '_tokens')
            for case in selected for index, event in enumerate(case.trace) if event.get('type') == 'turn.completed'),
            description='按原规则逐项累加原生 token', rule='src/method_sessions.py:source_metrics')
    nodes['total'] = calc('sum', nodes['input'], nodes['output'], description='原 input + output')
    report = {'rule': rule, 'cases_counted': len(selected), 'metrics': {
        key: metric(value, denominator) for key, value in nodes.items()},
        'selection': [dict(case_id=c.case_id, included=c in selected,
            reason='轨迹存在且满足原补丁筛选' if c in selected else '缺少轨迹或不符合原补丁筛选') for c in cases],
        'note': 'Source-compatible native usage projection; original policy is separate from all-attempt raw usage.'}
    if author_overhead is not None:
        report['overhead'] = author_overhead
    return report


def author_metrics(count, values, rule):
    from .accounting import METRICS
    return {'rule': rule, 'cases_counted': count, 'metrics': {
        key: {'sum': values.get(key), 'mean': values[key] / count if key in values and count else None,
              'complete': key in values, 'reasons': [] if key in values else ['not measured by author']}
        for key in (*METRICS, 'calls', 'service_seconds')}}
