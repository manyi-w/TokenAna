"""Build only selected executors. Docker contexts explicitly exclude local secrets."""
import json
from pathlib import Path
import subprocess
import time

from .records import write_json
from .telemetry import span

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'pilot-v1'
PYTHON = '''FROM mambaorg/micromamba:2.3.2 AS python
USER root
RUN micromamba create -y -p /opt/tokenana -c conda-forge python=3.12.14 pip && micromamba clean --all --yes
ENV PATH=/opt/tokenana/bin:$PATH
'''


def build(output, name, dockerfile, *, includes=(), context=ROOT):
    directory = output / 'build'
    directory.mkdir(exist_ok=True)
    safe = name.replace('/', '_').replace(':', '_')
    path = directory / (safe + '.Dockerfile')
    path.write_text(dockerfile)
    # Dockerfile-specific ignore overrides root .dockerignore. No configuration,
    # credentials, histories or output directories enter an image build context.
    ignore = ['**']
    for item in includes:
        pieces = Path(item).parts
        for index in range(1, len(pieces) + 1):
            ignore.append('!' + '/'.join(pieces[:index]) + '/')
        ignore += ['!' + item, '!' + item + '/**']
    ignore += ['**/.git', '**/__pycache__', '**/node_modules', '**/target', '**/.DS_Store']
    Path(str(path) + '.dockerignore').write_text('\n'.join(ignore) + '\n')
    image_command(output, name, ['docker', 'build', '--platform', 'linux/amd64', '--progress', 'plain',
                               '-t', name, '-f', str(path), str(context)])


def image_command(output, name, command, *, action='BUILD'):
    """Record image preparation with shared progress and interruption handling."""
    directory = output / 'build'
    directory.mkdir(exist_ok=True)
    safe = name.replace('/', '_').replace(':', '_')
    log_path = directory / (safe + '.log')
    print(f'{action}: {name}; log: {log_path}', flush=True)
    with span(directory, 'image_' + action.lower(), image=name):
        started = time.monotonic()
        with log_path.open('a') as log:
            attempt_start = log.tell()
            with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT) as process:
                try:
                    while True:
                        try:
                            code = process.wait(timeout=15)
                            break
                        except subprocess.TimeoutExpired:
                            with log_path.open('rb') as stream:
                                stream.seek(max(attempt_start, log_path.stat().st_size - 2048))
                                lines = stream.read(2048).decode('utf-8', errors='replace').splitlines()
                            latest = next((line for line in reversed(lines) if line.strip()), 'waiting for build output')
                            print(f'{action} RUNNING: {name}; {time.monotonic() - started:.0f}s; {latest}', flush=True)
                except BaseException:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise
            if code:
                print(f'{action} FAILED: {name}; exit={code}; log: {log_path}', flush=True)
                raise subprocess.CalledProcessError(code, command)
        print(f'{action} DONE: {name}; {time.monotonic() - started:.0f}s', flush=True)


def tools_image(agent):
    if agent in ('mini', 'trae', 'verifier', 'verified_verifier'):
        source = {'mini': 'agents/mini_swe_agent/upstream', 'trae': 'agents/trae/upstream',
                  'verifier': 'datasets/deepswe/upstream/pier',
                  'verified_verifier': 'datasets/swe_bench_verified/upstream/swebench'}[agent]
        return PYTHON + f'''COPY {source}/ /build/source/
RUN /opt/tokenana/bin/python -m pip install /build/source && /opt/tokenana/bin/python -m pip freeze > /opt/tokenana/installed-packages.txt
RUN ln -s bin/python /opt/tokenana/python && mkdir -p /opt/tokenana/empty
''' + (f'RUN ln -s bin/{"mini" if agent == "mini" else "trae-cli"} /opt/tokenana/{"mini" if agent == "mini" else "trae-cli"}\n' if agent in ('mini', 'trae') else ''), [source]
    if agent == 'codex':
        return '''FROM rust:slim-bookworm AS builder
RUN apt-get update && apt-get install -y pkg-config libssl-dev build-essential cmake clang libclang-dev protobuf-compiler git
COPY agents/codex/controlled/ /build/codex/
WORKDIR /build/codex/codex-rs
ENV CARGO_BUILD_JOBS=2
RUN cargo build --release --locked -p codex-cli --bin codex --features codex-core/tokenana-turn-control,codex-core/tokenana-session
''' + PYTHON + '''COPY --from=builder /build/codex/codex-rs/target/release/codex /opt/tokenana/codex
RUN ln -s codex /opt/tokenana/codex-controlled
''', ['agents/codex/controlled']
    if agent == 'opencode':
        return '''FROM oven/bun:1.3.14 AS builder
USER root
RUN apt-get update && apt-get install -y git python3 build-essential
COPY agents/opencode/upstream/ /build/opencode/
WORKDIR /build/opencode
ENV OPENCODE_VERSION=1.0.0-tokenana-pilot OPENCODE_CHANNEL=latest
RUN bun install --frozen-lockfile
RUN cd packages/opencode && bun run script/build.ts --single --baseline --skip-install --skip-embed-web-ui
RUN mkdir /built && find packages/opencode/dist -type f -path '*/bin/opencode' -exec cp {} /built/opencode \\;
''' + PYTHON + '''COPY --from=builder /built/opencode /opt/tokenana/opencode
RUN mkdir -p /opt/tokenana/opencode-config/opencode && touch /opt/tokenana/opencode-config/opencode/.gitignore && chmod -R a-w /opt/tokenana/opencode-config
''', ['agents/opencode/upstream']
    raise ValueError('unknown executor image')


