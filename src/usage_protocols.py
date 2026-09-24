"""Explicit API usage schemas; raw payloads remain in HTTP recording artifacts."""

from .accounting import _count
from .accounting_trace import atom, calc, constant
import re
from urllib.parse import urlsplit


API_PATHS = {
    "responses": ("/v1/responses", "/v1/responses/compact"),
    "chat_completions": ("/v1/chat/completions",),
    "anthropic_messages": ("/v1/messages",),
    "gemini_generate_content": ("/v1beta/models/{model}:generateContent", "/v1beta/models/{model}:streamGenerateContent?alt=sse"),
}


def api_path_supported(path, protocol):
    if protocol == 'anthropic_messages':
        parsed = urlsplit(path)
        return parsed.path == '/v1/messages' and parsed.query in ('', 'beta=true')
    if protocol == 'gemini_generate_content':
        parsed = urlsplit(path)
        return bool(re.fullmatch(r'/v1(?:beta)?/models/[A-Za-z0-9._-]+:(?:generateContent|streamGenerateContent)', parsed.path)
                    and parsed.query in ('', 'alt=sse'))
    return path in API_PATHS.get(protocol, ())


def normalize_usage(raw, protocol, provider=None, *, calculation=None, source='raw_usage', locators=None):
    """Compute normalized fields and their exact field-level arithmetic together."""
    locators = locators or {}
    nodes = {}
    def read(path):
        value = raw
        for key in path.split('/'):
            value = value.get(key) if isinstance(value, dict) else None
        value = _count(value, path)
        return atom(value, source, locators.get(path, '/usage/' + path), kind='api_usage' if value is not None else 'missing',
                    description='API usage 原字段' if value is not None else 'API 未提供此字段，不能当作零')
    def plus(*values):
        return calc('sum', *values, description='不重叠 token 分项相加', rule='src/usage_protocols.py:normalize_usage')
    def sub(a, b):
        return calc('subtract', a, b, description='从包含子项的总量中扣除子项', rule='src/usage_protocols.py:normalize_usage')
    if protocol == 'gemini_generate_content':
        # Gemini promptTokenCount INCLUDES cachedContentTokenCount. The native
        # protobuf counters omitted from an otherwise present usageMetadata
        # have their scalar default, zero. Do not apply this to arbitrary gateways.
        zero = lambda field: constant(0, 'https://ai.google.dev/api/generate-content#UsageMetadata',
                                      'Gemini usageMetadata omitted scalar: ' + field)
        nodes['input'] = read('promptTokenCount')
        nodes['cache_read'] = read('cachedContentTokenCount') if 'cachedContentTokenCount' in raw else zero('cachedContentTokenCount')
        nodes['cache_write'] = zero('no token cache-write charge in generateContent')
        nodes['reasoning'] = read('thoughtsTokenCount') if 'thoughtsTokenCount' in raw else zero('thoughtsTokenCount')
        nodes['ordinary_output'] = read('candidatesTokenCount') if 'candidatesTokenCount' in raw else zero('candidatesTokenCount')
        nodes['output'] = plus(nodes['ordinary_output'], nodes['reasoning'])
        nodes['ordinary_input'] = sub(nodes['input'], nodes['cache_read'])
        if raw.get('toolUsePromptTokenCount', 0):
            raise ValueError('Gemini server-tool prompt billing is outside the frozen RQ1 setting')
        if nodes['ordinary_input']['value'] is not None and nodes['ordinary_input']['value'] < 0:
            raise ValueError('Gemini cache exceeds effective prompt')
    elif protocol == 'anthropic_messages':
        nodes.update(ordinary_input=read('input_tokens'), cache_read=read('cache_read_input_tokens'),
                     cache_write=read('cache_creation_input_tokens'), output=read('output_tokens'))
        nodes['input'] = plus(nodes['ordinary_input'], nodes['cache_read'], nodes['cache_write'])
        nodes['reasoning'] = atom(None, source, '/usage/reasoning', kind='missing', description='协议未提供独立 reasoning 计数')
        if raw.get('cache_creation') is not None:
            if not isinstance(raw['cache_creation'], dict):
                raise ValueError('cache_creation must be an object')
            for ttl in ('5m', '1h'):
                nodes['cache_write_' + ttl] = read('cache_creation/ephemeral_' + ttl + '_input_tokens')
            parts = [nodes['cache_write_' + ttl]['value'] for ttl in ('5m', '1h')]
            if nodes['cache_write']['value'] is not None and all(v is not None for v in parts) and sum(parts) != nodes['cache_write']['value']:
                raise ValueError('cache TTL details disagree with cache creation total')
    elif protocol in ('responses', 'chat_completions'):
        inp, out = ('input_tokens', 'output_tokens') if protocol == 'responses' else ('prompt_tokens', 'completion_tokens')
        ik, ok = inp + '_details', out + '_details'
        for key in (ik, ok):
            if not isinstance(raw.get(key) or {}, dict):
                raise ValueError('usage details must be objects')
        nodes.update(input=read(inp), output=read(out), cache_read=read(ik + '/cached_tokens'),
                     cache_write=read(ik + '/cache_write_tokens'), reasoning=read(ok + '/reasoning_tokens'))
        if provider == 'google' and protocol == 'chat_completions':
            # Some preserved Gemini gateways expose native-style counters at
            # the top level. Keep the original payload; normalize only here.
            if raw.get('cache_read_input_tokens') is not None:
                hit = read('cache_read_input_tokens')
                if nodes['cache_read']['value'] not in (None, hit['value']):
                    raise ValueError('Gemini gateway cache counters disagree')
                nodes['cache_read'] = hit
            if raw.get('reasoning_tokens') is not None:
                nodes['reasoning'] = read('reasoning_tokens')
                total = raw.get('total_tokens')
                base = raw.get(inp)
                completion = raw.get(out)
                reasoning = nodes['reasoning']['value']
                if all(type(v) is int for v in (total, base, completion, reasoning)):
                    if total == base + completion + reasoning:
                        nodes['output'] = plus(read(out), nodes['reasoning'])
                    elif total != base + completion:
                        raise ValueError('Ambiguous Gemini gateway reasoning accounting')
            if nodes['cache_write']['value'] is None:
                nodes['cache_write'] = constant(0, 'https://ai.google.dev/api/generate-content#UsageMetadata',
                                               'Gemini generation does not expose a token cache-write charge')
        if provider == 'deepseek' and protocol == 'chat_completions' and raw.get('prompt_cache_hit_tokens') is not None:
            hit = read('prompt_cache_hit_tokens')
            if nodes['cache_read']['value'] not in (None, hit['value']):
                raise ValueError('DeepSeek cache counters disagree')
            nodes['cache_read'] = hit
        if provider == 'dashscope' and 'cache_creation_input_tokens' in (raw.get(ik) or {}):
            write = read(ik + '/cache_creation_input_tokens')
            if nodes['cache_write']['value'] not in (None, write['value']):
                raise ValueError('Qwen cache write counters disagree')
            nodes['cache_write'] = write
        incoming, outgoing = nodes['input']['value'], nodes['output']['value']
        read_count, write_count, reasoning = (nodes[k]['value'] for k in ('cache_read', 'cache_write', 'reasoning'))
        if incoming is not None:
            if any(v is not None and v > incoming for v in (read_count, write_count)):
                raise ValueError('cache detail exceeds input')
            if read_count is not None and write_count is not None:
                if read_count + write_count > incoming:
                    raise ValueError('cache read + write exceeds input')
                nodes['ordinary_input'] = sub(sub(nodes['input'], nodes['cache_read']), nodes['cache_write'])
        if outgoing is not None and reasoning is not None:
            if reasoning > outgoing:
                raise ValueError('reasoning exceeds output')
            nodes['ordinary_output'] = sub(nodes['output'], nodes['reasoning'])
        for prefix, details, names in (
            ('input', ik, ('text_tokens', 'image_tokens', 'audio_tokens')),
            ('output', ok, ('text_tokens', 'image_tokens', 'audio_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens'))):
            for name in names:
                if name in (raw.get(details) or {}):
                    nodes[prefix + '_' + name] = read(details + '/' + name)
        if provider == 'deepseek' and protocol == 'chat_completions' and 'prompt_cache_miss_tokens' in raw:
            nodes['cache_miss'] = read('prompt_cache_miss_tokens')
            miss = nodes['cache_miss']['value']
            if all(v is not None for v in (miss, read_count, incoming)) and miss + read_count != incoming:
                raise ValueError('DeepSeek cache hit + miss disagrees with prompt_tokens')
    else:
        raise ValueError('unsupported usage protocol')
    nodes['total'] = plus(nodes['input'], nodes['output'])
    total_field = 'totalTokenCount' if protocol == 'gemini_generate_content' else 'total_tokens'
    reported = _count(raw.get(total_field), total_field) if protocol != 'anthropic_messages' else None
    if reported is not None and nodes['total']['value'] is not None and reported != nodes['total']['value']:
        raise ValueError('reported total disagrees with input + output')
    if calculation is not None:
        calculation.update(nodes)
    return {k: v['value'] for k, v in nodes.items()}


