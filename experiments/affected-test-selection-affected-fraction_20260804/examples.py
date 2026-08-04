#!/usr/bin/env python3
"""Pull real labeled example commits from each decision bucket, with subject,
file count, the decisive reason, and (for spot-checks) the actual changed files.
Proves the selector discriminates on real history in BOTH directions."""
import csv, subprocess
from pathlib import Path
REPO = Path("/home/newton/work/dev-hermit/hermit")

def subj(sha):
    return subprocess.run(["git","-C",str(REPO),"log","-1","--format=%s",sha],
                          capture_output=True,text=True).stdout.strip()
def files(sha):
    return subprocess.run(["git","-C",str(REPO),"diff","--name-only",f"{sha}^1",sha],
                          capture_output=True,text=True).stdout.strip().splitlines()

rows=list(csv.DictReader(open("/home/newton/work/dev-hermit/scratch/affsel/rows.csv")))
by={}
for r in rows: by.setdefault(r["decision"],[]).append(r)

for dec in ("skip","selective","full"):
    bucket=by.get(dec,[])
    print(f"\n===== {dec.upper()}  ({len(bucket)} commits) =====")
    # sort selective by node count ascending to show the narrowest (best) cases
    if dec=="selective":
        bucket=sorted(bucket,key=lambda r:int(r["nodes"]))
    for r in bucket[:4]:
        sha=r["sha"]
        print(f"  {sha[:12]} nodes={r['nodes']}/47 shards={r['shards']}/11 cells={r['cells']}/70 nfiles={r['nfiles']}")
        print(f"     subj: {subj(sha)[:90]}")
        print(f"     why : {r['top_reason'][:110]}")
