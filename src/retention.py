"""Workspace snapshots and verified archives; never discard failed archives."""
import json
from pathlib import Path
import subprocess
import tarfile
import threading

from .records import write_json
from .telemetry import span


def verify_tar(path, *, allow_empty=False):
    # Streaming through every member detects incomplete data, not just a valid header.
    count = 0
    with tarfile.open(path, 'r|*') as archive:
        for member in archive:
            count += 1
            if member.isfile():
                stream = archive.extractfile(member)
                while stream.read(1024 * 1024):
                    pass
    if not count and not allow_empty:
        raise ValueError('empty filesystem archive')


class Snapshots:
    def __init__(self, container, directory):
        self.container, self.directory = container, Path(directory)
        self.lock = threading.Lock()
        self.number = 0

    def __call__(self, metadata=None):
        with self.lock:
            self.number += 1
            target = self.directory / 'fs-snapshots'
            target.mkdir(exist_ok=True)
            name = f'{self.number:06d}'
            with span(self.directory, 'recording_snapshot', snapshot=name):
                # Listed-incremental tar records deletions and every changed file.
                # The initial image plus these snapshots reconstruct these trees.
                script = ('set --; for p in app tmp root home logs; do '
                          'test ! -e /"$p" || set -- "$@" "$p"; done; '
                          'tar --listed-incremental=/workspace/output/fs-snapshots/state.snar '
                          '-czf /workspace/output/fs-snapshots/' + name + '.tar.gz '
                          '-C / "$@"')
                process = subprocess.run(['docker', 'exec', self.container, 'bash', '-c', script],
                                         capture_output=True, text=True, timeout=300)
                write_json(target / (name + '.json'), {
                    'boundary': 'before_recorded_model_request', 'request': metadata,
                    'returncode': process.returncode, 'stderr': process.stderr})
                if process.returncode:
                    raise RuntimeError('workspace snapshot failed; see fs-snapshots')
                verify_tar(target / (name + '.tar.gz'))


def archive_container(name, directory):
    directory = Path(directory)
    result = {'container': name, 'complete': False, 'retained': True}
    try:
        with span(directory, 'archive'):
            # Stop before export so a timed-out docker exec cannot keep editing files.
            subprocess.run(['docker', 'stop', '--time', '10', name], check=True,
                           capture_output=True, timeout=45)
            archive = directory / 'container-filesystem.tar'
            with archive.open('wb') as out, (directory / 'archive.stderr').open('ab') as err:
                subprocess.run(['docker', 'export', name], stdout=out, stderr=err,
                               check=True, timeout=1800)
            verify_tar(archive)
            inspected = json.loads(subprocess.check_output(
                ['docker', 'inspect', name], timeout=30))[0]
            # docker export excludes volume contents. Explicitly archive all writable
            # mounts except /workspace/output, already persisted beside this archive.
            for index, mount in enumerate(inspected.get('Mounts', [])):
                if not mount.get('RW') or mount['Destination'] == '/workspace/output':
                    continue
                path = directory / f'mount-{index:03d}.tar'
                with path.open('wb') as out:
                    subprocess.run(['docker', 'cp', name + ':' + mount['Destination'] + '/.', '-'],
                                   stdout=out, stderr=subprocess.PIPE, check=True, timeout=1800)
                verify_tar(path, allow_empty=True)
            result.update(complete=True, image_id=inspected['Image'],
                          mounts=[{k: m.get(k) for k in ('Type', 'Destination', 'RW')}
                                  for m in inspected.get('Mounts', [])])
    except BaseException as error:
        result['error_type'] = type(error).__name__
        raise
    finally:
        write_json(directory / 'retention.json', result)
    return result
