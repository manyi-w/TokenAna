#!/usr/bin/env bash
# Review-only entry point. Nothing invokes this from TokenAna runtime.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
mode="${1:-}"
destination="${2:-}"
if [[ $# != 2 || "$destination" != /* || "$destination" == / ]]; then
    echo 'Usage: bash build.sh --apply-reviewed-patch|--build-offline /absolute/new/copy' >&2
    exit 2
fi
case "$mode" in
    --apply-reviewed-patch)
        # mkdir must fail for any existing destination; never patch the original.
        mkdir -- "$destination"
        cp -R "$script_dir/../upstream/." "$destination/"
        (
            cd -- "$destination"
            patch -p1 -t -N -i "$script_dir/turn-control.patch"
            patch -p1 -t -N -i "$script_dir/session-chat.patch"
            patch -p1 -t -N -i "$script_dir/deepswe-pilot.patch"
        )
        printf '%s\n' codex-sampling-boundary-v1 > "$destination/.tokenana-controlled-copy"
        echo "Prepared controlled copy: $destination (not built)"
        ;;
    --build-offline)
        [[ -f "$destination/.tokenana-controlled-copy" ]] || {
            echo 'Missing reviewed controlled-copy marker' >&2; exit 2;
        }
        # Explicit native toolchain paths avoid rustup bootstrap/download behavior.
        : "${TOKENANA_CARGO:?Set absolute path to preinstalled native cargo (not rustup shim)}"
        : "${RUSTC:?Set absolute path to preinstalled native rustc}"
        : "${RUSTDOC:?Set absolute path to preinstalled native rustdoc}"
        for executable in "$TOKENANA_CARGO" "$RUSTC" "$RUSTDOC"; do
            [[ "$executable" == /* && -x "$executable" ]] || {
                echo "Invalid toolchain executable: $executable" >&2; exit 2;
            }
        done
        export RUSTC RUSTDOC CARGO_NET_OFFLINE=true
        export CARGO_TARGET_DIR="$destination/tokenana-control-target"
        cd -- "$destination/codex-rs"
        "$TOKENANA_CARGO" build --offline --locked --release -p codex-cli --bin codex \
            --features codex-core/tokenana-turn-control,codex-core/tokenana-session
        echo "Controlled executable: $CARGO_TARGET_DIR/release/codex"
        ;;
    *) echo 'Unknown mode; no action taken' >&2; exit 2 ;;
esac