class UsageEvents:
    """Accumulate one HTTP exchange, never sum cumulative streaming snapshots."""

    def __init__(self, protocol, streaming):
        if protocol not in API_PATHS:
            raise ValueError("unsupported usage protocol")
        self.protocol, self.streaming = protocol, streaming
        self.records = {}
        self.terminal = False
        self.message_id = None
        self.message_delta_seen = False
        self.field_sources = {}
        self.event_index = -1

    def add(self, event):
        self.event_index += 1
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "error" or event.get("error"):
            raise ValueError("API error event")
        if self.protocol == 'gemini_generate_content':
            raw = event.get('usageMetadata')
            identity = event.get('responseId') or self.message_id or 'unidentified-response'
            self.message_id = identity
            if isinstance(raw, dict):
                self.records[identity] = raw  # cumulative, never sum stream chunks
                self._locate(identity, raw, '/usageMetadata')
            if not self.streaming or (isinstance(raw, dict) and any(c.get('finishReason') for c in event.get('candidates', []))):
                self.terminal = True
            return
        if self.streaming and self.protocol == "anthropic_messages":
            if kind == "message_start":
                if self.message_id is not None:
                    raise ValueError("duplicate message_start")
                message = event.get("message") or {}
                self.message_id = message.get("id") or "unidentified-response"
                self._record(message, prefix="/message/usage")
            elif kind == "message_delta":
                if self.message_id not in self.records:
                    raise ValueError("message_delta without initial usage")
                previous = self.records[self.message_id]
                usage = event.get("usage")
                if not isinstance(previous, dict) or not isinstance(usage, dict):
                    raise ValueError("invalid cumulative usage")
                for key, value in usage.items():
                    old = previous.get(key)
                    if type(old) is int and type(value) is int and value < old:
                        self.records[self.message_id] = None
                        raise ValueError("cumulative usage decreased")
                self.records[self.message_id] = {**previous, **usage}
                self._locate(self.message_id, usage, "/usage")
                self.message_delta_seen = self.message_delta_seen or "output_tokens" in usage
            elif kind == "message_stop":
                if not self.message_delta_seen:
                    raise ValueError("message_stop without final cumulative usage")
                self.terminal = True
            return
        if self.streaming and self.protocol == "responses":
            if kind not in ("response.completed", "response.incomplete", "response.failed"):
                return
            event = event.get("response") or {}
            self.terminal = True
        elif self.streaming:
            if kind == "tokenana.stream_done":
                self.terminal = True
                return
        else:
            self.terminal = True
        self._record(event, prefix="/response/usage" if self.streaming and self.protocol == "responses" else "/usage")

    def _locate(self, identity, raw, prefix):
        target = self.field_sources.setdefault(identity, {})
        def visit(value, path=""):
            for key, child in value.items():
                field = path + key
                if isinstance(child, dict):
                    visit(child, field + "/")
                else:
                    target[field] = (f"event[{self.event_index}]" if self.streaming else "") + prefix + "/" + field
        visit(raw)

    def _record(self, response, prefix="/usage"):
        raw = response.get("usage")
        if not isinstance(raw, dict):
            return
        identity = response.get("id") or "unidentified-response"
        if not isinstance(identity, str):
            raise ValueError("response id must be a string")
        if identity in self.records and self.records[identity] != raw:
            self.records[identity] = None
        else:
            self.records.setdefault(identity, raw)
            self._locate(identity, raw, prefix)
