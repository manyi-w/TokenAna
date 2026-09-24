#!/usr/bin/env bash
set -euo pipefail
RQ1_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$RQ1_DIR/environment.sh"
cd "$RQ1_DIR/.."
exec python -B -m src.rq1_run turn_control "$@"
