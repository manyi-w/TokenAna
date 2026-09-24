#!/usr/bin/env bash
# Shared environment setup for RQ1 launchers; no dependency installation.
if [[ "${CONDA_DEFAULT_ENV:-}" != tokenAna ]]; then
  if [[ -n "${TOKENANA_CONDA_SH:-}" ]]; then
    source "$TOKENANA_CONDA_SH"
  elif command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
  elif [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
  elif [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
  else
    echo 'Activate tokenAna, or set TOKENANA_CONDA_SH to conda.sh.' >&2
    exit 2
  fi
  conda activate tokenAna
fi
python -B -c 'import sys; sys.exit(0 if sys.version_info[:3] == (3, 12, 14) else "RQ1 requires Python 3.12.14 in tokenAna.")'
