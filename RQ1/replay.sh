#!/usr/bin/env bash
# Rebuild RQ1 from existing author archives. Does not run model experiments.
set -euo pipefail

RQ1_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="$(cd -- "$RQ1_DIR/.." && pwd -P)"

if [[ $# -gt 0 ]]; then
  if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
    echo 'Usage: bash RQ1/replay.sh'
    echo 'Offline archive replay only. Writes RQ1/reports; missing actual cache usage stays unknown.'
    echo 'Requires conda environment tokenAna (Python 3.12.14) and bsdtar or 7z.'
    exit 0
  fi
  echo 'This script accepts no experiment arguments; it only rebuilds the offline RQ1 report.' >&2
  exit 2
fi

source "$RQ1_DIR/environment.sh"

cd "$REPO_DIR"
echo 'RQ1: offline archive replay only; no model calls or official evaluation.'
echo 'Actual cache read/write and corrected cost remain unknown when evidence is missing.'
exec python -B -m src.rq1 --study "$RQ1_DIR"
