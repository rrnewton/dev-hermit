#!/usr/bin/env bash
# bootstrap.sh — make this experiment runnable again after `/nix` disappears.
#
# WHY THIS EXISTS: `/nix` on a Meta devserver is EPHEMERAL. Chef reverts it.
# Every number in this experiment was measured against a host nix install that
# will not survive. Run this to recreate an equivalent one, then `./run.sh`.
#
# Two paths, in order of preference:
#
#   host     recreate the single-user, no-daemon, sandbox=false install this
#            experiment used, pinned to the same nix version and the same
#            nixpkgs revision. Requires fwdproxy egress (verified reachable:
#            cache.nixos.org returns 200 through http://fwdproxy:8080).
#
#   podman   rootless `docker.io/nixos/nix:2.3.16`, the provisioning path from
#            experiments/nix-hermit-container-approach_20260730/. That image IS
#            cached on this box today. Use it when the host install is
#            impossible or keeps being reverted. Note the division of labour:
#            **podman provides a reproducible nix INSTALLATION; hermit provides
#            the determinism.** Since `--tmp=/tmp` keeps all of hermit's
#            namespaces, podman is no longer needed for isolation.
#
# Usage:
#   ./bootstrap.sh check          # report what is available (default)
#   ./bootstrap.sh host           # install/repair the host nix path
#   ./bootstrap.sh podman         # verify the container path works
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Exact versions this experiment was measured against; see metadata.json.
NIX_VERSION="2.30.2"
NIXPKGS_REV="cab778239e705082fe97bb4990e0d24c50924c04"   # nixpkgs 25.11pre839900
NIX_IMAGE="docker.io/nixos/nix:2.3.16"

export http_proxy="${http_proxy:-http://fwdproxy:8080}"
export https_proxy="${https_proxy:-http://fwdproxy:8080}"
export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy"
export no_proxy="${no_proxy:-.facebook.com,.internalfb.com,.tfbnw.net,.fbcdn.net,localhost,127.0.0.1,::1}"
export NO_PROXY="$no_proxy"

ok()   { printf '  OK   %s\n' "$*"; }
bad()  { printf '  MISS %s\n' "$*"; }

do_check() {
  echo "== host nix =="
  if [ -x "$HOME/.nix-profile/bin/nix" ]; then
    ok "$($HOME/.nix-profile/bin/nix --version)  (measured against $NIX_VERSION)"
    ok "/nix is $(du -sh /nix 2>/dev/null | cut -f1)"
    local rev
    rev=$(readlink -f "$HOME/.nix-defexpr/channels/nixpkgs" 2>/dev/null)
    [ -n "$rev" ] && ok "nixpkgs channel -> $rev" || bad "no nixpkgs channel"
    grep -q 'sandbox = false' "$HOME/.config/nix/nix.conf" 2>/dev/null \
      && ok "sandbox = false" || bad "sandbox = false NOT set (required: the seam interposes on the builder directly)"
  else
    bad "no host nix at ~/.nix-profile/bin/nix  -> run: ./bootstrap.sh host"
  fi
  echo "== proxy egress =="
  if curl -sS -o /dev/null -m 20 -w '%{http_code}' https://cache.nixos.org/nix-cache-info 2>/dev/null | grep -q 200; then
    ok "cache.nixos.org reachable through $https_proxy"
  else bad "cache.nixos.org NOT reachable; substitution of build inputs will fail"; fi
  echo "== podman fallback =="
  if command -v podman >/dev/null; then
    ok "podman $(podman --version | awk '{print $3}')"
    podman image exists "$NIX_IMAGE" 2>/dev/null && ok "$NIX_IMAGE cached" || bad "$NIX_IMAGE not cached (pull needs egress)"
  else bad "no podman"; fi
  echo "== hermit =="
  local h="${HERMIT:-/home/newton/work/dev-hermit/worktrees/nix-repro176/hermit/target/release/hermit}"
  [ -x "$h" ] && ok "$("$h" --version)" || bad "no hermit at $h (build: cargo build --release --bin hermit)"
}

