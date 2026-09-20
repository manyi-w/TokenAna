"""Read explicit provider protocol usage from recorded HTTP bodies, never agent trace."""

import gzip
import json
from pathlib import Path
import zlib

from .accounting import METRICS, CaseUsage, UsageObservation, UsageOperation
from .usage_protocols import UsageEvents, normalize_usage


def _events(body: bytes, content_type: str):
    decoding_error = None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        text = body[:error.start].decode("utf-8")
        decoding_error = error
    if "text/event-stream" not in content_type:
        yield json.loads(text)
        if decoding_error:
            raise decoding_error
        return
    # SSE event data can span multiple lines. Only complete events are parsed.
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True):
        line = raw_line.removesuffix("\n")
        if not line:
            if lines:
                value = "\n".join(lines)
                yield {"type": "tokenana.stream_done"} if value == "[DONE]" else json.loads(value)
                lines = []
        elif line.startswith("data:"):
            lines.append(line[5:].removeprefix(" "))
    if lines:
        raise ValueError("truncated SSE event")
    if decoding_error:
        raise decoding_error


def read_raw_usage(directory: Path, *, case_id: str, attempt_id: str,
                   call_id: str) -> CaseUsage:
    """Each recorded HTTP attempt counts once, including agent retries.

    Within one HTTP attempt identical response usage is deduplicated. Conflicting
    terminal usage is unknown; partial streams retain completed response evidence.
    Missing requests cannot be inferred from a recording directory alone.
    """
    observations = []
    operations = []
    issues = []
    call_seen = False
    paths = [entry / "metadata.json" for entry in sorted(Path(directory).glob("*"))
             if entry.is_dir()]
    if not paths:
        return CaseUsage(case_id, None, coverage_complete=False,
                         issues=("no recorded API request; call status unknown",))
    for path in paths:
        response_records = {}
        events = None
        protocol = "responses"
        provider = None
        meta = {}
        try:
            meta = json.loads(path.read_text())
            if not isinstance(meta, dict):
                raise ValueError("metadata must be an object")
            protocol = meta.get("protocol", "responses")
            provider = meta.get("provider")
            if meta.get("rejected"):
                raise ValueError("request rejected by recorder")
            call_seen = True
            if not meta.get("response_complete"):
                issues.append(f"{path}: incomplete HTTP exchange")
            headers = {k.lower(): v for k, v in meta.get("response_headers", [])}
            body = path.with_name("response.body").read_bytes()
            encoding = headers.get("content-encoding", "identity").lower()
            if encoding == "gzip":
                body = gzip.decompress(body)
            elif encoding != "identity":
                raise ValueError("unsupported content encoding")
            is_stream = "text/event-stream" in headers.get("content-type", "")
            events = UsageEvents(protocol, is_stream)
            response_records = events.records
            for event in _events(body, headers.get("content-type", "")):
                events.add(event)
        except (OSError, ValueError, TypeError, AttributeError, EOFError, zlib.error) as error:
            issues.append(f"{path}: cannot fully read raw usage ({type(error).__name__})")
        if events is None or not events.terminal:
            issues.append(f"{path}: no terminal response")
        if not response_records:
            issues.append(f"{path}: response usage missing")
        from .overhead import validate_attribution, duration
        attribution = {}
        operation_issues = []
        try:
            attribution = validate_attribution(meta.get("attribution", {}))
            for key, value in (("case_id", case_id), ("attempt_id", attempt_id)):
                if attribution.get(key, value) != value:
                    raise ValueError("request association disagrees with saved attempt")
        except (ValueError, TypeError, AttributeError):
            attribution = {}
            operation_issues.append("invalid request attribution")
            issues.append(f"{path}: invalid request attribution")
        model = meta.get("model") if isinstance(meta, dict) else None
        if not isinstance(model, str) or not model:
            model = None
        elapsed = duration(meta) if isinstance(meta, dict) else None
        purpose = attribution.get("purpose", "unknown")
        parent = attribution.get("parent_call_id")
        operations.append(UsageOperation(case_id, attempt_id, call_id, path.parent.name,
                          purpose, model, parent, duration_sec=elapsed,
                          complete=bool(isinstance(meta, dict) and meta.get("response_complete")),
                          issues=operation_issues))
        for response_id, raw in response_records.items():
            metric_issues = {}
            try:
                if raw is None:
                    raise ValueError("conflicting terminal usage")
                metrics = normalize_usage(raw, protocol, provider)
            except (ValueError, TypeError, AttributeError) as error:
                metrics = dict.fromkeys(METRICS)
                metric_issues = dict.fromkeys(METRICS, str(error))
            observations.append(UsageObservation(
                case_id, attempt_id, call_id, f"{path.parent.name}/{response_id}",
                metrics, str(path.with_name("response.body")), raw or {}, metric_issues,
                purpose, model, parent, path.parent.name))
    return CaseUsage(case_id, True if call_seen else None, observations, not issues, issues, operations)
