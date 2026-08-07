#!/usr/bin/env bash
# Regenerate every table that appears in SCORECARD-CURRENT.md, in the same
# order, from the four tracked CSVs in this directory.
#
#   compat-envelope/render-current-scorecard.sh            # human tables
#   compat-envelope/render-current-scorecard.sh --tsv      # machine projection
#
# This script exists so the published rendering is reproducible: run it, diff
# its output against the fenced blocks in SCORECARD-CURRENT.md, and any drift
# means the doc is stale relative to the CSVs beside it.
#
# It renders ONLY. It never runs a guest, never touches a backend, and never
# writes a CSV — the CSVs are produced by the collectors (see README.md).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
render="$here/render-scorecard.rs"
fmt=()
[ "${1:-}" = "--tsv" ] && fmt=(--tsv)
[ "${1:-}" = "--json" ] && fmt=(--json)

banner() { printf '\n===== %s =====\n\n' "$1"; }

banner "PROVENANCE (input CSV identity — quote these, not a branch name)"
for f in fullcorpus-scorecard scorecard reverie-scorecard e9patch-scorecard; do
  p="$here/$f.csv"
  printf '%-26s blob=%s rows=%s\n' \
    "$f.csv" "$(git -C "$here" hash-object "$p")" "$(( $(wc -l < "$p") - 1 ))"
done

banner "STRICT COMPARISON TIER — per-cell standard and exact distribution"
python3 "$here/check-scorecard-tier.py" --root "$here"

banner "TABLE 1 — full corpus (definition-of-done denominator)"
"$render" --csv "$here/fullcorpus-scorecard.csv" --observable stdout --all "${fmt[@]}"

banner "TABLE 2 — regression / CI envelope"
"$render" --csv "$here/scorecard.csv" --observable stdout --all "${fmt[@]}"

banner "TABLE 3 — Reverie B1.5 Guest/Tool boundary (tool-count observable)"
"$render" --csv "$here/reverie-scorecard.csv" --denominator counter \
  --backends kvm --observable tool-count --all "${fmt[@]}"

banner "TABLE 4 — e9patch preprocessing-invariance over ptrace (NOT a backend)"
"$render" --csv "$here/e9patch-scorecard.csv" --backends e9patch \
  --observable stdout --all "${fmt[@]}"

banner "CERTIFICATION TIER — which comparator produced every 'verified' cell"
python3 - "$here" <<'PY'
import csv, collections, os, sys
here = sys.argv[1]
for name in ("fullcorpus-scorecard", "scorecard", "reverie-scorecard", "e9patch-scorecard"):
    path = os.path.join(here, name + ".csv")
    rows = list(csv.DictReader(open(path)))
    if not rows or "verify_compare" not in rows[0]:
        print(f"{name+'.csv':26s} verify_compare COLUMN ABSENT "
              f"({len(rows)} rows record no comparator at all)")
        continue
    counts = collections.Counter(r["verify_compare"] or "(blank)" for r in rows)
    print(f"{name+'.csv':26s} verify_compare {dict(counts)}")
print()
print("Any count under a key other than 'bitwise' is NOT a bitwise certification.")
PY
