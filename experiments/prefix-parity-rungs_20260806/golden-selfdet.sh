#!/usr/bin/env bash
# ptrace golden self-determinism, per rung, n runs, FULL INFO log.
#
# Verdicts are three-valued on purpose:
#   IDENTICAL  every pair of runs agrees over the whole log
#   FAIL       a real first divergence -> the rung is DISQUALIFIED as a golden
#   TOOL-ERROR the harness/guest could not produce two comparable logs
# TOOL-ERROR is never reported as FAIL. Conflating them is what produced a
# dramatic false finding that survived a run earlier today.
#
# Nothing is normalized except the real wall-clock prefix, which is the one
# field with no deterministic content. No time blunting, no field dropping.
set -uo pipefail
cd /home/newton/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
H=${HERMIT_BIN:?set HERMIT_BIN}
N=${RUNS:-3}
OUT=ignored/det4-golden-selfdet.tsv
: > "$OUT"; printf 'rung\truns\tverdict\tY_depth\tZ_total\tdetail\n' >> "$OUT"

# label|command...
RUNGS=(
  "true|/bin/true"
  "echo|/bin/echo hi"
  "cat-hostname|/bin/cat /etc/hostname"
  "wc-hostname|/bin/wc -c /etc/hostname"
  "fork-exec-pipe|/bin/sh -c echo a | /bin/wc -c"
  "sh-loop-200|/bin/sh -c i=0; while [ \$i -lt 200 ]; do i=\$((i+1)); done; echo \$i"
  "ls-recursive|/bin/sh -c /bin/ls -R /usr/include | /bin/wc -l"
  "python-startup|/usr/bin/python3 -c print(1)"
  "readdir-heavy|/bin/sh -c /bin/ls -la /usr/lib64 | /bin/wc -l"
  "cat-big|/bin/cat /usr/include/linux/kvm.h"
)

for entry in "${RUNGS[@]}"; do
  label=${entry%%|*}; cmd=${entry#*|}
  W=ignored/det4-gsd/$label; rm -rf "$W"; mkdir -p "$W"
  echo "################ $label :: $cmd"
  ok=1
  for i in $(seq 1 "$N"); do
    timeout 600 "$H" --log info --log-file "$PWD/$W/r$i.log" run --backend=ptrace --strict \
      -- /bin/sh -c "$cmd" > "$W/r$i.out" 2> "$W/r$i.err"
    rc=$?
    n=$(grep -cE 'DETLOG|COMMIT turn' "$W/r$i.log" 2>/dev/null || echo 0)
    echo "   run$i rc=$rc records=$n"
    if [[ $rc -ne 0 || $n -eq 0 ]]; then ok=0; fi
  done
  if ((ok == 0)); then
    d=$(head -2 "$W/r1.err" | tr '\n' ' ' | cut -c1-140)
    echo "   -> TOOL-ERROR (a run failed or produced no records) — NOT a golden failure"
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
done
echo; column -t -s$'\t' "$OUT"
