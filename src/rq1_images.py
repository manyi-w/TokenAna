"""Prepare RQ1 images before generation, using the shared Docker builder."""
import re
import subprocess

from .pilot_images import PYTHON, build, image_command, tools_image
from .records import write_json

REVISION = 'rq1-v1'
DEFAULT_VERIFIER = 'tokenana-verified-evaluator:latest'


def image_reference(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/@:-]*', value):
        raise ValueError('Image references must be nonempty Docker image names or IDs')
    return value


def identity(image):
    result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                            capture_output=True, text=True, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else None


def require_image(image):
    result = identity(image)
    if not result:
        raise ValueError('Image unavailable after preparation: ' + image)
    return result


def pull(output, image):
    image_reference(image)
    found = identity(image)
    if found:
        return found
    # A saved local ID has no registry location. Never substitute a newer image.
    if image.startswith('sha256:'):
        raise ValueError('Frozen image ID is missing; restore that exact image: ' + image)
    image_command(output, image, ['docker', 'pull', '--platform', 'linux/amd64', image], action='PULL')
    return require_image(image)


def native_tools(method):
    if method == 'turn_control':
        return tools_image('trae')
    return PYTHON, []


def task_dockerfile(method, base, tools, *, node=None):
    stages = f'FROM {tools} AS tools\n'
    if node:
        stages += f'FROM {node} AS node\n'
    text = stages + f'''FROM {base}
USER root
COPY --from=tools /opt/tokenana /opt/tokenana
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep libssl3 libstdc++6 \\
    && mkdir -p /logs/artifacts /logs/agent /logs/verifier /workspace /home/swe-bench
'''
    if method == 'run_free':
        text += '''COPY --from=node /usr/local /opt/tokenana-node
RUN ln -s /opt/tokenana-node/bin/node /usr/local/bin/node \\
    && ln -s /opt/tokenana-node/bin/claude /usr/local/bin/claude \\
    && (id nonroot >/dev/null 2>&1 || useradd -m -s /bin/bash nonroot) \\
    && chown -R nonroot:nonroot /testbed \\
    && git config --system --add safe.directory /testbed \\
    && test -f /opt/miniconda3/etc/profile.d/conda.sh
'''
    elif method in ('agent_diet', 'attn_compress'):
        # Original tools use this fixed interpreter path. It does not replace
        # the task's testbed environment or add a research tokenizer.
        text += '''RUN mkdir -p /home/swe-bench/conda_envs \\
    && ln -s /opt/tokenana /home/swe-bench/conda_envs/py312
'''
    return text + 'WORKDIR /testbed\nENTRYPOINT []\n'


def prepare_images(output, config, runtime):
    """Resolve every task and verifier to immutable IDs before any model call."""
    output.mkdir(parents=True, exist_ok=True)
    method = config.method.name
    ids = config.dataset.options['task_ids']
    overrides = runtime['images']
    verifier = runtime['verified_evaluation']['image']
    print(f'PREPARE RQ1: {method}, {len(ids)} tasks; logs: {output}. '
          'Missing images will be pulled/built before model calls.', flush=True)
    result = {'method': method, 'images': {}, 'verifier': None}
    write_json(output / 'images.json', result)
    # Build shared layers once; Docker reuses cached layers on later runs.
    tools = node = None
    if any(not overrides.get(case) for case in ids):
        name = f'tokenana/rq1-{method}-tools:{REVISION}'
        dockerfile, includes = native_tools(method)
        build(output, name, dockerfile, includes=includes)
        require_image(name)
        tools = name
        if method == 'run_free':
            name = f'tokenana/rq1-claude-code:{config.agent.options["spec"]["claude_cli_version"]}'
            version = config.agent.options['spec']['claude_cli_version']
            if not re.fullmatch(r'\d+\.\d+\.\d+', version):
                raise ValueError('Invalid frozen Claude CLI version')
            build(output, name, 'FROM node:22-bookworm-slim\n'
                  f'RUN npm install --global @anthropic-ai/claude-code@{version}\n')
            require_image(name)
            node = name
    if verifier == DEFAULT_VERIFIER:
        dockerfile, includes = tools_image('verified_verifier')
        build(output, verifier, 'FROM docker:27-cli AS dockercli\n' + dockerfile +
              'COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker\nENTRYPOINT []\n',
              includes=includes)
        result['verifier'] = require_image(verifier)
    else:
        result['verifier'] = pull(output, verifier)
    write_json(output / 'images.json', result)
    for index, case in enumerate(ids, 1):
        print(f'PREPARE RQ1 TASK: {index}/{len(ids)} {case}', flush=True)
        if overrides.get(case):
            image = pull(output, overrides[case])
        else:
            base = 'swebench/sweb.eval.x86_64.' + case.replace('__', '_1776_').lower() + ':latest'
            pull(output, base)
            name = f'tokenana/rq1-{case.lower()}-{method}:{REVISION}'
            build(output, name, task_dockerfile(method, base, tools, node=node))
            image = require_image(name)
        result['images'][case] = image
        write_json(output / 'images.json', result)
    return {**runtime, 'images': result['images'],
            'verified_evaluation': {**runtime['verified_evaluation'], 'image': result['verifier']}}
