"""Structural request diagnostics. No retokenization or allocation of API usage."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import shlex

RULES = {
    'version': 'structured-content-v1',
    'unit': 'characters; not tokens or billable usage',
    'categories': ['task_instructions', 'system_instructions', 'tool_definitions',
                   'tool_arguments', 'repository_code', 'patch', 'test_output',
                   'error_build_log', 'natural_language', 'method_summary', 'unclassified'],
    'classification': 'Explicit role, block type, tool name and parsed command only; no content keyword inference.',
    'identity': 'Native message/call ID when available; otherwise role + exact content digest + occurrence.',
    'limitations': 'Equal text is a structural recurrence, not proof of identical semantic origin. '
                  'Provider usage remains attached to the whole request. Hidden reasoning is not inspected.'}


def text_value(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def tool_category(name, arguments):
    if name in ('read_file', 'view_file', 'read', 'readFile'):
        return 'repository_code'
    if name in ('apply_patch', 'patch', 'write_patch'):
        return 'patch'
    if name in ('run_tests', 'test'):
        return 'test_output'
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return 'unclassified'
    if not isinstance(arguments, dict):
        return 'unclassified'
    command = arguments.get('command', arguments.get('cmd', ''))
    if not isinstance(command, str):
        return 'unclassified'
    try:
        words = shlex.split(command)
    except ValueError:
        return 'unclassified'
    # Compound commands can mix sources; leave their output unclassified.
    if any(c in command for c in (';', '|', '&&', '\n', '$(')) or not words:
        return 'unclassified'
    executable = Path(words[0]).name
    if executable in ('cat', 'head', 'tail', 'sed', 'rg', 'grep'):
        return 'repository_code'
    if words[:2] in (['git', 'diff'], ['git', 'show']):
        return 'patch'
    if executable in ('pytest', 'unittest') or words[:3] == ['python', '-m', 'pytest']:
        return 'test_output'
    if executable in ('make', 'cmake', 'gcc', 'clang', 'javac'):
        return 'error_build_log'
    return 'unclassified'


def segments(request):
    calls = {}
    items = request.get('messages', request.get('input', []))
    if isinstance(items, str):
        items = [{'role': 'user', 'content': items}]
    if not isinstance(items, list):
        items = []
    for key in ('instructions', 'system'):
        if request.get(key):
            yield key, 'system', 'system_instructions', text_value(request[key]), key
    if request.get('tools'):
        yield 'tools', 'system', 'tool_definitions', text_value(request['tools']), 'tools'
    user_seen = False
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        role = item.get('role', 'assistant' if item.get('type') in ('function_call', 'custom_tool_call') else 'tool')
        identity = item.get('id') or item.get('call_id') or item.get('tool_call_id')
        category = ('system_instructions' if role in ('system', 'developer') else
                    'task_instructions' if role == 'user' and not user_seen else
                    'natural_language' if role == 'assistant' else 'unclassified')
        if role == 'user':
            user_seen = True
        if str(identity or '').startswith(('summary-', 'method-')):
            category = 'method_summary'
        if item.get('type') in ('function_call', 'custom_tool_call'):
            args = item.get('arguments', item.get('input', ''))
            calls[item.get('call_id')] = tool_category(item.get('name'), args)
            yield identity, role, 'tool_arguments', text_value(args), f'input/{index}'
            continue
        for call in item.get('tool_calls') or []:
            function = call.get('function', {})
            args = function.get('arguments', {})
            calls[call.get('id')] = tool_category(function.get('name'), args)
            yield call.get('id'), role, 'tool_arguments', text_value(args), f'messages/{index}/tool_calls'
        if item.get('type') == 'function_call_output' or role == 'tool':
            category = calls.get(item.get('call_id', item.get('tool_call_id')), 'unclassified')
        content = item.get('content', item.get('output', ''))
        if isinstance(content, str):
            if content:
                yield identity, role, category, content, f'messages/{index}'
        elif isinstance(content, list):
            for bi, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                kind = block.get('type')
                if kind in ('thinking', 'redacted_thinking', 'reasoning'):
                    continue
                if kind == 'tool_use':
                    args = block.get('input', {})
                    calls[block.get('id')] = tool_category(block.get('name'), args)
                    yield block.get('id'), role, 'tool_arguments', text_value(args), f'messages/{index}/{bi}'
                elif kind == 'tool_result':
                    yield block.get('tool_use_id'), 'tool', calls.get(block.get('tool_use_id'), 'unclassified'), text_value(block.get('content', '')), f'messages/{index}/{bi}'
                elif isinstance(block.get('text'), str):
                    yield identity, role, category, block['text'], f'messages/{index}/{bi}'


def inspect_requests(run, *, seed=20260921, samples_per_category=3):
    requests = []
    for metadata in Path(run).glob('attempts/**/api-records/*/metadata.json'):
        requests.append(metadata)
    # Attempt directory names are not standardized; locate only recording metadata.
    requests = sorted(set(requests) | {p for p in Path(run).rglob('metadata.json')
        if p.with_name('request.body').exists() and ('api-records' in p.parts or 'auxiliary-records' in p.parts)})
    parsed, issues = [], []
    for path in requests:
        try:
            meta = json.loads(path.read_text())
            if meta.get('rejected') or ('forwarded_at' in meta and meta['forwarded_at'] is None):
                continue
            request = json.loads(path.with_name('request.body').read_text())
            parsed.append((meta.get('started_at') or 0, str(path), meta, request))
        except (OSError, ValueError):
            issues.append({'source': str(path), 'reason': 'request or metadata unreadable'})
    seen, versions, counts = defaultdict(set), defaultdict(dict), Counter()
    rows, per_request = [], []
    for order, (_, source, meta, request) in enumerate(sorted(parsed), 1):
        stream = str(Path(source).parent.parent)  # isolate auxiliary streams
        occurrences = Counter()
        before = len(rows)
        for identity, role, category, content, location in segments(request):
            digest = hashlib.sha256(content.encode()).hexdigest()
            signature = (role, digest)
            occurrences[signature] += 1
            fallback = f'{role}:{digest}:{occurrences[signature]}'
            identity = identity or fallback
            key = (stream, identity)
            repeated = digest in seen[key]
            if digest not in versions[key]:
                versions[key][digest] = len(versions[key]) + 1
            seen[key].add(digest)
            counts[(key, digest)] += 1
            rows.append(dict(request_order=order, request_id=Path(source).parent.name,
                source=str(Path(source).with_name('request.body')), location=location,
                model=request.get('model'), purpose=(meta.get('attribution') or {}).get('purpose', 'unknown'),
                category=category, message_id=identity, identity_kind='content_occurrence' if identity == fallback else 'native',
                version=versions[key][digest], digest=digest, characters=len(content), repeated=repeated,
                send_number=counts[(key, digest)]))
        selected = rows[before:]
        per_request.append(dict(request_order=order, request_id=Path(source).parent.name, source=source,
            visible_characters=sum(r['characters'] for r in selected),
            repeated_characters=sum(r['characters'] for r in selected if r['repeated']),
            new_characters=sum(r['characters'] for r in selected if not r['repeated'])))
    samples = []
    randomizer = random.Random(seed)
    for category in RULES['categories']:
        candidates = [r for r in rows if r['category'] == category]
        samples.extend(randomizer.sample(candidates, min(samples_per_category, len(candidates))))
    changes = []
    for path in sorted(Path(run).rglob('session-channel/**/*.json')):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        event, decision = value.get('event', {}), value.get('decision', {})
        if not isinstance(event, dict) or 'history' not in event:
            continue
        before = {m['id']: m for m in event['history']}
        after = {m['id']: m for m in decision.get('history') or []}
        changes.append(dict(source=str(path), event=event.get('kind'), status=value.get('status'),
            before_characters=sum(len(m.get('text', '')) for m in before.values()),
            after_characters=sum(len(m.get('text', '')) for m in after.values()) if value.get('status') == 'applied' else None,
            removed_ids=sorted(before.keys() - after.keys()) if value.get('status') == 'applied' else None,
            changed_ids=sorted(k for k in before.keys() & after.keys() if before[k].get('text') != after[k].get('text')),
            summary_ids=[k for k in after if k.startswith('summary-')],
            note='Direct before/after observation within this method event; not a baseline causal effect.'))
    return dict(rule=RULES, segments=rows, requests=per_request, review_samples=samples, issues=issues, method_changes=changes,
                note='Samples are source pointers, not copied prompts. Categories are structural diagnostics, not token shares.')
