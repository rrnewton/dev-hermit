#!/usr/bin/env bash
# A1-LATEST (ambiguous-zero class, task `fix-scorecard-latest-empty-denominator-total-zero`):
# the empty-denominator refusal must also hold on the RUN-SELECTION path.
#
# Companion to test_render_scorecard_empty_denominator.sh. That suite proves the
# guard itself is correct, but it drives the renderer with an explicit `--run-id`
# on single-run fixtures, so it never exercises WHICH run gets scored. `--latest`
# (which is also the DEFAULT when neither `--run-id` nor `--all` is given) picks the
# run_id of the last-appended row. A CSV whose history is healthy but whose NEWEST
# run has no ptrace reference is therefore the dangerous shape: the denominator is
# empty for the selected run while the file as a whole looks fine.
#
# That shape is not hypothetical. At dev-hermit main 6ed3002 the committed
# compat-envelope/scorecard.csv ended with run backend-parity-fc49593ac21c-1785914664-639593:
# 28 rows, every one backend=dbi test_mode=strict, ZERO ptrace rows. The pre-fix
# renderer answered `--latest` with an empty table and a confident `TOTAL 0` at
# exit 0 — NUMERATOR 0 over DENOMINATOR 0, published as if it were a measurement.
#
# Every fixture below therefore states its numerator and denominator explicitly, and
# the suite brackets in BOTH directions: the same CSV that must refuse under
# `--latest` must still render under `--run-id <older>`. `--all` must now refuse
# the two run identities instead of silently pooling their code/population state.
#
# Usage: test_render_scorecard_latest_empty_denominator.sh [renderer.rs] [schema-source.csv]
# Both default to the in-repo copies. The fixture header is COPIED from the schema
# source, so this test is agnostic to CSV column renames.

set -uo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
RENDER=${1:-$ROOT/compat-envelope/render-scorecard.rs}
SCHEMA_CSV=${2:-$ROOT/compat-envelope/scorecard.csv}

[ -x "$RENDER" ] || { echo "no renderer at $RENDER" >&2; exit 2; }
[ -r "$SCHEMA_CSV" ] || { echo "no schema source at $SCHEMA_CSV" >&2; exit 2; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

HEADER=$(head -1 "$SCHEMA_CSV")
case ",$HEADER," in *,stack_parity,*) ;; *) HEADER="$HEADER,stack_parity";; esac
case ",$HEADER," in *,heap_parity,*) ;; *) HEADER="$HEADER,heap_parity";; esac

# Emit one CSV row into $1, setting only the named columns; every other column is
# blank (blank = "unknown", the not-measured state the renderer must preserve).
row() {
    local out=$1; shift
    awk -v hdr="$HEADER" -v spec="$*" '
    BEGIN {
        n = split(hdr, cols, ",")
        v["comparison_tier"] = "full-stdout-info-stack-heap"
        v["stdout_parity"] = "pass"
        v["bitwise_parity"] = "pass"
        v["compared_log_messages"] = "9|9"
        v["stack_parity"] = "pass"
        v["heap_parity"] = "pass"
        m = split(spec, kvs, " ")
        for (i = 1; i <= m; i++) { p = index(kvs[i], "="); v[substr(kvs[i],1,p-1)] = substr(kvs[i],p+1) }
        out = ""
        for (i = 1; i <= n; i++) out = out (i > 1 ? "," : "") (cols[i] in v ? v[cols[i]] : "")
        print out
    }' >> "$out"
}
new_csv() { echo "$HEADER" > "$1"; }

OLD=older-healthy-run
NEW=newer-dbi-only-run

# --- fixture 1: healthy history, newest run has no ptrace reference -----------
# Append order IS the selection input: OLD first, NEW last, so NEW is `--latest`.
#   OLD: NUMERATOR 3 dbi-verify passes / DENOMINATOR 3 passing ptrace-verify cells
#   NEW: NUMERATOR 2 dbi-strict passes / DENOMINATOR 0 (no ptrace rows at all)
new_csv "$TMP/mixed.csv"
for t in t1 t2 t3; do
    row "$TMP/mixed.csv" run_id=$OLD bucket=b1 test_id=$t test_mode=verify backend=ptrace cell_state=ran outcome=pass
    row "$TMP/mixed.csv" run_id=$OLD bucket=b1 test_id=$t test_mode=verify backend=dbi    cell_state=ran outcome=pass
done
for t in t4 t5; do
    row "$TMP/mixed.csv" run_id=$NEW bucket=b1 test_id=$t test_mode=strict backend=dbi    cell_state=ran outcome=pass
done

# --- fixture 2: newest run HAS ptrace, but passing a different mode -----------
# This is the Reverie shape: the denominator is OMITTED, so it defaults to `verify`,
# while the run's ptrace rows pass `counter`. DENOMINATOR 0 under the default,
# DENOMINATOR 3 under the mode the remedy names.
new_csv "$TMP/othermode.csv"
for t in t1 t2 t3; do
    row "$TMP/othermode.csv" run_id=$OLD bucket=b1 test_id=$t test_mode=verify  backend=ptrace cell_state=ran outcome=pass
