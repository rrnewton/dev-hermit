#!/usr/bin/env bash
# rebuild-compare.sh — rebuild a Nix derivation and detect output nondeterminism.
#
# Uses Nix's own reproducibility oracle: realize the derivation, then
# `nix-store --realise --check --keep-failed`, which rebuilds and byte-compares
# against the already-registered output. Distinct-output exit status is 104
# (see ai_docs/nix-reprobuild-ca-store-research_20260729.md). On a mismatch Nix
# retains `<out>.check`; we NAR-hash both outputs as evidence.
#
# Usage:
#   rebuild-compare.sh <label> <nix-expr>
# where <nix-expr> evaluates (with `nix-instantiate -E`) to one derivation.
#
# Emits one CSV row on stdout (label,drv,out_hash,check_hash,check_exit,verdict)
# and a human summary on stderr.
set -uo pipefail

label="${1:?label}"
expr="${2:?nix expression}"

log() { printf '[%s] %s\n' "$label" "$*" >&2; }

narhash() { # NAR (sha256, base32) of a store path — Nix's content identity
  nix-hash --type sha256 --base32 "$1" 2>/dev/null || echo "MISSING"
}

drv=$(nix-instantiate -E "$expr" 2>/tmp/${label}.inst.err | tail -1)
if [[ -z "$drv" || ! -e "$drv" ]]; then
  log "instantiate FAILED"; sed 's/^/    /' /tmp/${label}.inst.err >&2
  echo "$label,INSTANTIATE_FAIL,,,,error"; exit 2
fi
log "drv=$drv"

# First realization (build if not cached).
out=$(nix-store --realise "$drv" 2>/tmp/${label}.build1.err | tail -1)
if [[ -z "$out" || ! -e "$out" ]]; then
  log "build#1 FAILED"; tail -20 /tmp/${label}.build1.err | sed 's/^/    /' >&2
  echo "$label,$drv,BUILD_FAIL,,,error"; exit 2
fi
h1=$(narhash "$out")
log "build#1 out=$out narhash=$h1"

# Rebuild with --check: rebuilds and compares to the registered output.
nix-store --realise --check --keep-failed "$drv" >/tmp/${label}.check.out 2>/tmp/${label}.check.err
rc=$?
log "check exit=$rc"

check_path="${out}.check"
if [[ -e "$check_path" ]]; then
  h2=$(narhash "$check_path")
  log "check output retained: $check_path narhash=$h2"
else
  # exit 0 => reproduced; the rebuilt output equals $out (no .check kept)
  h2="$h1"
fi

if [[ "$rc" -eq 104 || "$h1" != "$h2" ]]; then
  verdict="NONDETERMINISTIC"
elif [[ "$rc" -eq 0 ]]; then
  verdict="reproducible"
else
  verdict="build-error(rc=$rc)"
  tail -20 /tmp/${label}.check.err | sed 's/^/    /' >&2
fi
log "VERDICT: $verdict (h1=$h1 h2=$h2)"
echo "$label,$drv,$h1,$h2,$rc,$verdict"
