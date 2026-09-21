"""Retain research evidence without copying the base environment per task."""
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import threading

from .records import write_json
from .telemetry import span


def retention_mode(runtime):
    # Snapshots written before this option existed used full retention. New runs
    # explicitly freeze research mode in their runtime instead of reinterpreting them.
    mode = runtime.get('retention_mode', 'full')
    if mode not in ('research', 'full'):
        raise ValueError('retention_mode must be research or full')
    return mode


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
                script = ('set --; for p in app testbed tmp root home logs; do '
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


def _changes(text, roots):
    """Docker's writable-layer changes, including committed edits and deletions."""
    result = {}
    for line in text.splitlines():
        kind, separator, path = line.partition(' ')
        if not separator or kind not in ('A', 'C', 'D') or not path.startswith('/'):
            raise ValueError('unreadable docker diff; container retained')
        if '..' in PurePosixPath(path).parts:
            raise ValueError('unsafe docker diff path')
        if any(path == root or path.startswith(root + '/') for root in roots):
            result[path] = kind
    return result


def _selected(path, changes):
    if changes is None:
        return True  # A writable mount is not covered by docker diff.
    if changes.get(path) in ('A', 'C'):
        return True
    return any(changes.get(str(parent)) == 'A' for parent in PurePosixPath(path).parents)


def _copy_tree(name, root, archive, changes, stderr, excluded=()):
    """Stream a stopped container's tree, storing only changed members.

    docker cp is used instead of running Git/tar inside a stopped task. No full
    temporary tree is written to disk, and symlinks are not dereferenced.
    """
    prefix = PurePosixPath(root).name
    seen = set()
    with subprocess.Popen(['docker', 'cp', name + ':' + root, '-'],
                          stdout=subprocess.PIPE, stderr=stderr) as process:
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as source:
                for member in source:
                    relative = PurePosixPath(member.name)
                    if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != prefix:
                        raise ValueError('unexpected docker cp archive path')
                    path = str(PurePosixPath(root).parent / relative)
                    if (_selected(path, changes) and
                            not any(path == p or path.startswith(p + '/') for p in excluded)):
                        archive.addfile(member, source.extractfile(member) if member.isfile() else None)
                        seen.add(path)
            # Drain tar padding before waiting, so the writer cannot block on a full pipe.
            while process.stdout.read(1024 * 1024):
                pass
            if process.wait(timeout=1800):
                raise RuntimeError('docker cp failed; see archive.stderr')
        except BaseException:
            process.kill()
            raise
        finally:
            process.stdout.close()
    if changes is not None:
        expected = {p for p, k in changes.items() if k != 'D' and (p == root or p.startswith(root + '/'))}
        if expected - seen:
            raise ValueError('changed workspace entries missing from archive; container retained')
    return len(seen)


def _research_archive(name, directory, inspected, root, persisted_mounts):
    # A stable tag keeps the shared base image discoverable after builder tags move.
    # Docker stores common layers once. No per-task docker save/export is created.
    image = inspected['Image']
    tag = 'tokenana-retained:' + image.removeprefix('sha256:')
    subprocess.run(['docker', 'image', 'tag', image, tag], check=True,
                   capture_output=True, timeout=30)
    roots = (root, '/logs')
    changes = _changes(subprocess.check_output(['docker', 'diff', name], text=True, timeout=120), roots)
    mounts, external_mounts = [], []
    for index, mount in enumerate(inspected.get('Mounts', [])):
        destination = mount['Destination']
        item = {k: mount.get(k) for k in ('Type', 'Destination', 'RW')}
        if not mount.get('RW'):
            item['storage'] = 'read_only'
        elif (destination in persisted_mounts or
              (mount.get('Type') == 'bind' and Path(mount['Source']).resolve().is_relative_to(directory.resolve()))):
            item['storage'] = 'already_in_result_directory'
            item['result_path'] = (persisted_mounts[destination] if destination in persisted_mounts else
                                   str(Path(mount['Source']).resolve().relative_to(directory.resolve())))
        else:
            item.update(storage='archive', archive=f'mount-{index:03d}.tar.gz')
            external_mounts.append(item)
        mounts.append(item)
    # Mount contents do not belong to the image's writable layer. Already-persisted
    # logs/artifacts are left in place, not duplicated in the delta archive.
    mounted = [m['Destination'] for m in mounts]
    changes = {p: k for p, k in changes.items()
               if not any(p == m or p.startswith(m + '/') for m in mounted)}
    target = directory / 'workspace-delta.tar.gz'
    count = 0
    with (directory / 'archive.stderr').open('ab') as err:
        with tarfile.open(target, 'w:gz') as archive:
            for tree in roots:
                if any(k != 'D' and (p == tree or p.startswith(tree + '/')) for p, k in changes.items()):
                    count += _copy_tree(name, tree, archive, changes, err, mounted)
        verify_tar(target, allow_empty=True)
        for mount in external_mounts:
            path = directory / mount['archive']
            with tarfile.open(path, 'w:gz') as archive:
                _copy_tree(name, mount['Destination'], archive, None, err)
            verify_tar(path, allow_empty=True)
            mount['archive_bytes'] = path.stat().st_size
    return dict(base_image_tag=tag, archive=target.name, archived_entries=count, mounts=mounts,
                archive_bytes=target.stat().st_size,
                scope=list(roots), changes=changes,
                deleted=sorted(p for p, kind in changes.items() if kind == 'D'),
                restore='Use the saved image ID, remove deleted paths, then overlay workspace-delta.tar.gz; '
                        'restore writable mounts from their recorded result directory or archive.',
                boundary='Final workspace and logs only; no per-request filesystem history or OS/home/cache snapshot.')


def archive_container(name, directory, *, mode='research', root='/app', persisted_mounts=()):
    directory = Path(directory)
    retention_mode({'retention_mode': mode})
    result = {'version': 2, 'mode': mode, 'container': name, 'complete': False, 'retained': True}
    try:
        with span(directory, 'archive'):
            # Stop before export so a timed-out docker exec cannot keep editing files.
            subprocess.run(['docker', 'stop', '--time', '10', name], check=True,
                           capture_output=True, timeout=45)
            inspected = json.loads(subprocess.check_output(
                ['docker', 'inspect', name], timeout=30))[0]
            result['image_id'] = inspected['Image']
            if mode == 'research':
                result.update(_research_archive(name, directory, inspected, root, persisted_mounts))
                result['complete'] = True
                return result
            archive = directory / 'container-filesystem.tar'
            with archive.open('wb') as out, (directory / 'archive.stderr').open('ab') as err:
                subprocess.run(['docker', 'export', name], stdout=out, stderr=err,
                               check=True, timeout=1800)
            verify_tar(archive)
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
