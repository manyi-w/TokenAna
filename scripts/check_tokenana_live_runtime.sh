#!/usr/bin/env bash
set -euo pipefail

# Read-only preflight for the live command. It never pulls, builds, creates,
# starts, or removes a container.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${1:-$ROOT_DIR/experiments/runtime_verified_10.toml}"
CODEX_PATH="${2:-/opt/tokenana/codex}"

command -v docker >/dev/null || { echo "missing docker" >&2; exit 2; }
docker info >/dev/null || { echo "docker daemon is unavailable" >&2; exit 2; }
command -v conda >/dev/null || { echo "missing conda" >&2; exit 2; }

python3 - "$RUNTIME" "$CODEX_PATH" <<'PY'
import re
import sys
from pathlib import Path

runtime, codex_path = Path(sys.argv[1]), sys.argv[2]
text = runtime.read_text(encoding="utf-8")
images = re.findall(r'^"([^"]+)"\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
if not images:
    raise SystemExit("runtime has no [images] entries")
for instance, image in images:
    import subprocess
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        raise SystemExit(f"missing image for {instance}: {image}")
print(f"checked {len(images)} local images")
print(f"container Codex path: {codex_path}")
print("preflight passed; start a fake Responses API on host.docker.internal:8000 before run")
PY
