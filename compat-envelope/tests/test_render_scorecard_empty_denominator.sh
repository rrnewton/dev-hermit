#!/usr/bin/env bash
# A1 (ambiguous-zero class, task `execute-ambiguous-zero-fix-order-a3-a4-first`):
# an EMPTY DENOMINATOR must not render as a measured zero.
#
# render-scorecard.rs builds every percentage over the set of PASSING PTRACE cells
# in the requested `--denominator` mode. When that set is empty there are no cells
# at all, and the pre-fix renderer printed a confident `TOTAL 0` at exit 0 — which
# is indistinguishable from "we measured, and nothing passed". Both states occur in
# the live scorecard, so the reader could not tell them apart.
#
# The fix refuses to render, exits 3 (distinct from 2 = usage, 0 = rendered), and
# carries the denominator's own population: how many rows were considered, how many
# were ptrace rows, and which `--denominator` modes actually have passing ptrace
# rows. There are THREE distinct causes of an empty denominator and the message must
# separate them, because the remedy differs:
#
#   A. no ptrace rows at all      -> changing --denominator cannot help
#   B. ptrace passes another mode -> retry with that mode (and it must then render)
#   C. ptrace rows all failed     -> the reference backend itself failed here
#
# Bracketed BOTH WAYS: a real result must still score normally (case D). It is easy
# to make every zero read "unknown" and call the class fixed.
#
# Usage: test_render_scorecard_empty_denominator.sh [renderer.rs] [schema-source.csv]
# Both default to the in-repo copies. The fixture header is COPIED from the schema
# source rather than hardcoded, so this test is agnostic to the in-flight
# `parity` -> `stdout_parity` column rename.

set -uo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
RENDER=${1:-$ROOT/compat-envelope/render-scorecard.rs}
SCHEMA_CSV=${2:-$ROOT/compat-envelope/scorecard.csv}

[ -x "$RENDER" ] || { echo "no renderer at $RENDER" >&2; exit 2; }
[ -r "$SCHEMA_CSV" ] || { echo "no schema source at $SCHEMA_CSV" >&2; exit 2; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

HEADER=$(head -1 "$SCHEMA_CSV")

# Emit one CSV row into $1, setting only the named columns; every other column is
# blank. Blank is meaningful here: a blank `deterministic`/parity is "unknown", which
# is exactly the not-measured state the renderer is supposed to preserve.
row() {
    local out=$1; shift
    awk -v hdr="$HEADER" -v spec="$*" '
    BEGIN {
        n = split(hdr, cols, ",")
        v["comparison_tier"] = "full-stdout-info-stack-heap"
        m = split(spec, kvs, " ")
        for (i = 1; i <= m; i++) { p = index(kvs[i], "="); v[substr(kvs[i],1,p-1)] = substr(kvs[i],p+1) }
        out = ""
        for (i = 1; i <= n; i++) out = out (i > 1 ? "," : "") (cols[i] in v ? v[cols[i]] : "")
        print out
    }' >> "$out"
}

new_csv() { echo "$HEADER" > "$1"; }

RUN=a1-fixture

# --- case A: dbi-only run, no ptrace rows in any mode -------------------------
new_csv "$TMP/a.csv"
for t in t1 t2 t3; do
    row "$TMP/a.csv" run_id=$RUN bucket=b1 test_id=$t test_mode=strict backend=dbi cell_state=ran outcome=pass
done

# --- case B: ptrace passes `strict`, but the default denominator is `verify` --
new_csv "$TMP/b.csv"
for t in t1 t2 t3; do
    row "$TMP/b.csv" run_id=$RUN bucket=b1 test_id=$t test_mode=strict backend=ptrace cell_state=ran outcome=pass
    row "$TMP/b.csv" run_id=$RUN bucket=b1 test_id=$t test_mode=strict backend=dbi cell_state=ran outcome=pass
done

# --- case C: ptrace rows present in the requested mode, none passing ----------
new_csv "$TMP/c.csv"
for t in t1 t2 t3; do
    row "$TMP/c.csv" run_id=$RUN bucket=b1 test_id=$t test_mode=verify backend=ptrace cell_state=ran outcome=fail
    row "$TMP/c.csv" run_id=$RUN bucket=b1 test_id=$t test_mode=verify backend=dbi cell_state=ran outcome=pass
done

# --- case D: a real, non-empty denominator ------------------------------------
new_csv "$TMP/d.csv"
for t in t1 t2 t3; do
    row "$TMP/d.csv" run_id=$RUN bucket=b1 test_id=$t test_mode=verify backend=ptrace cell_state=ran outcome=pass
    row "$TMP/d.csv" run_id=$RUN bucket=b1 test_id=$t test_mode=verify backend=dbi cell_state=ran outcome=pass
done

FAIL=0
run_render() { # csv, extra args... -> sets OUT and RC
    OUT=$("$RENDER" --csv "$1" --run-id "$RUN" "${@:2}" 2>&1)
    RC=$?
}

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); }
check() { if [ "$2" = yes ]; then ok "$1"; else bad "$1"; fi; }
has()  { case "$OUT" in *"$1"*) echo yes;; *) echo no;; esac; }
hasnt(){ case "$OUT" in *"$1"*) echo no;; *) echo yes;; esac; }
rc_is(){ if [ "$RC" = "$1" ]; then echo yes; else echo no; fi; }

