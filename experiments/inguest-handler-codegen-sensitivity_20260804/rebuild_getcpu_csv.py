#!/usr/bin/env python3
# Authoritative parser of raw /usr/bin/time -v files (live awk had \s bug -> cpu=0).
import re, glob, os, statistics
RAW="scratch/inguest-codegen-getcpu/raw"
def parse(tf):
    u=s=None
    for ln in open(tf):
        m=re.search(r'User time \(seconds\):\s*([\d.]+)',ln);  u=float(m.group(1)) if m else u
        m=re.search(r'System time \(seconds\):\s*([\d.]+)',ln); s=float(m.group(1)) if m else s
    return (u,s)
rows={}  # (variant,N)->[cpu...]
for tf in sorted(glob.glob(f"{RAW}/*.time")):
    b=os.path.basename(tf)[:-5]  # strip .time
    m=re.match(r'(\w+)_N(\d+)_r(\d+)',b)
    if not m: continue
    var,N,rep=m.group(1),int(m.group(2)),int(m.group(3))
    if rep==1: continue  # discard warmup
    u,s=parse(tf)
    if u is None: continue
    rows.setdefault((var,N),[]).append(u+s)
# median CPU per (variant,N), then slope
import collections
med={k:statistics.median(v) for k,v in rows.items()}
print(f"{'variant':8} {'cpu@1e5':>9} {'cpu@2e6':>9} {'ns/call':>9}")
NLO,NHI=100000,2000000
for var in ("native","A","D"):
    lo=med.get((var,NLO)); hi=med.get((var,NHI))
    if lo is None or hi is None: continue
    ns=(hi-lo)/(NHI-NLO)*1e9
    print(f"{var:8} {lo:9.4f} {hi:9.4f} {ns:9.1f}")
