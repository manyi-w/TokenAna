"""Per-agent-call HTTP recording proxy. No retries, transforms or model calls of its own."""

from contextlib import contextmanager
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .usage_protocols import API_PATHS, api_path_supported


HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host"}


def _headers(headers):
    """Keep operational headers but never persist authentication credentials."""
    return [[key, "[REDACTED]" if any(word in key.lower() for word in
            ("authorization", "cookie", "api-key", "api_key", "token", "secret")) else value]
            for key, value in headers]


def _save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _has_cache_control(value):
    if isinstance(value, dict):
        return "cache_control" in value or any(_has_cache_control(item) for item in value.values())
    return isinstance(value, list) and any(_has_cache_control(item) for item in value)


@contextmanager
def recording_proxy(directory: Path, upstream_base_url: str, *, timeout: float = 60,
                    protocol: str = "responses", provider: str | None = None, request_guard=None,
                    attribution=None, attribution_resolver=None, before_request=None):
    """Bind host loopback; a Workspace may expose it through an isolated byte channel.

    Artifacts are incremental request/response bodies and metadata per HTTP attempt.
    Only the selected protocol paths are accepted. WebSockets/redirects are not followed.
    Credentials are forwarded, not saved. Upstream URL cannot contain credentials.
    """
    if protocol not in API_PATHS:
        raise ValueError("unsupported recording protocol")
    upstream = urlsplit(upstream_base_url)
    if (upstream.scheme not in ("http", "https") or not upstream.hostname
            or upstream.username or upstream.password or upstream.query or upstream.fragment):
        raise ValueError("upstream_base_url must be an HTTP(S) base URL without credentials/query/fragment")
    if timeout <= 0:
        raise ValueError("proxy timeout must be positive")
    from .overhead import validate_attribution
    attribution = validate_attribution(attribution or {})
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    _save(directory / 'session.json', {'protocol': protocol, 'closed': False})
    active = set()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(timeout)

        def log_message(self, *args):
            pass

        def reject(self, status, reason):
            output = directory / uuid4().hex
            output.mkdir()
            _save(output / "metadata.json", {
                "protocol": protocol, "rejected": True, "reason": reason,
                "attribution": dict(attribution),
                "method": self.command, "response_complete": False,
            })
            self.close_connection = True
            self.send_error(status, reason)

        def do_GET(self):
            self.reject(405, "Only recorded POST API paths are supported")

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_GET

        def do_POST(self):
            if request_guard is not None:
                try:
                    admitted = request_guard()
                except Exception:
                    admitted = False
                if not admitted:
                    self.reject(503, "Required agent control plugin is not initialized")
                    return
            if not api_path_supported(self.path, protocol):
                self.reject(404, "API path is not covered by recorder")
                return
            if self.headers.get("Transfer-Encoding"):
                self.reject(400, "Chunked request bodies are unsupported")
                return
            try:
                length = int(self.headers["Content-Length"])
                if length < 0:
                    raise ValueError()
            except (TypeError, ValueError):
                self.reject(411, "Content-Length required")
                return
            base = upstream.path.rstrip("/")
            suffix = self.path.removeprefix("/v1")
            if protocol == 'gemini_generate_content':
                # Native SDKs include the API version and model in the URL.
                suffix = self.path
                if base.endswith(('/v1', '/v1beta')):
                    base = base.rsplit('/', 1)[0]
            if protocol == "anthropic_messages" and not base.endswith("/v1"):
                base += "/v1"
            request_id = uuid4().hex
            output = directory / request_id
            output.mkdir()
            metadata = {"request_id": request_id, "started_at": time.time(),
                        "request_headers": _headers(self.headers.items()),
                        "upstream_path": base + suffix, "protocol": protocol,
                        "provider": provider, "attribution": dict(attribution),
                        "response_complete": False, "status": None, "forwarded_at": None}
            started = time.monotonic()
            _save(output / "metadata.json", metadata)
            connection_type = HTTPSConnection if upstream.scheme == "https" else HTTPConnection
            conn = connection_type(upstream.hostname, upstream.port, timeout=timeout)
            with lock:
                active.add(conn)
            headers_sent = False
            self.connection.settimeout(timeout)
            self.close_connection = True
            try:
                body = self.rfile.read(length)
                (output / "request.body").write_bytes(body)
                if len(body) != length:
                    raise ValueError("incomplete request body")
                try:
                    request = json.loads(body)
                    if isinstance(request, dict):
                        metadata["model"] = request.get("model")
                        if protocol == 'gemini_generate_content':
                            metadata['model'] = urlsplit(self.path).path.split('/models/', 1)[1].split(':', 1)[0]
                    metadata["cache_control_observed"] = _has_cache_control(request)
                    metadata["stream_usage_requested"] = (
                        request.get("stream_options", {}).get("include_usage")
                        if isinstance(request, dict) else None)
                except (ValueError, TypeError, AttributeError):
                    metadata["cache_control_observed"] = None
                if attribution_resolver is not None:
                    resolved = attribution_resolver(json.loads(body), dict(self.headers))
                    metadata["attribution"] = validate_attribution({**attribution, **resolved})
                _save(output / "metadata.json", metadata)
                if before_request is not None:
                    snapshot_started = time.monotonic()
                    before_request({'request_id': request_id, 'attribution': metadata['attribution']})
                    metadata['recording_snapshot_seconds'] = time.monotonic() - snapshot_started
                    _save(output / 'metadata.json', metadata)
                connection_headers = {name.strip().lower() for name in
                                      self.headers.get("Connection", "").split(",")}
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() not in HOP_HEADERS | connection_headers | {'x-tokenana-call'}}
                headers["Connection"] = "close"
                metadata['forwarded_at'] = time.time()
                _save(output / 'metadata.json', metadata)
                conn.request("POST", metadata["upstream_path"], body=body, headers=headers)
                response = conn.getresponse()
                metadata.update(status=response.status,
                                response_headers=_headers(response.getheaders()))
                _save(output / "metadata.json", metadata)
                self.send_response_only(response.status, response.reason)
                blocked = HOP_HEADERS | {"content-length"} | {
                    name.strip().lower() for name in response.getheader("Connection", "").split(",")}
                for key, value in response.getheaders():
                    if key.lower() not in blocked:
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                headers_sent = True
                received = 0
                with (output / "response.body").open("wb") as raw:
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        raw.write(chunk)
                        raw.flush()
                        received += len(chunk)
                        # Persist before forwarding so downstream failures do not erase evidence.
                        self.wfile.write(chunk)
                        self.wfile.flush()
                declared = response.getheader("Content-Length")
                if declared is not None and received != int(declared):
                    raise ValueError("incomplete upstream response body")
                metadata["response_complete"] = True
            except Exception as error:
                # Exception strings can contain URLs/credentials; persist only the type.
                metadata["error_type"] = type(error).__name__
                if not headers_sent:
                    try:
                        self.send_error(502, "Recording proxy upstream failure")
                    except OSError:
                        pass
            finally:
                metadata["finished_at"] = time.time()
                metadata['proxy_wall_seconds'] = time.monotonic() - started
                metadata["duration_sec"] = metadata['proxy_wall_seconds'] - metadata.get('recording_snapshot_seconds', 0)
                try:
                    _save(output / "metadata.json", metadata)
                finally:
                    conn.close()
                    with lock:
                        active.discard(conn)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        prefix = "" if protocol in ("anthropic_messages", "gemini_generate_content") else "/v1"
        yield f"http://127.0.0.1:{server.server_port}{prefix}"
    finally:
        server.shutdown()
        with lock:
            connections = list(active)
        for conn in connections:
            conn.close()
        server.server_close()
        thread.join()
        _save(directory / 'session.json', {'protocol': protocol, 'closed': True})