def prepare_images(output, rows, settings, *, jobs):
    from datasets.deepswe.tasks import select_tasks
    selected = {agent: sorted({r['case'] for r in rows if r['agent'] == agent})
                for agent in sorted({r['agent'] for r in rows})}
    datasets = {r['case']: r.get('dataset', 'deepswe') for r in rows}
    selected['verifier'] = sorted(case for case, dataset in datasets.items() if dataset == 'deepswe')
    info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}'], timeout=30))
    write_json(output / 'docker-environment.json', {key: info.get(key) for key in
        ('OSType', 'Architecture', 'Driver', 'DriverStatus', 'NCPU', 'MemTotal', 'ServerVersion')})
    if (output / 'images.json').exists():
        saved = json.loads((output / 'images.json').read_text())
        identities = [saved['controller']] + [saved[agent][case]
                     for agent, cases in selected.items() for case in cases]
        if 'verified' in datasets.values():
            identities.append(saved['verified_verifier'])
        for image in dict.fromkeys(identities):
            subprocess.run(['docker', 'image', 'inspect', image], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return saved
    agents = sorted({r['agent'] for r in rows})
    controller = 'tokenana/controller:' + REVISION
    build(output, controller, '''FROM docker:27-cli AS dockercli
''' + PYTHON + '''COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN python -m pip install tiktoken jinja2 httpx requests openai && python -m pip freeze > /opt/tokenana/installed-packages.txt
RUN python -c "import tiktoken; tiktoken.encoding_for_model('gpt-4o')"
ENTRYPOINT []
''')
    result = {'controller': controller, **{a: {} for a in (*agents, 'verifier')}}
    cases = selected['verifier']
    records = {r.task.instance_id: r for r in select_tasks(task_ids=cases)}
    for agent in (*agents, 'verifier'):
        if not selected[agent]:
            continue
        custom = settings.get('images', {}).get(agent)
        if not custom:
            tools = f'tokenana/{agent}-tools:{REVISION}'
            dockerfile, includes = tools_image(agent)
            build(output, tools, dockerfile, includes=includes)
        for case in selected[agent]:
            if custom:
                image = custom.format(task_id=case, dataset=datasets[case])
                if subprocess.run(['docker', 'image', 'inspect', image], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode:
                    subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', image], check=True)
            else:
                base = (records[case].config['environment'].get('docker_image') if datasets[case] == 'deepswe'
                        else 'swebench/sweb.eval.x86_64.' + case.lower().replace('__', '_1776_') + ':latest')
                if not base:
                    raise ValueError(f'{case}: task has no published docker_image')
                image = f'tokenana/{case}-{agent}:{REVISION}'
                dockerfile = f'FROM {tools} AS tools\nFROM {base}\nUSER root\nCOPY --from=tools /opt/tokenana /opt/tokenana\n'
                # Keep the original task Python first on PATH. Executors have their
                # own absolute interpreter and cannot change the task dependencies.
                dockerfile += 'RUN apt-get update && apt-get install -y --no-install-recommends ripgrep libssl3 libstdc++6 && mkdir -p /logs/artifacts /logs/agent /logs/verifier\n'
                if agent == 'verifier':
                    source = f'datasets/deepswe/data/tasks/{case}/tests'
                    dockerfile += f'COPY {source}/ /tests/\nRUN chmod +x /tests/test.sh\n'
                    includes = [source]
                else:
                    includes = []
                    dockerfile += 'RUN test ! -e /tests && test ! -e /solution\n'
                dockerfile += f"WORKDIR {'/app' if datasets[case] == 'deepswe' else '/testbed'}\nENTRYPOINT []\n"
                build(output, image, dockerfile, includes=includes)
            result[agent][case] = image
    if 'verified' in datasets.values():
        image = settings.get('images', {}).get('verified_verifier')
        if not image:
            image = 'tokenana/verified-verifier:' + REVISION
            dockerfile, includes = tools_image('verified_verifier')
            build(output, image, 'FROM docker:27-cli AS dockercli\n' + dockerfile +
                  'COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker\n', includes=includes)
        result['verified_verifier'] = image
    def identity(image):
        return subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                                       text=True, timeout=30).strip()
    result = {key: identity(value) if isinstance(value, str) else
              {case: identity(image) for case, image in value.items()} for key, value in result.items()}
    write_json(output / 'images.json', result)
    return result
