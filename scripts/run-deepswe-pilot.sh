#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "${CONDA_DEFAULT_ENV:-}" != tokenAna ]]; then
  if [[ -n "${TOKENANA_CONDA_SH:-}" ]]; then
    source "$TOKENANA_CONDA_SH"
  elif [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
  elif [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
  elif ! command -v conda >/dev/null 2>&1; then
    echo 'Activate conda environment tokenAna, or set TOKENANA_CONDA_SH to conda.sh.' >&2
    exit 2
  else
    source "$(conda info --base)/etc/profile.d/conda.sh"
  fi
  conda activate tokenAna
fi
cd "$ROOT_DIR"
exec python -B -m src.pilot "$@"
