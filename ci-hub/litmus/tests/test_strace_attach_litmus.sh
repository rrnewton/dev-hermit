#!/usr/bin/env bash
# Regression tests for strace_attach_litmus.sh's count/classify path.
#
# WHY THIS EXISTS. `grep -c` PRINTS "0" *and* EXITS 1 when there are no matches,
# so the idiom `n=$(grep -c ... || echo 0)` captures BOTH and yields the two-line
# string "0\n0". That produced two distinct faults, and only the first is
# visible:
#
#   1. an arithmetic syntax error on stderr from `(( n > 0 ))`, and
#   2. a bare "0" line injected into this instrument's key=value STDOUT, so a
#      parser reading the output saw an orphan line between fields.
#
# The second is the dangerous one: it corrupts the machine-readable record
# silently, while the verdict still prints and still reads correct.
#
# THE BUG ONLY APPEARS ON A ZERO-MATCH RUN, which is why it survived: every
# contended run has >=1 match, so `grep -c` exits 0 and `|| echo 0` never fires.
# It took a KVM run with 546 trace lines and ZERO establishing ptrace calls to
# expose it. The zero-match fixture below is therefore the load-bearing case,
# not an edge case.
#
# These tests bind to the REAL functions by extracting them from the script
# under test rather than restating the logic, so a future rewrite of
# count_matches cannot leave the tests passing against a copy.
#
# Both directions are covered: run against pre-fix 0b8b8c43 the zero-match case
# emits 80 bytes of stderr and 2 orphan numeric lines and these assertions FAIL,
# which is what makes a pass here mean something.

set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
UNDER_TEST=${UNDER_TEST:-$SCRIPT_DIR/../strace_attach_litmus.sh}
WORK=$(mktemp -d)
trap 'chmod -R u+rwX "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

failures=0
checks=0

# Run the real classify_wrap against a synthetic log, capturing stdout and
# stderr to FILES. Capturing to files is deliberate: a KVM/container run was
# observed to hide post-header stdout when the descriptors were consumed
# inline, which made a complete record look truncated.
drive() {
    local log=$1 rc=$2 out=$3 err=$4
    BACKEND=${BACKEND:-kvm} log=$log bash -c '
        set -uo pipefail
        eval "$(awk "
            /^ESTABLISHING=/                 {print}
            /^verdict\(\) \{/,/^\}/          {print}
            /^count_matches\(\) \{/,/^\}/    {print}
            /^classify_wrap\(\) \{/,/^\}/    {print}
        " "$1")"
        classify_wrap "$2"
    ' _ "$UNDER_TEST" "$rc" >"$out" 2>"$err"
}

check() {
    local label=$1 expected=$2 actual=$3
    checks=$((checks + 1))
    if [[ $expected == "$actual" ]]; then
        printf '  ok   %-58s %s\n' "$label" "$actual"
    else
        printf '  FAIL %-58s got %-14s want %s\n' "$label" "$actual" "$expected"
        failures=$((failures + 1))
    fi
}

field() { sed -n "s/^$2=//p" "$1" | tr '\n' '|'; }
# A bare numeric line is the stdout-corruption signature: a count that spilled a
# second line into the key=value stream.
orphans() { grep -cxE '[0-9]+' "$1"; }

# --- 1. ZERO establishing calls, hermit rc=0. The case that exposed the bug. ---
{
    printf 'ptrace(PTRACE_PEEKDATA, 123, 0x10, 0) = 0\n'
    for _ in $(seq 1 545); do printf 'read(3, "x", 1) = 1\n'; done
} >"$WORK/zero.log"
drive "$WORK/zero.log" 0 "$WORK/zero.out" "$WORK/zero.err"
check "zero-match: verdict"              "ATTACHED|" "$(field "$WORK/zero.out" VERDICT)"
check "zero-match: calls is ONE scalar"  "0|"       "$(field "$WORK/zero.out" PTRACE_ESTABLISHING_CALLS)"
check "zero-match: eperm is ONE scalar"  "0|"       "$(field "$WORK/zero.out" PTRACE_ESTABLISHING_EPERM)"
check "zero-match: stderr is empty"      "0"        "$(wc -c <"$WORK/zero.err")"
check "zero-match: no orphan stdout line" "0"       "$(orphans "$WORK/zero.out")"
check "zero-match: denominator reported" "546|"     "$(field "$WORK/zero.out" STRACE_LOG_LINES)"

# --- 2. An establishing op refused with EPERM is the informative result. ---
printf 'ptrace(PTRACE_TRACEME) = -1 EPERM (Operation not permitted)\n' >"$WORK/eperm.log"
drive "$WORK/eperm.log" 1 "$WORK/eperm.out" "$WORK/eperm.err"
check "traceme-eperm: verdict"    "REFUSED-ALREADY-TRACED|" "$(field "$WORK/eperm.out" VERDICT)"
check "traceme-eperm: eperm count" "1|"                    "$(field "$WORK/eperm.out" PTRACE_ESTABLISHING_EPERM)"
check "traceme-eperm: stderr empty" "0"                    "$(wc -c <"$WORK/eperm.err")"

# --- 3. An empty log measured nothing. It must NOT report a count of 0. ---
: >"$WORK/empty.log"
drive "$WORK/empty.log" 0 "$WORK/empty.out" "$WORK/empty.err"
check "empty-log: verdict"            "ERROR|" "$(field "$WORK/empty.out" VERDICT)"
check "empty-log: calls are n/a not 0" "n/a|" "$(field "$WORK/empty.out" PTRACE_ESTABLISHING_CALLS)"
check "empty-log: eperm are n/a not 0" "n/a|" "$(field "$WORK/empty.out" PTRACE_ESTABLISHING_EPERM)"
check "empty-log: no orphan stdout line" "0"  "$(orphans "$WORK/empty.out")"

# --- 4. A log that cannot be READ is a setup failure, not a measured zero. ---
# This is the half a naive fix erases: collapsing rc>1 into 0 would turn an
# unreadable log into a clean ATTACHED.
printf 'ptrace(PTRACE_ATTACH, 9, 0, 0) = 0\n' >"$WORK/locked.log"
chmod 000 "$WORK/locked.log"
if [[ -r $WORK/locked.log ]]; then
    printf '  skip unreadable-log check (running as root; chmod 000 still readable)\n'
else
    drive "$WORK/locked.log" 0 "$WORK/locked.out" "$WORK/locked.err"
    check "unreadable-log: verdict is ERROR not ATTACHED" "ERROR|" \
        "$(field "$WORK/locked.out" VERDICT)"
    check "unreadable-log: emits no count at all" "" \
        "$(field "$WORK/locked.out" PTRACE_ESTABLISHING_CALLS)"
fi
chmod 644 "$WORK/locked.log"

printf '%s: %d/%d checks passed\n' "$(basename "$0")" "$((checks - failures))" "$checks"
((failures == 0)) || exit 1
