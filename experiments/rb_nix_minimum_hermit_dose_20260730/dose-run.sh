#!/usr/bin/env bash
# dose-run.sh — run ONE nix build under hermit with a given dose (HERMIT_ARGS),
# inside rootless podman + nixos/nix:2.3.16, and print the sha256 of the built
# output content (the reproducibility witness) plus wall-clock seconds.
#
# Usage: HERMIT_ARGS="--no-namespace --strict" ./dose-run.sh
# Output (stdout, one line): "<sha256>  <elapsed_s>"  (or "BUILD_FAIL <elapsed_s>")
set -uo pipefail

IMAGE="${IMAGE:-docker.io/nixos/nix:2.3.16}"
DEVHERMIT="${DEVHERMIT:-/home/newton/work/dev-hermit}"
HERMIT_DIR="$DEVHERMIT/hermit/target/release"
HOSTLIBS="$DEVHERMIT/experiments/nix-hermit-container-approach_20260730/hostlibs"
HERMIT_ARGS="${HERMIT_ARGS:---no-namespace --strict}"

# Probe derivation: emits build-nondeterminism sources that hermit determinizes
# under --no-namespace (time + procfs uuid). nix -j1/--cores 1 keeps it serial.
# Probe uses bash BUILTINS only (nix build sandbox clears PATH; image is bash 4.x
# so no $EPOCHREALTIME). printf '%(%s)T' -> time() (hermit-virtualized); read from
# /proc/.../uuid -> RandomUuid (hermit-determinized). Both determinized even under
# --no-namespace (getpid-seeded $RANDOM is deliberately excluded; it stays
# nondeterministic under --no-namespace via the real host pid).
NIX_EXPR='derivation { name = "hermit-dose-probe"; system = "x86_64-linux"; builder = "/bin/sh"; args = [ "-c" "{ read a < /proc/sys/kernel/random/uuid; read b < /proc/sys/kernel/random/uuid; read up < /proc/uptime; echo a=$a; echo b=$b; echo up=$up; } > $out" ]; }'

# HERMIT_ARGS="native" runs nix-build WITHOUT hermit (control: proves the probe
# actually captures run-to-run nondeterminism).

export https_proxy="${https_proxy:-http://fwdproxy:8080}" http_proxy="${http_proxy:-http://fwdproxy:8080}"
export HTTPS_PROXY="$https_proxy" HTTP_PROXY="$http_proxy"

GUEST_CMD='p=$(nix-build --no-out-link --cores 1 --max-jobs 1 --option build-users-group "" --option sandbox false --option substituters "" -E '"'"''"$NIX_EXPR"''"'"' 2>/dev/null); if [ -n "$p" ] && [ -e "$p" ]; then sha256sum "$p" | cut -d" " -f1; else echo BUILD_FAIL; fi'

start=$(date +%s)
if [ "$HERMIT_ARGS" = "native" ]; then
  out=$(podman run --rm \
    --security-opt seccomp=unconfined \
    -v "$HERMIT_DIR":/hermit:ro -v "$HOSTLIBS":/hostlibs:ro \
    "$IMAGE" \
    /bin/sh -c "$GUEST_CMD" 2>/dev/null | tail -1)
else
  out=$(podman run --rm \
    --security-opt seccomp=unconfined \
    -e RUST_LOG="${RUST_LOG:-detcore=error}" \
    -v "$HERMIT_DIR":/hermit:ro -v "$HOSTLIBS":/hostlibs:ro \
    "$IMAGE" \
    /hostlibs/ld-linux-x86-64.so.2 --library-path /hostlibs \
    /hermit/hermit run $HERMIT_ARGS -- \
    /bin/sh -c "$GUEST_CMD" 2>/dev/null | tail -1)
fi
end=$(date +%s)
echo "${out:-BUILD_FAIL}  $((end - start))"
