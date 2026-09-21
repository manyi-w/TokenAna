"""Explicit API usage schemas; raw payloads remain in HTTP recording artifacts."""

from .accounting import _count, normalize_openai_usage


API_PATHS = {
    "responses": ("/v1/responses", "/v1/responses/compact"),
    "chat_completions": ("/v1/chat/completions",),
    "anthropic_messages": ("/v1/messages",),
}


def normalize_usage(raw, protocol, provider=None):
    # Qwen's OpenAI-compatible cache creation counter is an input subcategory.
    # Never treat a missing counter as a server-reported zero.
    if provider == 'dashscope' and protocol in ('responses', 'chat_completions'):
        key = 'input_tokens_details' if protocol == 'responses' else 'prompt_tokens_details'
        details = raw.get(key) or {}
        if isinstance(details, dict) and 'cache_creation_input_tokens' in details:
            write = _count(details['cache_creation_input_tokens'], 'cache_creation_input_tokens')
            if details.get('cache_write_tokens') not in (None, write):
                raise ValueError('Qwen cache write counters disagree')
            raw = {**raw, key: {**details, 'cache_write_tokens': write}}
    if protocol == "responses":
        return normalize_openai_usage(raw)
    if protocol == "chat_completions":
        details = raw.get("prompt_tokens_details") or {}
        if not isinstance(details, dict):
            raise ValueError("prompt_tokens_details must be an object")
        details = dict(details)
        if provider == "deepseek" and raw.get("prompt_cache_hit_tokens") is not None:
            hit = _count(raw["prompt_cache_hit_tokens"], "prompt_cache_hit_tokens")
            if details.get("cached_tokens") not in (None, hit):
                raise ValueError("DeepSeek cache counters disagree")
            details["cached_tokens"] = hit
        metrics = normalize_openai_usage({
            "input_tokens": raw.get("prompt_tokens"),
            "output_tokens": raw.get("completion_tokens"),
            "total_tokens": raw.get("total_tokens"),
            "input_tokens_details": details,
            "output_tokens_details": raw.get("completion_tokens_details"),
        })
        if not isinstance(raw.get("completion_tokens_details") or {}, dict):
            raise ValueError("completion_tokens_details must be an object")
        if provider == "deepseek" and "prompt_cache_miss_tokens" in raw:
            miss = _count(raw["prompt_cache_miss_tokens"], "prompt_cache_miss_tokens")
            metrics["cache_miss"] = miss
            hit, incoming = metrics["cache_read"], metrics["input"]
            if all(value is not None for value in (miss, hit, incoming)) and miss + hit != incoming:
                raise ValueError("DeepSeek cache hit + miss disagrees with prompt_tokens")
        return metrics
    if protocol != "anthropic_messages":
        raise ValueError("unsupported usage protocol")
    ordinary, read, write, output = (
        _count(raw.get(key), key) for key in
        ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
    # Anthropic's input_tokens excludes both cached and newly cached input.
    incoming = (ordinary + read + write
                if all(value is not None for value in (ordinary, read, write)) else None)
    metrics = {"input": incoming, "output": output,
            "total": incoming + output if incoming is not None and output is not None else None,
            "ordinary_input": ordinary, "cache_read": read, "cache_write": write,
            "reasoning": None}
    creation = raw.get("cache_creation")
    if creation is not None:
        if not isinstance(creation, dict):
            raise ValueError("cache_creation must be an object")
        for ttl in ("5m", "1h"):
            metrics[f"cache_write_{ttl}"] = _count(
                creation.get(f"ephemeral_{ttl}_input_tokens"), f"cache_write_{ttl}")
        parts = [metrics[f"cache_write_{ttl}"] for ttl in ("5m", "1h")]
        if write is not None and all(value is not None for value in parts) and sum(parts) != write:
            raise ValueError("cache TTL details disagree with cache creation total")
    return metrics


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

    def add(self, event):
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "error" or event.get("error"):
            raise ValueError("API error event")
        if self.streaming and self.protocol == "anthropic_messages":
            if kind == "message_start":
                if self.message_id is not None:
                    raise ValueError("duplicate message_start")
                message = event.get("message") or {}
                self.message_id = message.get("id") or "unidentified-response"
                self._record(message)
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
        self._record(event)

    def _record(self, response):
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
