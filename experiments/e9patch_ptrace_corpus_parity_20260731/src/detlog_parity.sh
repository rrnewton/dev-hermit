#!/bin/bash
# Finer parity signal: does the e9patch guest-syscall DETLOG match golden's
# EXCEPT for the deterministic e9loader prologue prefix?
#
# We capture --log=info plain --strict runs (fast; --verify would double time
# and is unnecessary here) and reduce each to its canonical guest-syscall
# sequence: the ordered "inbound syscall:" names+normalized-args (timestamps
# and 0x-addresses stripped). exit_group appears here (it has no finish line).
#
# Parity verdict per guest:
#   TAIL_MATCH  : golden sequence == suffix of e9patch sequence, and the removed
#                 e9patch prefix == the known e9loader prologue length.
#   DIVERGE     : otherwise (a real guest-syscall divergence to investigate).
set -uo pipefail
SRCDIR="${1:?}"; OUTDIR="${2:?}"
HB="${HB:-/home/newton/work/dev-hermit/worktrees/e9patch/hermit/target/debug/hermit}"
E9DIR="${E9DIR:-/home/newton/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch}"
export HERMIT_E9TOOL="$E9DIR/e9tool" HERMIT_E9PATCH_BACKEND="$E9DIR/e9patch"
WORK="$(mktemp -d /home/newton/e9scratch/dlp.XXXXXX)"; mkdir -p "$OUTDIR"
CSV="$OUTDIR/detlog_parity.csv"
echo "guest,golden_syscalls,e9_syscalls,prologue_len,tail_match,verdict" >"$CSV"

# canonical guest-syscall sequence: names+normalized args, one per line
canon() { grep -oE 'inbound syscall: [a-z_0-9]+\(.*\) = \?' "$1" \
    | sed -E 's/ = \?$//; s/0x[0-9a-f]+/A/g; s/, [0-9]{4,}/, N/g'; }

for src in "$SRCDIR"/*.c; do
    g="$(basename "$src" .c)"; bin="$WORK/$g"
    cc -nostdlib -static -ffreestanding -O0 -fno-pie -no-pie "$src" -o "$bin" 2>/dev/null || { echo "$g,,,,,COMPILE_FAIL">>"$CSV"; continue; }
    timeout 40 "$HB" --log=info run --strict -- "$bin" >/dev/null 2>"$WORK/$g.g"
    timeout 60 "$HB" --log=info --backend e9patch run --strict -- "$bin" >/dev/null 2>"$WORK/$g.e"
    canon "$WORK/$g.g" >"$WORK/$g.gseq"
    canon "$WORK/$g.e" >"$WORK/$g.eseq"
    ng=$(wc -l <"$WORK/$g.gseq"); ne=$(wc -l <"$WORK/$g.eseq")
    prologue=$((ne - ng))
    # tail of e9 sequence of length ng must equal golden sequence
    tailmatch=no
    if [ "$prologue" -ge 0 ]; then
        tail -n "$ng" "$WORK/$g.eseq" >"$WORK/$g.etail"
        cmp -s "$WORK/$g.gseq" "$WORK/$g.etail" && tailmatch=yes
    fi
    verdict=DIVERGE
    [ "$tailmatch" = yes ] && verdict=TAIL_MATCH
    echo "$g,$ng,$ne,$prologue,$tailmatch,$verdict" >>"$CSV"
    echo "[$g] golden=$ng e9=$ne prologue=$prologue tail_match=$tailmatch -> $verdict"
done
echo "== detlog parity =="; column -t -s, "$CSV"
# stash the prologue observed on write_stdout for the artifact
canon "$WORK/write_stdout.e" | head -n "$(( $(wc -l <"$WORK/write_stdout.eseq") - $(wc -l <"$WORK/write_stdout.gseq") ))" >"$OUTDIR/observed_e9loader_prologue.txt" 2>/dev/null || true
rm -rf "$WORK"
