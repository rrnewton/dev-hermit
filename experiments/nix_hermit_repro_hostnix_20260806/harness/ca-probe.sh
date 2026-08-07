#!/usr/bin/env bash
# ca-probe.sh — assess whether nix 2.30's experimental `ca-derivations` can act
# as the reproducibility ORACLE for hermit-determinized builds.
#
# Two questions:
#   Q1  Does `--check` detect a deliberately nondeterministic derivation in
#       INPUT-ADDRESSED mode?  (expected: yes, exit 104)
#   Q2  Does `--check` detect the SAME nondeterminism in CONTENT-ADDRESSED mode?
#       (NixOS/nix#5336 says no; verify on 2.30.2)
#   Q3  Does the hermit wrap make the CA derivation land on ONE store path
#       across rebuilds? (the property CA would buy us IF it were checkable)
#
# Nothing here edits the shared ~/.config/nix/nix.conf; `ca-derivations` is
# enabled per-invocation with --extra-experimental-features.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"

CA=(--extra-experimental-features "ca-derivations nix-command")
say() { printf '\n### %s\n' "$*"; }

ia_expr="(import $EXP_DIR/nix/ca-nondet.nix) { contentAddressed = false; }"
ca_expr="(import $EXP_DIR/nix/ca-nondet.nix) { contentAddressed = true; }"
ca_hermit_expr="(import $EXP_DIR/nix/hermit-wrap.nix { hermit = \"$HERMIT\"; hermitArgs = [ \"run\" \"--tmp=/tmp\" \"--no-rcb-time\" \"--max-timeslice\" \"disabled\" ]; useSetarch = false; }).hermitize ($ca_expr)"

say "Q0 nix version / features"
nix --version
nix "${CA[@]}" config show experimental-features 2>/dev/null || true

say "Q1 input-addressed --check on a date-nondeterministic derivation"
d=$(nix-instantiate -E "$ia_expr" 2>/dev/null | tail -1); echo "drv=$d"
nix-store --realise "${NIX_SERIAL_OPTS[@]}" "$d" >/dev/null 2>&1
nix-store --realise --check "${NIX_SERIAL_OPTS[@]}" "$d" >/dev/null 2>&1
echo "IA --check exit=$?   (104 == nix detected differing output)"

say "Q2 content-addressed --check on the same nondeterminism"
dca=$(nix-instantiate "${CA[@]}" -E "$ca_expr" 2>&1 | tail -1); echo "drv=$dca"
if [ ! -e "$dca" ]; then echo "CA INSTANTIATE FAILED (see above)"; else
  nix-store "${CA[@]}" --realise "${NIX_SERIAL_OPTS[@]}" "$dca" >/tmp/ca.b1 2>/dev/null
  echo "build#1 -> $(cat /tmp/ca.b1)"; cat "$(cat /tmp/ca.b1)/result.txt" 2>/dev/null
  nix-store "${CA[@]}" --realise --check "${NIX_SERIAL_OPTS[@]}" "$dca" >/dev/null 2>&1
  echo "CA --check exit=$?   (104 == detected; 0 == NOT detected, i.e. nix#5336)"
fi

say "Q3 CA + hermit: do repeated builds land on ONE content-addressed path?"
dch=$(nix-instantiate "${CA[@]}" -E "$ca_hermit_expr" 2>&1 | tail -1); echo "drv=$dch"
if [ -e "$dch" ]; then
  for i in 1 2 3; do
    p=$(nix-store "${CA[@]}" --realise "${NIX_SERIAL_OPTS[@]}" "${NIX_NOSUB_OPTS[@]}" "$dch" 2>/dev/null | tail -1)
    echo "  build#$i -> $p  ($(cat "$p/result.txt" 2>/dev/null))"
    # Drop the realisation+output so the next build is a genuine rebuild.
    nix-store --delete "$p" >/dev/null 2>&1
  done
fi
