#!/usr/bin/env bash
# run-build.sh — run a nix build under hermit --strict inside rootless podman.
# NO host nix-install, NO host-root. Usage: run-build.sh <label> [hermit-args...]
set -uo pipefail
cd "$(dirname "$0")/.."
export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export HTTPS_PROXY=$https_proxy HTTP_PROXY=$http_proxy
HREL=/home/newton/work/dev-hermit/hermit/target/release
HL=$PWD/hostlibs
label="${1:?label}"; shift || true
HERMIT_ARGS=("${@:---strict --no-namespace}")
podman run --rm --security-opt seccomp=unconfined \
  -e NIX_CONFIG=$'build-users-group =\nsandbox = false\nsubstituters =\nexperimental-features = nix-command' \
  -v "$HREL":/hermit:ro -v "$HL":/hostlibs:ro \
  docker.io/nixos/nix:latest \
  /hostlibs/ld-linux-x86-64.so.2 --library-path /hostlibs \
  /hermit/hermit run $HERMIT_ARGS -- \
  nix-build --no-out-link --no-substitute --option substituters '' \
    -E 'derivation { name = "hermit-container-hello"; builder = "/bin/sh"; args = [ "-c" "echo deterministic-hello-from-hermit > $out" ]; system = "x86_64-linux"; }'
