#!/bin/bash
# e9patch preprocessing + ptrace  vs  golden plain-ptrace  corpus parity sweep.
#
# For each freestanding guest we run four hermit invocations and derive a
# per-guest verdict:
#   golden plain   : hermit run --strict                 -> exit_g, stdout_g
#   golden verify  : hermit run --strict --verify        -> L2_g (bitwise repeat)
#   e9p    plain   : hermit --backend e9patch run --strict         -> exit_e, stdout_e, patch metrics
#   e9p    verify  : hermit --backend e9patch run --strict --verify -> L2_e
#
# PASS_L2 (the honest e9patch-preprocessing compat bar) requires ALL of:
#   exit_g == exit_e, stdout_g == stdout_e, L2_g pass, L2_e pass, b0_sites == 0.
# stdout is captured from the PLAIN run because --verify diverts guest stdout
# into a temp file for DETLOG comparison. b0_sites>0 is a hard e9patch reject
# (SIGILL signal-fallback sites would change guest signal semantics).
set -uo pipefail

SRCDIR="${1:?usage: sweep.sh <srcdir> <outdir>}"
OUTDIR="${2:?usage: sweep.sh <srcdir> <outdir>}"
HB="${HB:-/home/newton/work/dev-hermit/worktrees/e9patch/hermit/target/debug/hermit}"
E9DIR="${E9DIR:-/home/newton/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch}"
export HERMIT_E9TOOL="$E9DIR/e9tool"
export HERMIT_E9PATCH_BACKEND="$E9DIR/e9patch"
WORK="$(mktemp -d /home/newton/e9scratch/sweep.XXXXXX)"
mkdir -p "$OUTDIR"
CSV="$OUTDIR/results.csv"
LOGDIR="$OUTDIR/logs"; mkdir -p "$LOGDIR"
echo "guest,exit_g,exit_e,exit_par,stdout_par,L2_g,L2_e,candidate_sites,mapped_sites,b0_sites,verdict" >"$CSV"

metric() { grep -oE "$1=[0-9]+" "$2" 2>/dev/null | head -1 | cut -d= -f2; }
l2ok()   { grep -q "Determinism verified" "$1" && echo pass || echo fail; }

for src in "$SRCDIR"/*.c; do
    g="$(basename "$src" .c)"
    bin="$WORK/$g"
    if ! cc -nostdlib -static -ffreestanding -O0 -fno-pie -no-pie "$src" -o "$bin" 2>"$LOGDIR/$g.cc.err"; then
        echo "$g,,,,,,,,,,COMPILE_FAIL" >>"$CSV"; echo "[$g] COMPILE_FAIL"; continue
    fi

    timeout 40 "$HB" run --strict -- "$bin" >"$LOGDIR/$g.g.out" 2>"$LOGDIR/$g.g.err"; exit_g=$?
    timeout 60 "$HB" run --strict --verify -- "$bin" >/dev/null 2>"$LOGDIR/$g.gv.err"; L2_g=$(l2ok "$LOGDIR/$g.gv.err")
    timeout 60 "$HB" --backend e9patch run --strict -- "$bin" >"$LOGDIR/$g.e.out" 2>"$LOGDIR/$g.e.err"; exit_e=$?
    timeout 90 "$HB" --backend e9patch run --strict --verify -- "$bin" >/dev/null 2>"$LOGDIR/$g.ev.err"; L2_e=$(l2ok "$LOGDIR/$g.ev.err")

    cand=$(metric candidate_sites "$LOGDIR/$g.e.err")
    mapd=$(metric mapped_sites "$LOGDIR/$g.e.err")
    b0=$(metric b0_sites "$LOGDIR/$g.e.err")

    exit_par=$([ "$exit_g" = "$exit_e" ] && echo yes || echo no)
    stdout_par=$(cmp -s "$LOGDIR/$g.g.out" "$LOGDIR/$g.e.out" && echo yes || echo no)

    verdict=PASS_L2
    [ "$exit_par" = no ]    && verdict=EXIT_DIVERGE
    [ "$stdout_par" = no ]  && verdict=STDOUT_DIVERGE
    [ "$L2_g" != pass ]     && verdict=GOLDEN_NONDET
    [ "$L2_e" != pass ]     && verdict=E9_NONDET
    [ "${b0:-0}" != 0 ]     && [ -n "${b0}" ] && verdict=B0_REJECT
    [ "$exit_g" = 124 ]     && verdict=GOLDEN_TIMEOUT
    [ "$exit_e" = 124 ]     && verdict=E9_TIMEOUT

    echo "$g,$exit_g,$exit_e,$exit_par,$stdout_par,$L2_g,$L2_e,${cand:-},${mapd:-},${b0:-},$verdict" >>"$CSV"
    echo "[$g] exit $exit_g/$exit_e par=$exit_par stdout=$stdout_par L2 g=$L2_g e=$L2_e sites c/$cand m/$mapd b0/$b0 -> $verdict"
done
echo "== done; results in $CSV =="
column -t -s, "$CSV"
rm -rf "$WORK"
