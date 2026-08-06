#!/usr/bin/env bash
# ptrace golden self-determinism, per rung, n runs, FULL INFO log.
#
# Continues ignored/det4-golden-selfdet.sh with two fixes it left open:
#
#  1. python-startup was TOOL-ERROR, not a verdict. Cause: the runner wrapped every
#     rung in `/bin/sh -c "$cmd"`, so `/usr/bin/python3 -c print(1)` reached sh as
#     UNQUOTED `print(1)` and sh died on the paren. Here a rung declares whether it
#     needs a shell; python is run DIRECTLY (argv, no shell), so nothing can eat it.
#
#  2. The ladder had no rung between ~1715 records and demo05's ~1.5M. `dd bs=1` is a
#     tunable knob -- one read + one write syscall per block -- so count= brackets the
#     gap by construction instead of hoping a workload lands in the right band.
#
# Verdicts stay three-valued: IDENTICAL / FAIL / TOOL-ERROR. A run that crashes, times
# out, or emits zero records is TOOL-ERROR and is NEVER reported as FAIL.
#
# Nothing is normalized except the real wall-clock prefix -- the one field with no
# deterministic content. No time blunting, no field dropping, no address masking.
set -uo pipefail
cd /home/newton/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
H=${HERMIT_BIN:?set HERMIT_BIN}
N=${RUNS:-3}
OUT=${OUT:-ignored/w2-rungs/rung-selfdet.tsv}
WROOT=${WROOT:-ignored/w2-rungs/w}
mkdir -p "$(dirname "$OUT")"
: > "$OUT"; printf 'rung\truns\tverdict\tY_depth\tZ_total\tdetail\n' >> "$OUT"

# label | shell? (sh|direct) | command
RUNGS=(
  "python-startup|direct|/usr/bin/python3 -c print(1)"
  "python-imports|direct|/usr/bin/python3 -c import json,os,sys,re;print(len(sys.modules))"
  "dd-1k|sh|/bin/dd if=/dev/zero of=/dev/null bs=1 count=1000 2>/dev/null"
  "dd-10k|sh|/bin/dd if=/dev/zero of=/dev/null bs=1 count=10000 2>/dev/null"
  "dd-100k|sh|/bin/dd if=/dev/zero of=/dev/null bs=1 count=100000 2>/dev/null"
  "dd-400k|sh|/bin/dd if=/dev/zero of=/dev/null bs=1 count=400000 2>/dev/null"
)
[ -n "${ONLY:-}" ] && mapfile -t RUNGS < <(printf '%s\n' "${RUNGS[@]}" | grep -E "^(${ONLY})\|")

for entry in "${RUNGS[@]}"; do
  label=${entry%%|*}; rest=${entry#*|}; mode=${rest%%|*}; cmd=${rest#*|}
  W=$WROOT/$label; rm -rf "$W"; mkdir -p "$W"
  echo "################ $label [$mode] :: $cmd"
  ok=1
  for i in $(seq 1 "$N"); do
    if [ "$mode" = sh ]; then
      timeout 900 "$H" --log info --log-file "$PWD/$W/r$i.log" run --backend=ptrace --strict \
        -- /bin/sh -c "$cmd" > "$W/r$i.out" 2> "$W/r$i.err"
    else
      # DIRECT argv: no shell, so quoting cannot be eaten. This is the python fix.
      timeout 900 "$H" --log info --log-file "$PWD/$W/r$i.log" run --backend=ptrace --strict \
        -- $cmd > "$W/r$i.out" 2> "$W/r$i.err"
    fi
    rc=$?
    n=$(grep -cE 'DETLOG|COMMIT turn' "$W/r$i.log" 2>/dev/null || echo 0)
    echo "   run$i rc=$rc records=$n out=$(head -c 40 "$W/r$i.out" | tr '\n' ' ')"
    if [[ $rc -ne 0 || $n -eq 0 ]]; then ok=0; fi
  done
  if ((ok == 0)); then
    d=$(head -2 "$W/r1.err" | tr '\n' ' ' | cut -c1-140)
    echo "   -> TOOL-ERROR (a run failed or produced no records) - NOT a golden failure"
    printf '%s\t%s\tTOOL-ERROR\t-\t-\t%s\n' "$label" "$N" "$d" >> "$OUT"
    continue
  fi
  python3 - "$W" "$N" "$label" "$OUT" <<'PY'
import re,sys
W,N,label,out=sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4]
def recs(p):
    return [re.sub(r'^[0-9T:.Z-]+ +','',l.rstrip('\n'))
            for l in open(p,errors='replace') if 'DETLOG' in l or 'COMMIT turn' in l]
runs=[recs(f"{W}/r{i}.log") for i in range(1,N+1)]
Z=len(runs[0]); worst=None
for i in range(1,N):
    a,b=runs[0],runs[i]
    m=min(len(a),len(b))
    d=next((k for k in range(m) if a[k]!=b[k]), m if len(a)==len(b) else m)
    if len(a)==len(b) and d==m: continue
    if worst is None or d<worst[0]: worst=(d,i,a,b)
if worst is None:
    print(f"   -> IDENTICAL across {N} runs ({Z} records)")
    open(out,'a').write(f"{label}\t{N}\tIDENTICAL\t{Z}\t{Z}\tall pairs agree over the whole log\n")
else:
    d,i,a,b=worst
    det=f"run1 vs run{i+1} @{d}: - {a[d][:90] if d<len(a) else '<end>'} | + {b[d][:90] if d<len(b) else '<end>'}"
    print(f"   -> *** FAIL *** self-depth {d}/{Z}  ({det})")
    open(out,'a').write(f"{label}\t{N}\tFAIL\t{d}\t{Z}\t{det}\n")
PY
  rm -f "$W"/r*.log
done
echo; column -t -s$'\t' "$OUT"
