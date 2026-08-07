#!/usr/bin/env bash
set -uo pipefail
cd /home/newton/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
export PYTHONDONTWRITEBYTECODE=1
H=${HERMIT_BIN:?}; D=$HOME/det4-q; rm -rf $D; mkdir -p $D
printf 'int main(void){return 0;}\n' > $D/tiny.c
OUT=ignored/det4-rung-qualify.tsv; : > $OUT
printf 'rung\ttarget_band\truns\tverdict\tY\tZ\tdetail\n' >> $OUT
qual() { local label=$1 band=$2; shift 2
  local ok=1
  for i in 1 2 3; do
    timeout 900 $H --log info --log-file $D/$label.$i.log run --backend=ptrace --strict -- "$@" \
      > $D/$label.$i.out 2> $D/$label.$i.err
    local rc=$?; local n=$(grep -cE 'DETLOG|COMMIT turn' $D/$label.$i.log 2>/dev/null||echo 0)
    [[ $rc -ne 0 || $n -eq 0 ]] && ok=0
  done
  if ((ok==0)); then echo "  $label TOOL-ERROR"
    printf '%s\t%s\t3\tTOOL-ERROR\t-\t-\ta run failed or emitted no records\n' "$label" "$band" >> $OUT; return; fi
  python3 - "$D" "$label" "$band" "$OUT" <<'PY'
import re,sys
D,label,band,out=sys.argv[1:5]
def recs(p): return [re.sub(r'^[0-9T:.Z-]+ +','',l.rstrip('\n'))
  for l in open(p,errors='replace') if 'DETLOG' in l or 'COMMIT turn' in l]
r=[recs(f"{D}/{label}.{i}.log") for i in (1,2,3)]
Z=len(r[0]); worst=None
for i in (1,2):
    a,b=r[0],r[i]; m=min(len(a),len(b))
    d=next((k for k in range(m) if a[k]!=b[k]), m if len(a)==len(b) else m)
    if len(a)==len(b) and d==m: continue
    if worst is None or d<worst[0]: worst=(d,i,a,b)
if worst is None:
    print(f"  {label:<18} {band:>6}  IDENTICAL  {Z} records")
    open(out,'a').write(f"{label}\t{band}\t3\tIDENTICAL\t{Z}\t{Z}\tall pairs agree\n")
else:
    d,i,a,b=worst
    det=f"run1 vs run{i+1} @{d}: - {a[d][:80] if d<len(a) else '<end>'} | + {b[d][:80] if d<len(b) else '<end>'}"
    print(f"  {label:<18} {band:>6}  *** FAIL *** {d}/{Z}")
    open(out,'a').write(f"{label}\t{band}\t3\tFAIL\t{d}\t{Z}\t{det}\n")
PY
}
qual gcc-tiny-c        ~5K  /usr/bin/gcc -O0 -o $D/tiny.out $D/tiny.c
qual tar-usr-include   ~10K /bin/sh -c '/usr/bin/tar cf /dev/null /usr/include'
qual fork-100          ~18K /bin/sh -c 'i=0; while [ $i -lt 100 ]; do /bin/true; i=$((i+1)); done'
qual grep-recursive    ~31K /bin/sh -c '/usr/bin/grep -rl include /usr/include | /usr/bin/wc -l'
qual fork-500          ~89K /bin/sh -c 'i=0; while [ $i -lt 500 ]; do /bin/true; i=$((i+1)); done'
qual fork-2000        ~353K /bin/sh -c 'i=0; while [ $i -lt 2000 ]; do /bin/true; i=$((i+1)); done'
echo; column -t -s$'\t' $OUT
