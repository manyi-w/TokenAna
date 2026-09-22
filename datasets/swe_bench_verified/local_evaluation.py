"""Run the local, unchanged official harness in a prepared verifier image."""
import json
from pathlib import Path
import shutil
import subprocess

from src.records import write_json


def plan(run, submission):
    run = Path(run).resolve()
    runtime = json.loads((run / 'runtime.json').read_text())
    config = json.loads((run / 'config.json').read_text())
    options = runtime.get('verified_evaluation', {})
    image = options.get('image')
    if not image:
        raise ValueError('runtime.verified_evaluation.image must contain the local official harness')
    data = Path(__file__).parent / 'data/swe_bench_verified.json'
    shared = run / 'evaluation' / 'official-harness'
    task_ids = config['dataset']['options'].get('task_ids')
    if task_ids is None:
        state = json.loads((run / 'state.json').read_text())
        task_ids = list(state['tasks'])
    command = ['docker', 'run', '--name', 'tokenana-verified-' + submission['run_id'],
        '--init', '--platform', 'linux/amd64', '-v', '/var/run/docker.sock:/var/run/docker.sock',
        '-v', f'{run}:{run}', '-v', f'{data.parent}:{data.parent}:ro', '-w', str(shared),
        '--entrypoint', options.get('python', '/opt/tokenana/bin/python'), image,
        '-m', 'swebench.harness.run_evaluation', '--dataset_name', str(data), '--split', 'test',
        '--predictions_path', submission['predictions'], '--run_id', submission['run_id'],
        '--max_workers', str(options.get('jobs', 4)), '--cache_level', 'instance', '--clean', 'False',
        '--namespace', options.get('namespace', 'swebench'), '--instance_ids', *task_ids]
    return dict(kind='local', command=command, directory=str(shared), run_id=submission['run_id'])


def evaluate_local(run, submission, directory, previous_report=None):
    selected = plan(run, submission)
    shared = Path(selected['directory'])
    shared.mkdir(parents=True, exist_ok=True)
    # The harness itself resumes from its per-instance reports in this stable cwd.
    name = 'tokenana-verified-' + submission['run_id']
    inspect = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', name],
                             text=True, capture_output=True)
    if inspect.returncode == 0:
        if inspect.stdout.strip() == 'true':
            raise ValueError('Previous Verified evaluator is still running')
        subprocess.run(['docker', 'rm', name], check=True, capture_output=True)
    write_json(directory / 'command.json', selected)
    with (directory / 'stdout.txt').open('w') as out, (directory / 'stderr.txt').open('w') as err:
        process = subprocess.run(selected['command'], stdout=out, stderr=err)
    write_json(directory / 'execution.json', {'returncode': process.returncode, 'container': name,
                                             'generation_repeated': False})
    process.check_returncode()
    reports = []
    for path in shared.glob('*.json'):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and 'resolved_ids' in data:
            reports.append(path)
    if len(reports) != 1:
        raise ValueError('Official harness did not produce a unique aggregate report; inspect evaluator logs')
    target = directory / 'report.json'
    shutil.copy2(reports[0], target)
    # Logs are mounted and the evaluator container is retained for inspection.
    return target
