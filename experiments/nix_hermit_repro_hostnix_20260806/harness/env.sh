#!/usr/bin/env bash
# env.sh — single source of environment for every harness script here.
#
# Sourced (not executed) by the other scripts. Everything that a second
# engineer must reproduce lives here: the nix profile, the fwdproxy egress
# settings (cache.nixos.org IS reachable from this host through fwdproxy, so
# build *inputs* may be substituted), the hermit binary under test, and the
# determinism-relevant nix options.
#
# Override any of these from the caller's environment.

# --- nix ---------------------------------------------------------------------
if [ -r "$HOME/.nix-profile/etc/profile.d/nix.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nix-profile/etc/profile.d/nix.sh"
fi
export PATH="$HOME/.nix-profile/bin:$PATH"

# --- fwdproxy egress (needed to substitute build inputs) ---------------------
export http_proxy="${http_proxy:-http://fwdproxy:8080}"
export https_proxy="${https_proxy:-http://fwdproxy:8080}"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export no_proxy="${no_proxy:-.facebook.com,.internalfb.com,.tfbnw.net,.fbcdn.net,localhost,127.0.0.1,::1}"
export NO_PROXY="$no_proxy"

# --- hermit under test -------------------------------------------------------
# Absolute HOST path (not a store path): the guest shares the host filesystem,
# so the builder can exec it and still write the real /nix/store output path.
: "${HERMIT:=/home/newton/work/dev-hermit/worktrees/nix-repro176/hermit/target/release/hermit}"
export HERMIT
# DOSE. Two independent choices:
#
#  (a) NAMESPACE MODE. `--tmp=/tmp` (full namespace, host /tmp) — NOT
#      `--no-namespace`. The 20260729 prototype's claim that the default mode
#      "discards writes to $out" was refuted on 2026-08-06
#      (experiments/rb_no_namespace_random_leaks_20260806, parent 76117cd9):
#      hermit's private mount namespace only replaces /tmp with a private
#      tmpfs; writes elsewhere always persisted. With `sandbox = false` nix
#      builds in /tmp/nix-build-*, so it was the BUILD DIRECTORY that vanished.
#      `--tmp=/tmp` keeps the full namespace (real user namespace, uid_map
#      `0 <uid> 1`) while letting nix's build directory live on host /tmp.
#      Verified here: writes to /nix/store persist; `chown 0:0` SUCCEEDS
#      (it fails under `--no-namespace`, which reports uid 0 without the
#      privilege to back it); getpid and bash $RANDOM are deterministic.
#      `setarch -R` is therefore also unnecessary: the full namespace pins ASLR.
#
#  (b) CLOCK. `--no-rcb-time` is REQUIRED on this host: the AMD PMU is unusable
#      by hermit (`PMU validation failed ... AmdSpecLockMapShouldBeDisabled`)
#      and hermit's default logical clock advances with PMU-read RCB counts,
#      which jitter at nanosecond resolution. `--max-timeslice disabled`
#      additionally removes PMU-backed preemption (~3x faster here). This
#      supersedes the plain-`--no-namespace` minimum dose of
#      experiments/rb_nix_minimum_hermit_dose_20260730, measured on a host
#      whose PMU worked.
: "${HERMIT_ARGS:=run --tmp=/tmp --no-rcb-time --max-timeslice disabled}"
export HERMIT_ARGS
# setarch -R was only needed because `--no-namespace` cannot pin ASLR.
: "${HERMIT_USE_SETARCH:=false}"
export HERMIT_USE_SETARCH

# --- determinism-relevant nix options ---------------------------------------
# -j1 --cores 1: hermit determinizes by sequentializing, and a serial build is
# both the fastest hermit dose (see rb_nix_minimum_hermit_dose_20260730) and the
# fairest native control.
NIX_SERIAL_OPTS=(--option max-jobs 1 --option cores 1)
export NIX_SERIAL_OPTS
# Disable substitution of the TARGET so it is genuinely rebuilt locally.
NIX_NOSUB_OPTS=(--option substitute false --option substituters "")
export NIX_NOSUB_OPTS

# --- experiment layout -------------------------------------------------------
EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export EXP_DIR
export LOG_DIR="$EXP_DIR/logs"
mkdir -p "$LOG_DIR"

# --- guards ------------------------------------------------------------------
# Stop building if the filesystem holding /nix drops below this many GiB free.
: "${MIN_FREE_GIB:=60}"
export MIN_FREE_GIB

free_gib() { df -BG --output=avail /nix | tail -1 | tr -dc '0-9'; }
check_disk() {
  local avail; avail="$(free_gib)"
  if [ "$avail" -lt "$MIN_FREE_GIB" ]; then
    echo "DISK GUARD: only ${avail}GiB free on /nix (< ${MIN_FREE_GIB}); refusing to build." >&2
    return 1
  fi
  return 0
}
export -f free_gib check_disk 2>/dev/null || true

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
