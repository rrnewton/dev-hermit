#!/usr/bin/env bash
# Derive the two-root verdict table from results.csv, so the numbers quoted in
# README.md are computed from the manifest rather than transcribed by hand.
#
# Two hermit arms are reported:
#   hermit        -- `hermit run --strict` (default RCB-derived virtual time)
#   hermit-norcb  -- adds `--no-rcb-time`, required on hosts whose PMU fails
#                    validation (this one does); see README.md.
set -euo pipefail
CSV="${1:-$(dirname "${BASH_SOURCE[0]}")/results.csv}"
python3 - "$CSV" <<'PY'
import csv, sys, collections
rows=list(csv.DictReader(open(sys.argv[1])))
by=collections.defaultdict(dict); ver={}
for r in rows:
    by[r['package']][r['root']]=r['artifact_sha256']; ver[r['package']]=r['source_version']
nat=('native-n1','native-n2'); her=('hermit-a','hermit-b'); nor=('hermit-norcb-a','hermit-norcb-b')
def verdict(h,k):
    if not all(x in h for x in k): return None
    return 'IDENTICAL' if h[k[0]]==h[k[1]] else 'DIVERGES'
print(f"{'package':<12}{'version':<12}{'native':<11}{'hermit':<11}{'hermit+--no-rcb-time':<22}")
n_d=h_i=r_i=comp=rcomp=0
for p in sorted(by):
    h=by[p]; n=verdict(h,nat); e=verdict(h,her); r=verdict(h,nor)
    if n is None: print(f"{p:<12}{ver[p]:<12}INCOMPLETE"); continue
    comp+=1; n_d += n=='DIVERGES'; h_i += e=='IDENTICAL'
    if r is not None: rcomp+=1; r_i += r=='IDENTICAL'
    print(f"{p:<12}{ver[p]:<12}{n:<11}{(e or '-'):<11}{(r or 'not run'):<22}")
print()
print(f"packages with the native control + default hermit arm: {comp}")
print(f"  native two-root DIVERGES (control fired):  {n_d}/{comp}")
print(f"  hermit two-root IDENTICAL:                 {h_i}/{comp}")
print(f"packages also measured with --no-rcb-time:   {rcomp}")
print(f"  hermit+--no-rcb-time two-root IDENTICAL:   {r_i}/{rcomp}")
cw=sum(1 for p in by if verdict(by[p],nat)=='DIVERGES' and verdict(by[p],nor)=='IDENTICAL')
print(f"CONTROLLED WIN (native diverges AND hermit+--no-rcb-time identical): {cw}/{rcomp}")
PY
