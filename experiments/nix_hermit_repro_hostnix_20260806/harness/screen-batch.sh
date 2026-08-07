#!/usr/bin/env bash
# screen-batch.sh — two-step triage of real nixpkgs packages.
#
#   step 1  native  canonical-rebuild x N   -> is it nondeterministic ON THIS HOST?
#   step 2  hermit  canonical-rebuild x N   -> does the exec-builder wrap fix it?
#
# Step 2 runs only for packages that failed step 1: a package that is already
# on-machine reproducible is a NEGATIVE CONTROL, not a hermit target (the
# nftables-1.1.6 lesson). Both verdicts are recorded.
#
# Input: a TSV of `label<TAB>nix-expr` (comments with `#` allowed).
# Usage: screen-batch.sh <candidates.tsv> [N] [PARALLEL]
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"

list="${1:?candidates.tsv}"; N="${2:-3}"; par="${3:-4}"
out="$EXP_DIR/results.csv"
[ -f "$out" ] || echo "label,target_expr,mode,dose,drv,n,distinct,verdict,hashes,wall_s,notes" > "$out"

screen_one() {
  local label="$1" expr="$2"
  local row nat_verdict
  row=$(bash "$here/canonical-nrep.sh" "$label" native "$expr" "$N" 2>>"$LOG_DIR/$label.screen.log")
  echo "$row" >> "$out"; echo "NATIVE  $row"
  nat_verdict=$(echo "$row" | cut -d, -f8)
  # ALWAYS_HERMIT=1 also runs the wrapped arm for already-reproducible packages.
  # That measures SEAM BUILDABILITY (does a real package build at all under the
  # wrap?) independently of whether hermit had any nondeterminism to fix.
  if [ "$nat_verdict" = "NONDETERMINISTIC" ] || [ "${ALWAYS_HERMIT:-0}" = "1" ]; then
    row=$(bash "$here/canonical-nrep.sh" "$label" hermit "$expr" "$N" 2>>"$LOG_DIR/$label.screen.log")
    echo "$row" >> "$out"; echo "HERMIT  $row"
  fi
}
export -f screen_one
export here N out LOG_DIR ALWAYS_HERMIT

grep -vE '^\s*(#|$)' "$list" \
  | while IFS=$'\t' read -r label expr; do printf '%s\t%s\n' "$label" "$expr"; done \
  | xargs -P "$par" -I{} -d'\n' bash -c 'IFS=$'"'"'\t'"'"' read -r l e <<< "{}"; screen_one "$l" "$e"'
