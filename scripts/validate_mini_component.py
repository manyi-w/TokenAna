"""Actual prepared mini CLI + loopback fake model. No paid model or benchmark claim."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--executable', default='/opt/tokenana/mini')
    parser.add_argument('--python', default='/opt/tokenana/bin/python')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if sys.version_info[:3] != (3, 12, 14):
        raise ValueError('Prepared Python 3.12.14 required')
    from agents.mini_swe_agent.adapter import MiniSweAgent
    from src.accounting_v2 import corrected_v2
    from src.interfaces import ArtifactDirectory
    from src.raw_usage import read_raw_usage
    from src.records import write_json
    from src.session import SessionDecision
    repo = args.output/'repository'
    repo.mkdir()
    def git(*command):
        subprocess.run(['git', '-C', str(repo), *command], check=True, capture_output=True)
    git('init')
    (repo/'example.py').write_text('value = 1\n')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            tools = sum(m.get('role') == 'tool' for m in request.get('messages', []))
            command = 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' if tools else "printf 'value = 2\\n' > example.py"
            usage = dict(prompt_tokens=20, completion_tokens=10, total_tokens=30,
                         prompt_tokens_details={'cached_tokens': 0}, completion_tokens_details={'reasoning_tokens': 0})
            response = dict(id=f'fake-{tools}', object='chat.completion', created=1, model=request['model'],
                choices=[dict(index=0, finish_reason='tool_calls', message={'role': 'assistant', 'content': 'Fixture action',
                    'tool_calls': [{'id': f'call-{tools}', 'type': 'function', 'function': {'name': 'bash', 'arguments': json.dumps({'command': command})}}]})], usage=usage)
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    class Workspace:
        root = str(repo)
        def __init__(self, output):
            self.artifacts = ArtifactDirectory(output, str(output))
            output.mkdir()
        def new_artifacts(self):
            p = Path(tempfile.mkdtemp(prefix='call-', dir=self.artifacts.host))
            return ArtifactDirectory(p, str(p))
        def launch_command(self, argv):
            return list(argv)
        def execute(self, argv, *, timeout=None):
            return subprocess.run(argv, cwd=repo, timeout=timeout, text=True, capture_output=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ['TOKENANA_FAKE_KEY'] = 'fake-local-only'
    results = []
    try:
        options = dict(executable=args.executable, python_executable=args.python, model='openai/gpt-4o',
            model_class='litellm', model_protocol='chat_completions', model_provider='openai',
            model_api_key_env='TOKENANA_FAKE_KEY', model_base_url=f'http://127.0.0.1:{server.server_port}/v1',
            record_raw_usage=True, cost_tracking='ignore_errors', timeout=120)
        agent = MiniSweAgent()
        for mode in ('native', 'controlled', 'session'):
            git('checkout', '--', 'example.py')
            workspace = Workspace(args.output/mode)
            selected = {**options, **({'turn_control': {'initial': 3, 'final': 5}} if mode == 'controlled' else {})}
            result = (agent.run_session('Set example.py value to 2 and submit.', workspace, selected, lambda event: SessionDecision())
                      if mode == 'session' else agent.run('Set example.py value to 2 and submit.', workspace, selected))
            usage = read_raw_usage(result.artifacts.host/'api-records', case_id='fixture', attempt_id='1', call_id='main', forwarded_only=True)
            report = corrected_v2([usage])
            ok = result.error is None and (repo/'example.py').read_text() == 'value = 2\n' and report['metrics']['total']['sum'] == 60
            entry = dict(mode=mode, passed=ok, error=result.error, artifacts=str(result.artifacts.host),
                         token_total=report['metrics']['total']['sum'])
            results.append(entry)
            write_json(args.output/'component-validation.json', {'kind': 'actual mini + fake loopback model; fixture repository', 'results': results})
            print(json.dumps(entry), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return int(not all(r['passed'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
