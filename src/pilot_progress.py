"""Read-only progress from persistent artifacts; never executes agent commands."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import time


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


class Progress:
    def __init__(self, root, emit=print):
        self.root = Path(root)
        self.emit = emit
        self.offsets = {}
        self.last = 0

    def poll(self):
        active = {}
        for path in sorted(self.root.rglob('timing.jsonl')):
            for line in path.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                key = (str(path), event.get('id'))
                if event.get('event') == 'start':
                    active[key] = event
                else:
                    active.pop(key, None)
        now = time.time()
        if now - self.last >= 15:
            self.last = now
            stages = ', '.join(f"{e['phase']} {max(0, now-e['utc_seconds']):.0f}s"
                               for e in active.values()) or 'no open phase recorded'
            counts = Counter()
            for path in self.root.rglob('metadata.json'):
                if 'api-records' in path.parts or 'auxiliary-records' in path.parts:
                    data = read_json(path)
                    counts[str(data.get('status') or 'pending')] += 1
            self.emit(f'[{self.root.name}] STATUS {stages}; HTTP={dict(counts)} (pending includes snapshot/wait; no new output does not prove a hang)', flush=True)
        for path in sorted(self.root.rglob('*')):
            if path.name not in ('container.jsonl', 'controller.json', 'live-stdout.txt',
                                 'live-stderr.txt', 'launcher.log') or not path.is_file():
                continue
            offset = self.offsets.get(path, 0)
            size = path.stat().st_size
            if size == offset:
                continue
            # Attach shows a bounded recent tail; original files remain intact.
            start = max(offset if size >= offset else 0, size - 12000)
            with path.open('rb') as stream:
                stream.seek(start)
                raw = stream.read(12000)
            self.offsets[path] = start + len(raw)
            content = raw.decode('utf-8', errors='replace')
            content = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', content)
            content = ''.join(c for c in content if c in '\n\t' or ord(c) >= 32)
            self.emit(f'[{self.root.name}] {path.relative_to(self.root)}\n{content}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Watch saved pilot progress without restarting it; Ctrl-C only stops watching.')
    parser.add_argument('directory', type=Path)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error('run directory does not exist')
    progress = Progress(args.directory)
    try:
        while True:
            progress.poll()
            if args.once:
                return
            time.sleep(2)
    except KeyboardInterrupt:
        return


if __name__ == '__main__':
    main()
