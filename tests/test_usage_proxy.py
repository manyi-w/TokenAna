"""Local HTTP fake-service tests; no external endpoints or model calls."""

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from src.accounting import corrected_accounting
from src.raw_usage import read_raw_usage
from src.usage_proxy import recording_proxy


RAW = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
       "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
       "output_tokens_details": {"reasoning_tokens": 5}, "future_field": {"value": 3}}


@contextmanager
def fake_service(body, *, content_type="application/json", status=200, declared=None):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers["Content-Length"])),
                             self.headers.get("Authorization")))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body) if declared is None else declared))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def post(endpoint):
    parsed = urlsplit(endpoint)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    connection.request("POST", parsed.path + "/responses", body=b'{"model":"fake","stream":true}',
                       headers={"Authorization": "Bearer test-secret", "Content-Type": "application/json"})
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


@unittest.skipUnless(os.environ.get("TOKENANA_LOCAL_PROXY_TESTS") == "1",
                     "Local socket tests require separate explicit authorization")
class UsageProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.records = Path(self.temp.name) / "records"

    def read(self):
        return read_raw_usage(self.records, case_id="case", attempt_id="attempt", call_id="call")

    def test_json_exact_body_headers_unknown_fields_and_no_retry(self):
        body = json.dumps({"id": "resp1", "usage": RAW}).encode()
        with fake_service(body) as (upstream, received):
            with recording_proxy(self.records, upstream) as endpoint:
                self.assertEqual(post(endpoint), (200, body))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/v1/responses")
        self.assertEqual(received[0][2], "Bearer test-secret")
        item = self.read()
        self.assertTrue(item.coverage_complete)
        self.assertEqual(item.observations[0].raw_usage, RAW)
        self.assertEqual(item.observations[0].metrics["reasoning"], 5)
        metadata = next(self.records.glob("*/metadata.json")).read_text()
        self.assertNotIn("test-secret", metadata)
        self.assertEqual(next(self.records.glob("*/response.body")).read_bytes(), body)

    def test_sse_duplicate_terminal_not_double_counted_and_requests_distinct(self):
        event = {"type": "response.completed", "response": {"id": "resp1", "usage": RAW}}
        body = ("data: " + json.dumps(event) + "\r\n\r\n") * 2
        with fake_service(body.encode(), content_type="text/event-stream") as (upstream, received):
            with recording_proxy(self.records, upstream) as endpoint:
                post(endpoint)
                post(endpoint)
        self.assertEqual(len(received), 2)
        case = self.read()
        self.assertEqual(len(case.observations), 2)
        metrics = corrected_accounting([case])["metrics"]
        self.assertEqual(metrics["total"]["sum"], 240)
        self.assertEqual(metrics["cache_read"]["sum"], 80)

    def test_error_is_forwarded_without_retry_and_usage_is_unknown(self):
        body = b'{"error":{"message":"busy"}}'
        with fake_service(body, status=429) as (upstream, received):
            with recording_proxy(self.records, upstream) as endpoint:
                self.assertEqual(post(endpoint), (429, body))
        self.assertEqual(len(received), 1)
        case = self.read()
        self.assertFalse(case.coverage_complete)
        self.assertIsNone(corrected_accounting([case])["metrics"]["total"]["sum"])

    def test_truncated_sse_keeps_completed_usage_and_marks_incomplete(self):
        event = {"type": "response.completed", "response": {"id": "resp1", "usage": RAW}}
        body = ("data: " + json.dumps(event) + '\n\ndata: {"type":').encode()
        with fake_service(body, content_type="text/event-stream") as (upstream, _):
            with recording_proxy(self.records, upstream) as endpoint:
                post(endpoint)
        case = self.read()
        self.assertFalse(case.coverage_complete)
        self.assertEqual(case.observations[0].metrics["total"], 120)

    def test_conflicting_usage_and_no_requests(self):
        self.records.mkdir()
        self.assertIsNone(self.read().llm_called)
        self.records.rmdir()
        events = [{"type": "response.completed", "response": {"id": "resp1", "usage": raw}}
                  for raw in (RAW, {**RAW, "input_tokens": 200})]
        body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
        with fake_service(body, content_type="text/event-stream") as (upstream, _):
            with recording_proxy(self.records, upstream) as endpoint:
                post(endpoint)
        self.assertIsNone(self.read().observations[0].metrics["total"])

    def test_invalid_endpoint_rejected_before_listening(self):
        with self.assertRaises(ValueError):
            with recording_proxy(self.records, "https://user:secret@example.com/v1"):
                self.fail("must reject URL credentials")


if __name__ == "__main__":
    unittest.main()