done
for t in t1 t2 t3; do
    row "$TMP/othermode.csv" run_id=$NEW bucket=b1 test_id=$t test_mode=counter backend=ptrace cell_state=ran outcome=pass
    row "$TMP/othermode.csv" run_id=$NEW bucket=b1 test_id=$t test_mode=counter backend=kvm    cell_state=ran outcome=pass
done

# --- fixture 3: newest run is itself healthy (positive control) ---------------
#   NEWEST: NUMERATOR 2 dbi-verify passes / DENOMINATOR 2 passing ptrace-verify cells
new_csv "$TMP/goodlatest.csv"
for t in t1 t2 t3; do
    row "$TMP/goodlatest.csv" run_id=$OLD bucket=b1 test_id=$t test_mode=verify backend=ptrace cell_state=ran outcome=pass
done
for t in t8 t9; do
    row "$TMP/goodlatest.csv" run_id=$NEW bucket=b1 test_id=$t test_mode=verify backend=ptrace cell_state=ran outcome=pass
    row "$TMP/goodlatest.csv" run_id=$NEW bucket=b1 test_id=$t test_mode=verify backend=dbi    cell_state=ran outcome=pass
done

FAIL=0
render() { OUT=$("$RENDER" --csv "$@" 2>&1); RC=$?; }
ok()    { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()   { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); }
check() { if [ "$2" = yes ]; then ok "$1"; else bad "$1"; fi; }
has()   { case "$OUT" in *"$1"*) echo yes;; *) echo no;; esac; }
hasnt() { case "$OUT" in *"$1"*) echo no;; *) echo yes;; esac; }
rc_is() { if [ "$RC" = "$1" ]; then echo yes; else echo no; fi; }

# ABSENCE PROBE, from behaviour rather than by grepping the source.
# "the guard is absent" and "the guard regressed" are different facts and a bare red
# conflates them. Exit 4 = the renderer under test has no A1 guard, so NOTHING here
# was verified; that is not a regression in the --latest selection path.
# Exit codes: 0 = present and correct, 1 = present but REGRESSED,
#             2 = usage, 4 = A1 not landed in this renderer (0 assertions evaluated).
render "$TMP/mixed.csv" --latest
if [ "$RC" = 0 ]; then
    echo "A1-LATEST-NOT-LANDED: $RENDER rendered a result for --latest on a run with"
    echo "  DENOMINATOR 0 (newest run has zero ptrace rows). That is the pre-fix"
    echo "  ambiguous zero, NOT a --latest selection regression: the empty-denominator"
    echo "  guard is absent from this renderer entirely."
    echo "  0 of 18 assertions were evaluated."
    echo "  Land the A1 guard (compat-envelope/render-scorecard.rs), then re-run."
    exit 4
fi

echo "== --latest selects the NEWEST run, and refuses when its denominator is empty =="
# numerator 0 / denominator 0 for run $NEW
check "--latest refuses (exit 3, not 0)"            "$(rc_is 3)"
check "  says NO DATA"                              "$(has 'NO DATA')"
check "  names the run it selected ($NEW)"          "$(has "$NEW")"
check "  did NOT select the older healthy run"      "$(hasnt "$OLD")"
check "  disowns the zero explicitly"               "$(has 'NOT a measured zero')"
check "  reports 0 ptrace rows"                     "$(has 'ptrace rows:      0')"
check "  names modes present (strict)"              "$(has 'modes present:    strict')"
check "  names backends present (dbi)"              "$(has 'backends present: dbi')"
check "  says --denominator cannot help (case A)"   "$(has 'changing --denominator will not help')"
check "  prints no TOTAL"                           "$(hasnt 'TOTAL')"

echo "== the SAME csv renders for the older run; --all refuses mixed runs =="
# Isolates the refusal to run selection: the data is renderable, the newest run is not.
render "$TMP/mixed.csv" --run-id "$OLD"
check "--run-id <older> renders (exit 0)"           "$(rc_is 0)"
check "  numerator/denominator: TOTAL ptrace = 3"   "$(has 'TOTAL                        3')"

render "$TMP/mixed.csv" --all
check "--all refuses mixed run identities (exit 2)" "$(rc_is 2)"
check "  refusal names mixed-run aggregation"       "$(has 'MIXED_RUN_AGGREGATE')"

echo "== denominator OMITTED, newest run passes another mode (Reverie shape) =="
render "$TMP/othermode.csv" --latest
check "refuses under the default denominator"       "$(rc_is 3)"
check "  remedy names the usable mode (counter)"    "$(has '--denominator <counter>')"
# Follow the remedy the tool printed: it MUST then render, or the advice is wrong.
render "$TMP/othermode.csv" --latest --denominator counter
check "  following that remedy renders (exit 0)"    "$(rc_is 0)"

echo "== positive control: a healthy newest run still scores =="
render "$TMP/goodlatest.csv" --latest
check "healthy --latest renders (exit 0)"           "$(rc_is 0)"
check "  numerator/denominator: TOTAL ptrace = 2"   "$(has 'TOTAL                        2')"

echo
if [ "$FAIL" -eq 0 ]; then echo "PASS"; exit 0; fi
echo "FAIL ($FAIL assertions)"
exit 1
