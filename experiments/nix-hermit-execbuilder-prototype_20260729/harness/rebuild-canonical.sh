#!/usr/bin/env bash
# rebuild-canonical.sh — fair same-machine reproducibility oracle.
#
# `nix-store --check` builds the rebuild into a SCRATCH path, so any output that
# embeds a self-reference to its own $out differs by exactly that store-path
# hash — a false positive unrelated to runtime nondeterminism (observed for
# nftables-1.1.6: the only diff in libnftables.so was the embedded $out hash).
#
# This oracle instead builds twice into the SAME canonical $out (build, hash,
# delete from store, rebuild, hash). Self-references are then identical in both
# builds, so only genuine nondeterminism (timestamps, RNG, ordering) remains.
#
# Usage: rebuild-canonical.sh <label> '<nix-expr>'
# Emits CSV: label,drv,hash1,hash2,verdict
set -uo pipefail
label="${1:?}"; expr="${2:?}"
log(){ printf '[%s] %s\n' "$label" "$*" >&2; }
narhash(){ nix-hash --type sha256 --base32 "$1" 2>/dev/null || echo MISSING; }

drv=$(nix-instantiate -E "$expr" 2>/tmp/${label}.c.inst.err | tail -1)
[ -e "$drv" ] || { log "instantiate FAIL"; echo "$label,INST_FAIL,,,error"; exit 2; }
log "drv=$drv"

out=$(nix-store --realise "$drv" 2>/tmp/${label}.c.b1.err | tail -1)
[ -e "$out" ] || { log "build1 FAIL"; echo "$label,$drv,BUILD_FAIL,,error"; exit 2; }
h1=$(narhash "$out"); log "build#1 $out h=$h1"

# Delete the output so the next realise rebuilds into the same canonical path.
nix-store --delete "$out" >/tmp/${label}.c.del.err 2>&1 || {
  log "delete FAIL (gc roots?)"; tail -5 /tmp/${label}.c.del.err | sed 's/^/    /' >&2
  echo "$label,$drv,$h1,DELETE_FAIL,error"; exit 2; }

out2=$(nix-store --realise "$drv" 2>/tmp/${label}.c.b2.err | tail -1)
[ -e "$out2" ] || { log "build2 FAIL"; echo "$label,$drv,$h1,BUILD2_FAIL,error"; exit 2; }
h2=$(narhash "$out2"); log "build#2 $out2 h=$h2"

if [ "$h1" = "$h2" ]; then verdict="reproducible"; else verdict="NONDETERMINISTIC"; fi
log "VERDICT: $verdict (h1=$h1 h2=$h2)"
echo "$label,$drv,$h1,$h2,$verdict"
