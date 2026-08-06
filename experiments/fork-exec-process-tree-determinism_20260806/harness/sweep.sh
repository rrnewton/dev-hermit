#!/usr/bin/env bash
# fork/exec + process-tree ORDERING DETERMINISM sweep.
#
# For every guest x backend it reports SIX nested facts, so a failure can be
# ATTRIBUTED rather than merely counted:
#
#   SELF-DETERMINISM (the double-run leg -- two independent hermit processes)
#     sd_out      run1 stdout == run2 stdout
#     sd_pev      run1 process-event trace == run2 process-event trace   <-- the task's assertion
#     sd_log      hermit log-diff reports no substantive differences (full DETLOG)
#
#   HERMIT'S OWN VERIFY (the --verify leg -- one process, guest run twice)
#     vrc         exit code of --strict --verify --verify-strict
#     bitp        bitwise_parity from --verify-json  (the repo's L2/L3 predicate)
#
#   CROSS-BACKEND PARITY (vs the ptrace golden reference)
#     px_out      stdout == ptrace stdout
#     px_pev      process-event trace == ptrace process-event trace
#     px_tree     "Final thread-tree" == ptrace's
#
# WHY px_pev AND NOT RAW log-diff FOR CROSS-BACKEND: --detlog-heap/-stack hash
# memory CONTENT, which holds absolute pointers, so ANY relocating backend
# diverges on every record while behaving identically. See
# ai_docs/cross-backend-detlog-parity-sweep-20260806.md 6. pevents.py is the
# relocation-invariant projection for the process-tree question specifically.
set -u
ROOT=/home/newton/work/dev-hermit
BASE=$ROOT/ignored/fork-exec-parity
export HERMIT_BIN="${HERMIT_BIN:?HERMIT_BIN must be set}"
# `hermit log-diff` is run directly from THIS shell (not through run-cell.sh's
# pinned `env -i`), so it needs the libunwind shim on its own path. Omitting it
# silently turns every sd_log cell into a false 0 -- observed once already.
export LD_LIBRARY_PATH="$ROOT/ignored/lu-parity/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
OUT="${OUT:-$BASE/sweep}"
BACKENDS="${BACKENDS:-ptrace}"
CSV="$OUT/results.csv"
mkdir -p "$OUT"
echo "guest,backend,rc1,rc2,sd_out,sd_pev,sd_log,vrc,vjson,px_out,px_pev,px_tree,n_pev,turns,tree,note" > "$CSV"

pev()   { python3 "$BASE/pevents.py" "$1" 2>/dev/null; }
tree_of() { grep -oE "Final thread-tree was: .*" "$1" 2>/dev/null | head -1 | sed 's/.*was: //'; }
turns_of() { grep -oE "scheduler ran [0-9]+ turns" "$1" 2>/dev/null | head -1 | grep -oE '[0-9]+'; }

for spec in "$@"; do
  name="${spec%%=*}"; cmd="${spec#*=}"
  read -r -a CMDA <<< "$cmd"
  d="$OUT/$name"; rm -rf "$d"; mkdir -p "$d"

  refpev=""; refout=""; reftree=""
  for b in $BACKENDS; do
    "$BASE/run-cell.sh"    "$b" "$d" "$b.r1" "${CMDA[@]}"
    "$BASE/run-cell.sh"    "$b" "$d" "$b.r2" "${CMDA[@]}"
    "$BASE/verify-cell.sh" "$b" "$d" "$b"    "${CMDA[@]}"

    rc1=$(cat "$d/$b.r1.rc" 2>/dev/null || echo 99)
    rc2=$(cat "$d/$b.r2.rc" 2>/dev/null || echo 99)
    vrc=$(cat "$d/$b.vrc"   2>/dev/null || echo 99)

    if [ "$rc1" != 0 ] || [ "$rc2" != 0 ]; then
      note=$(head -c 120 "$d/$b.r1.err" 2>/dev/null | tr '\n,' '; ' )
      echo "$name,$b,$rc1,$rc2,,,,$vrc,,,,,,,,\"run-failed: $note\"" >> "$CSV"
      continue
    fi

    # --- self-determinism (external double-run) ---
    cmp -s "$d/$b.r1.out" "$d/$b.r2.out" && sdo=1 || sdo=0
    pev "$d/$b.r1.log" > "$d/$b.r1.pev"; pev "$d/$b.r2.log" > "$d/$b.r2.pev"
    cmp -s "$d/$b.r1.pev" "$d/$b.r2.pev" && sdp=1 || sdp=0
    if "$HERMIT_BIN" log-diff --no-color --limit 1 "$d/$b.r1.log" "$d/$b.r2.log" 2>&1 \
         | grep -q "no substantive differences"; then sdl=1; else sdl=0; fi

    # --- hermit's own --verify ---
    # Plain --verify reports `verified`; `bitwise_parity` is the L2 predicate and
    # is only meaningful under --verify-strict (inert here -- see verify-cell.sh).
    bitp=$(python3 -c "
import json
try:
    d=json.load(open('$d/$b.verify.json'))
    print(str(d.get('verified','?'))+'/'+str(d.get('verdict','?')))
except Exception: print('na')" 2>/dev/null)

    npev=$(wc -l < "$d/$b.r1.pev")
    turns=$(turns_of "$d/$b.r1.log")
    tree=$(tree_of "$d/$b.r1.log")

    # --- cross-backend parity vs ptrace reference (first backend in BACKENDS) ---
    if [ -z "$refpev" ]; then
      refpev="$d/$b.r1.pev"; refout="$d/$b.r1.out"; reftree="$tree"
      pxo=ref; pxp=ref; pxt=ref
    else
      cmp -s "$d/$b.r1.out" "$refout" && pxo=1 || pxo=0
      cmp -s "$d/$b.r1.pev" "$refpev" && pxp=1 || pxp=0
      [ "$tree" = "$reftree" ] && pxt=1 || pxt=0
    fi

    echo "$name,$b,$rc1,$rc2,$sdo,$sdp,$sdl,$vrc,$bitp,$pxo,$pxp,$pxt,$npev,$turns,\"$tree\"," >> "$CSV"
  done
done
column -s, -t < "$CSV"