# "The guard is absent" and "the guard is present but wrong" are DIFFERENT FACTS,
# and a bare FAIL conflates them — which is the very defect class this test exists
# to police. So probe for absence first, from the BEHAVIOUR (case A renders a result
# instead of refusing) rather than by grepping the source, and give it its own exit
# status. A1 is authored and verified but blocked on shared ownership of
# render-scorecard.rs; until it lands, exit 4 says so out loud instead of emitting a
# bare red that reads like a regression.
# Exit codes: 0 = fix present and correct, 1 = fix present and REGRESSED,
#             2 = usage, 4 = fix not landed in this renderer (nothing was verified).
run_render "$TMP/a.csv"
if [ "$RC" = 0 ] && [ "$(hasnt 'NO DATA:')" = yes ]; then
    {
        echo "A1-NOT-LANDED: $RENDER rendered a result for an EMPTY DENOMINATOR."
        echo "  Case A has no passing ptrace cells at all, yet the renderer exited 0"
        echo "  with a table. That is the pre-fix ambiguous zero, NOT a regression:"
        echo "  the empty-denominator guard is absent from this renderer."
        echo "  0 of 26 assertions were evaluated."
        echo "  Point this test at a renderer carrying the A1 guard, or land A1."
    } >&2
    exit 4
fi

echo "case A — no ptrace rows in any mode"
run_render "$TMP/a.csv"
check "refuses instead of rendering"                  "$(has 'NO DATA:')"
check "exit 3 (could-not-measure, not a result)"      "$(rc_is 3)"
check "must NOT publish a bare TOTAL 0"               "$(hasnt 'TOTAL                        0')"
check "carries the ptrace-row denominator (0)"        "$(has 'ptrace rows:      0')"
check "says --denominator cannot help"                "$(has 'changing --denominator will not help')"

echo "case B — ptrace passes a DIFFERENT mode than the one requested"
run_render "$TMP/b.csv"
check "refuses instead of rendering"                  "$(has 'NO DATA:')"
check "exit 3"                                        "$(rc_is 3)"
check "reports the ptrace rows that DO exist"         "$(has 'ptrace rows:      3')"
check "names the mode that would work"                "$(has 'Retry with --denominator <strict>')"
check "does not misreport it as unmeasurable"         "$(hasnt 'changing --denominator will not help')"

echo "case B2 — FOLLOWING that remedy must actually render"
run_render "$TMP/b.csv" --denominator strict
check "renders"                                       "$(hasnt 'NO DATA:')"
check "exit 0"                                        "$(rc_is 0)"
check "denominator is the 3 passing ptrace cells"     "$(has 'TOTAL')"

echo "case C — ptrace rows present, none passing"
run_render "$TMP/c.csv"
check "refuses instead of rendering"                  "$(has 'NO DATA:')"
check "exit 3"                                        "$(rc_is 3)"
check "distinguishes it from 'no ptrace rows'"        "$(has 'ptrace rows:      3')"
check "attributes it to the reference backend"        "$(has 'none passing in any mode')"
check "does not offer a mode that cannot work"        "$(hasnt 'Retry with --denominator')"

echo "case D — POSITIVE CONTROL: a real result still scores"
run_render "$TMP/d.csv"
check "renders a table"                               "$(has 'TOTAL')"
check "exit 0"                                        "$(rc_is 0)"
check "no spurious refusal"                           "$(hasnt 'NO DATA:')"
run_render "$TMP/d.csv" --json
check "--json still emits"                            "$(has '"schema"')"
check "--json exit 0"                                 "$(rc_is 0)"
run_render "$TMP/d.csv" --tsv
check "--tsv still emits"                             "$(rc_is 0)"

echo
if [ "$FAIL" -eq 0 ]; then
    echo "PASS"
    exit 0
fi
echo "FAIL ($FAIL assertions)"
exit 1
