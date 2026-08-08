#!/usr/bin/env bash
# Validate a candidate guest AS AN INSTRUMENT, before any backend touches it.
#
# GATE (all must hold, natively):
#   G1 self-check   : exit code 0
#   G2 count        : exactly EXPECT syscalls of the counted kind
#   G3 env-invariant: same count with the guest's stdout a PIPE and a FILE
#   G4 under stress : G1+G2 hold across N concurrent runs
# Any failure REJECTS THE GUEST. It says nothing about any backend.
#
# COUNTING CAVEAT, stated because it bounds the claim: strace is PTRACE-based,
# and ptrace is itself a backend we intend to compare later. So the count is
# CORROBORATION, not the golden reference. The golden signal is G1/G4's exit
# code, which the guest produces itself and which needs no observer at all.
set -u
GUEST=${1:?usage: validate_guest.sh <guest> <counted-syscall> <expect> [parallel]}
SYS=${2:?}; EXPECT=${3:?}; N=${4:-20}
fail=0
note() { printf '  %-54s %s\n' "$1" "$2"; }

# $1 = where the GUEST's stdout goes. Returns the count on our stdout.
count_of() {
    # strace's summary needs its OWN sink: -o /dev/stdout collides with the
    # guest's stdout redirect below and silently yields 0. Caught by this
    # harness rejecting a known-good guest, which is why the gate is exercised
    # against a KNOWN answer before it is trusted against an unknown one.
    local sum; sum=$(mktemp)
    strace -c -f -o "$sum" "$GUEST" >"$1" 2>/dev/null
    awk -v s="$SYS" '$NF==s {c=$(NF-1)} END{print c+0}' "$sum"
    rm -f "$sum"
}

echo "G1/G2 single run"
"$GUEST" >/dev/null 2>&1; rc=$?
c=$(count_of /dev/null)
if [ "$rc" -eq 0 ]; then note "G1 exit code" "0 OK"; else note "G1 exit code" "$rc REJECT"; fail=1; fi
if [ "$c" = "$EXPECT" ]; then note "G2 $SYS count" "$c OK"; else note "G2 $SYS count" "$c (want $EXPECT) REJECT"; fail=1; fi

echo "G3 environment invariance (pipe vs file)"
cp=$(count_of /dev/null); tf=$(mktemp); cf=$(count_of "$tf"); rm -f "$tf"
if [ "$cp" = "$EXPECT" ] && [ "$cf" = "$EXPECT" ]; then note "G3 pipe=$cp file=$cf" "OK"
else note "G3 pipe=$cp file=$cf (want $EXPECT)" "REJECT"; fail=1; fi

echo "G4 stress: $N concurrent"
rcs=$(mktemp)
for _ in $(seq 1 "$N"); do ( "$GUEST" >/dev/null 2>&1; echo $? >>"$rcs" ) & done; wait
tot=$(wc -l <"$rcs"); bad=$(grep -cv '^0$' "$rcs" || true); bad=${bad:-0}; rm -f "$rcs"
if [ "$bad" -eq 0 ]; then note "G4 exit codes ($tot runs)" "$tot/$tot zero OK"
else note "G4 exit codes ($tot runs)" "$bad nonzero REJECT"; fail=1; fi

cs=$(mktemp)
for _ in $(seq 1 "$N"); do ( count_of /dev/null >>"$cs" ) & done; wait
ntot=$(wc -l <"$cs"); nbad=$(grep -cv "^${EXPECT}$" "$cs" || true); nbad=${nbad:-0}
obs=$(sort -u "$cs" | tr '\n' ' '); rm -f "$cs"
if [ "$nbad" -eq 0 ]; then note "G4 $SYS counts ($ntot runs)" "all $EXPECT OK"
else note "G4 $SYS counts ($ntot runs)" "$nbad deviating; observed {$obs} REJECT"; fail=1; fi

echo
if [ "$fail" -eq 0 ]; then echo "VERDICT: ACCEPTED as a yardstick"; exit 0
else echo "VERDICT: REJECTED - not deterministic natively; NOT a yardstick. This says nothing about any backend."; exit 1; fi
