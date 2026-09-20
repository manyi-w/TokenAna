#!/usr/bin/env bash
set -euo pipefail

# Build the ten images selected by experiments/run_free_codex_verified_live.toml.
# This script is intended for Linux x86_64. It does not pull base images: pull
# them explicitly first if they are not already present on the host.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERFILE="$ROOT_DIR/agents/codex/Dockerfile.source-agent"
CONTEXT="$ROOT_DIR/agents/codex"

images=(
  astropy_1776_astropy-12907
  astropy_1776_astropy-13033
  astropy_1776_astropy-13236
  astropy_1776_astropy-13398
  astropy_1776_astropy-13453
  astropy_1776_astropy-13579
  astropy_1776_astropy-13977
  astropy_1776_astropy-14096
  astropy_1776_astropy-14182
  astropy_1776_astropy-14309
)

for suffix in "${images[@]}"; do
  base="swebench/sweb.eval.x86_64.${suffix}:latest"
  target="${base%:latest}-agent:latest"
  if ! docker image inspect "$base" >/dev/null 2>&1; then
    echo "missing base image: $base" >&2
    echo "pull it first with: docker pull $base" >&2
    exit 2
  fi
  echo "building $target"
  docker build --pull=false --network host \
    --build-arg "BASE_IMAGE=$base" \
    --tag "$target" \
    --file "$DOCKERFILE" "$CONTEXT"
done

echo "all TokenAna Codex images built"
