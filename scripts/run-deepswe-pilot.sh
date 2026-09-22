#!/usr/bin/env bash
# Compatibility entry point; use run-experiments.sh for new commands.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$SCRIPT_DIR/run-experiments.sh" "$@"
