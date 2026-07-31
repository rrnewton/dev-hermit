#!/usr/bin/env bash
# nix-under-hermit.sh — run a Nix build under `hermit run --strict` with NO host
# nix-install and NO host-root, using a nixos/nix OCI image in ROOTLESS podman.
#
# This is the Option-1 recipe from task nix-without-host-install-container-approach.
# Podman provides the container (filesystem + namespaces); hermit provides
# deterministic execution via its ptrace backend INSIDE the rootless userns.
#
# Key requirements discovered (see ../README.md for the full analysis):
#   * The host-built hermit binary is dynamically linked with interpreter
#     /lib64/ld-linux-x86-64.so.2, which does NOT exist in the nixos/nix image.
#     So we bind-mount a "hostlibs" dir (ld-linux + the libs from `ldd hermit`)
#     and invoke hermit through the explicit loader.
#   * podman MUST run with `--security-opt seccomp=unconfined`: the default
#     seccomp profile makes personality(2) return ENOSYS, which reverie uses to
#     disable ASLR; without it hermit aborts ("could not disable ASLR").
#   * hermit runs `--no-namespace`: full-namespace mode needs mount/UTS caps that
#     rootless podman cannot grant (Hostname/Mount EPERM). Podman already
#     supplies the container isolation, so hermit only needs to determinize.
#   * Use a PRE-pidfd Nix (e.g. nixos/nix:2.3.16). Modern Nix (2.35) manages the
#     builder via pidfd_send_signal (syscall 424), which detcore classifies
#     Unsupported -> fails closed under --strict (and its non-strict passthrough
#     also fails for a pid-virtualized pidfd). Until detcore implements 424, use
#     old Nix, or drop --strict once 424 passthrough is fixed.
set -euo pipefail

IMAGE="${IMAGE:-docker.io/nixos/nix:2.3.16}"
HERMIT_DIR="${HERMIT_DIR:-/home/newton/work/dev-hermit/hermit/target/release}"
HOSTLIBS="${HOSTLIBS:-$(cd "$(dirname "$0")/.." && pwd)/hostlibs}"
HERMIT_ARGS="${HERMIT_ARGS:---strict --no-namespace}"
# Default guest: build a trivial derivation and print its store path.
NIX_EXPR="${NIX_EXPR:-derivation { name = \"hermit-strict-hello\"; builder = \"/bin/sh\"; args = [ \"-c\" \"echo hi > \$out\" ]; system = \"x86_64-linux\"; }}"

# Populate hostlibs from the current hermit binary if missing.
if [ ! -f "$HOSTLIBS/ld-linux-x86-64.so.2" ]; then
  mkdir -p "$HOSTLIBS"
  for l in $(ldd "$HERMIT_DIR/hermit" | awk '/=>/{print $3}'); do cp -Lu "$l" "$HOSTLIBS/"; done
  cp -Lu /lib64/ld-linux-x86-64.so.2 "$HOSTLIBS/"
fi

# with-proxy env for the image pull (fwdproxy is IPv6-only; no -4).
export https_proxy="${https_proxy:-http://fwdproxy:8080}" http_proxy="${http_proxy:-http://fwdproxy:8080}"
export HTTPS_PROXY="$https_proxy" HTTP_PROXY="$http_proxy"

exec podman run --rm \
  --security-opt seccomp=unconfined \
  -e RUST_LOG="${RUST_LOG:-detcore=error}" \
  -v "$HERMIT_DIR":/hermit:ro \
  -v "$HOSTLIBS":/hostlibs:ro \
  "$IMAGE" \
  /hostlibs/ld-linux-x86-64.so.2 --library-path /hostlibs \
  /hermit/hermit run $HERMIT_ARGS -- \
  nix-build --no-out-link \
    --option build-users-group '' --option sandbox false --option substituters '' \
    -E "$NIX_EXPR"
