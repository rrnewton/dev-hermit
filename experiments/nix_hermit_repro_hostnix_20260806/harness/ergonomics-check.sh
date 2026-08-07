#!/usr/bin/env bash
# ergonomics-check.sh — prove the opt-in overlay does exactly what it claims,
# at EVALUATION level (cheap, no builds), with both a positive and a negative:
#
#   POSITIVE  a package that declares `passthru.needsHermit` gets a realBuilder
#             pointing at the hermit wrapper, and a DIFFERENT .drv than stock.
#   NEGATIVE  a package that does NOT declare it is byte-identical to stock:
#             same .drv path, so nothing else in the closure is rebuilt.
#
# A gate that only checks the positive would pass even if the overlay wrapped
# EVERYTHING; a gate that only checks the negative would pass even if it wrapped
# NOTHING. Both are required.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"
demo="$EXP_DIR/nix/hermit-overlay-demo.nix"
rc=0

drv() { nix-instantiate "$demo" --argstr hermit "$HERMIT" -A "$1" 2>/dev/null | tail -1; }

h_lensfun=$(drv hermitized.lensfun)
s_lensfun=$(drv stock.lensfun)
h_hello=$(drv untouched.hello)
s_hello=$(drv stock.hello)

echo "hermitized.lensfun = $h_lensfun"
echo "stock.lensfun      = $s_lensfun"
echo "untouched.hello    = $h_hello"
echo "stock.hello        = $s_hello"

echo
echo "POSITIVE: declared needsHermit => wrapped, different derivation"
if [ -n "$h_lensfun" ] && [ "$h_lensfun" != "$s_lensfun" ]; then echo "  PASS drv differs"; else echo "  FAIL drv identical"; rc=1; fi
builder=$(grep -ao '/nix/store/[a-z0-9]\{32\}-hermit-exec-builder' "$h_lensfun" | head -1)
if [ -n "$builder" ]; then
  echo "  PASS realBuilder = $builder"
  nix-store --realise "$builder" >/dev/null 2>&1   # it is an input path, realise to read it
  echo "  wrapper body:"; sed 's/^/    /' "$builder" 2>/dev/null || echo "    (not realised)"
else echo "  FAIL no hermit-exec-builder in the .drv"; rc=1; fi

echo
echo "NEGATIVE: no needsHermit => untouched, IDENTICAL derivation to stock"
if [ -n "$h_hello" ] && [ "$h_hello" = "$s_hello" ]; then echo "  PASS drv identical ($h_hello)"; else echo "  FAIL drv differs"; rc=1; fi
if grep -aq 'hermit-exec-builder' "$h_hello" 2>/dev/null; then echo "  FAIL hello was wrapped anyway"; rc=1; else echo "  PASS no hermit in hello's .drv"; fi

echo
echo "ESCAPE HATCH: consumer-side unconditional hermitize of a package that did not declare"
f=$(drv forced); echo "  forced.hello = $f"
if [ -n "$f" ] && [ "$f" != "$s_hello" ] && grep -aq 'hermit-exec-builder' "$f"; then echo "  PASS"; else echo "  FAIL"; rc=1; fi

echo; echo "ergonomics-check rc=$rc"; exit $rc
