#!/usr/bin/env bash
set -euo pipefail

# Pull the official base images required by runtime_verified_10.toml.
# This is an explicit network operation; it is never invoked by tokenAna run.
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
  docker pull "swebench/sweb.eval.x86_64.${suffix}:latest"
done