do_host() {
  if [ -x "$HOME/.nix-profile/bin/nix" ]; then
    echo "host nix already present; not reinstalling. Delete ~/.nix-profile and /nix to force."
  else
    echo "installing nix $NIX_VERSION (single-user, no daemon)"
    curl -L "https://releases.nixos.org/nix/nix-${NIX_VERSION}/install" -o /tmp/nix-install.sh || exit 1
    sh /tmp/nix-install.sh --no-daemon || exit 1
  fi
  mkdir -p "$HOME/.config/nix"
  if [ -e "$HOME/.config/nix/nix.conf" ] && ! grep -q 'sandbox = false' "$HOME/.config/nix/nix.conf"; then
    echo "REFUSING to overwrite an existing ~/.config/nix/nix.conf (shared machine state)."
    echo "Add these lines yourself:"
    printf '  experimental-features = nix-command flakes\n  sandbox = false\n'
  elif [ ! -e "$HOME/.config/nix/nix.conf" ]; then
    printf 'experimental-features = nix-command flakes\nsandbox = false\nmax-jobs = auto\ncores = 0\n' \
      > "$HOME/.config/nix/nix.conf"
    echo "wrote ~/.config/nix/nix.conf"
  fi
  # Pin nixpkgs to the exact revision the results were measured against.
  # shellcheck disable=SC1091
  . "$HOME/.nix-profile/etc/profile.d/nix.sh"
  nix-channel --add "https://github.com/NixOS/nixpkgs/archive/${NIXPKGS_REV}.tar.gz" nixpkgs
  nix-channel --update nixpkgs
  do_check
}

do_podman() {
  command -v podman >/dev/null || { echo "no podman"; exit 1; }
  podman image exists "$NIX_IMAGE" || podman pull "$NIX_IMAGE" || exit 1

  echo "probe 1 (OFFLINE): build a self-contained derivation inside $NIX_IMAGE"
  podman run --rm --security-opt seccomp=unconfined "$NIX_IMAGE" \
    nix-build --no-out-link --option substituters '' -E \
    'derivation { name="bootstrap-probe"; system="x86_64-linux"; builder="/bin/sh"; args=["-c" "echo ok > $out"]; }'
  echo "  rc=$?  (a store path on stdout means the container path builds)"

  echo
  echo "probe 2 (ONLINE): can the container substitute from cache.nixos.org?"
  podman run --rm --network=host --security-opt seccomp=unconfined \
    -e http_proxy="$http_proxy" -e https_proxy="$https_proxy" \
    -e HTTP_PROXY="$http_proxy" -e HTTPS_PROXY="$https_proxy" \
    "$NIX_IMAGE" sh -c 'nix-channel --update 2>&1 | tail -2'
  echo "  MEASURED 2026-08-06: this FAILS (curl error 56, 'Failure when receiving"
  echo "  data from the peer') even with --network=host and the proxy env set."
  echo "  The image's nix 2.3.16 curl does not negotiate the fwdproxy CONNECT"
  echo "  tunnel. So the container path can PROVISION a store and build"
  echo "  self-contained derivations, but cannot fetch nixpkgs inputs. Use the"
  echo "  host path for anything needing <nixpkgs>, or fix egress in the image"
  echo "  (newer nixos/nix tag, or seed the store from a host-exported closure)."
  echo
  echo "To run hermit INSIDE the container, follow"
  echo "  experiments/nix-hermit-container-approach_20260730/recipe/"
  echo "  (hostlibs loader + --security-opt seccomp=unconfined)."
}

case "${1:-check}" in
  check) do_check ;;
  host) do_host ;;
  podman) do_podman ;;
  *) echo "usage: $0 [check|host|podman]"; exit 64 ;;
esac
