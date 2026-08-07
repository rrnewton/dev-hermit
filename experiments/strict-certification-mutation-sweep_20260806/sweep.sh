#!/bin/bash
# Mutation sweep: for each mutant x backend x comparator, does the probe FAIL?
H=/home/newton/work/dev-hermit/hermit/target/debug/hermit
MUT=$(cd "$(dirname "$0")/mutants" && pwd)
OUT="$(dirname "$0")/results.csv"
BACKENDS="${BACKENDS:-ptrace}"
MUTANTS="${MUTANTS:-clean_ctrl mut_stdout mut_exit mut_detlog_only mut_addr mut_path}"
[ -f "$OUT" ] || echo "backend,mutant,comparator,rc,verdict,bitwise_parity,cmp_left,cmp_right,banner" > "$OUT"
for b in $BACKENDS; do
 for m in $MUTANTS; do
  for cmpr in stripped strict; do
    rm -f /tmp/w7mutstate /tmp/w7mutpath_* /tmp/w7v.json
    flags=(run)
    [ "$b" != ptrace ] && flags=(--backend "$b" run)
    extra=()
    [ "$cmpr" = strict ] && extra=(--verify-strict)
    [ "$b" = ptrace ] && extra+=(--no-virtualize-cpuid)
    set +e
    err=$(timeout 180 "$H" "${flags[@]}" --strict --verify --verify-allow both \
        "${extra[@]}" --verify-json=/tmp/w7v.json \
        --base-env=minimal --max-timeslice=disabled --tmp=/tmp \
        -- "$MUT/$m" 2>&1 >/dev/null)
    rc=$?
    set -e
    if [ -s /tmp/w7v.json ]; then
      read -r vd bp cl cr < <(python3 -c "
import json,sys
d=json.load(open('/tmp/w7v.json'))
c=d.get('compared_log_messages') or {}
print(d.get('verdict'), d.get('bitwise_parity'), c.get('left','NA'), c.get('right','NA'))
")
    else vd=NOJSON; bp=NA; cl=NA; cr=NA; fi
    banner=$(printf '%s' "$err" | grep -oE ':: (Success|Failure|Error)[^|]*' | tail -1 | tr -d ',' | cut -c1-70)
    [ -z "$banner" ] && banner=$(printf '%s' "$err" | tail -1 | tr -d ',' | cut -c1-70)
    echo "$b,$m,$cmpr,$rc,$vd,$bp,$cl,$cr,$banner" | tee -a "$OUT"
  done
 done
done
