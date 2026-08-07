#!/usr/bin/env bash
# Derive the two-root verdict table from results.csv, so the numbers quoted in
# README.md are computed from the manifest rather than transcribed by hand.
set -euo pipefail
CSV="${1:-$(dirname "${BASH_SOURCE[0]}")/results.csv}"
python3 - "$CSV" <<'PY'
import csv, sys, collections
rows=list(csv.DictReader(open(sys.argv[1])))
by=collections.defaultdict(dict)
ver={}
for r in rows:
    by[r['package']][r['root']]=r['artifact_sha256']
    ver[r['package']]=r['source_version']
need=('native-n1','native-n2','hermit-a','hermit-b')
print(f"{'package':<16}{'version':<14}{'native':<12}{'hermit':<12}")
nat_div=her_id=complete=0
for p in sorted(by):
    h=by[p]
    if not all(k in h for k in need):
        print(f"{p:<16}{ver[p]:<14}{'INCOMPLETE':<12}"); continue
    complete+=1
    n = 'IDENTICAL' if h['native-n1']==h['native-n2'] else 'DIVERGES'
    e = 'IDENTICAL' if h['hermit-a']==h['hermit-b'] else 'DIVERGES'
    nat_div += n=='DIVERGES'; her_id += e=='IDENTICAL'
    print(f"{p:<16}{ver[p]:<14}{n:<12}{e:<12}")
print()
print(f"packages with all four builds: {complete}")
print(f"native two-root DIVERGES:      {nat_div}/{complete}")
print(f"hermit two-root IDENTICAL:     {her_id}/{complete}")
controlled = sum(1 for p in by if all(k in by[p] for k in need)
                 and by[p]['native-n1']!=by[p]['native-n2']
                 and by[p]['hermit-a']==by[p]['hermit-b'])
print(f"CONTROLLED WIN (native diverges AND hermit identical): {controlled}/{complete}")
PY
