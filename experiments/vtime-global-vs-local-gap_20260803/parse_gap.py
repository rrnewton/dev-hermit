#!/usr/bin/env python3
"""Parse a `RUST_LOG=detcore=trace hermit run` trace and measure the
GLOBAL(committed) - LOCAL(per-thread) virtual-time gap.

GLOBAL: `[sched-step3] Stepping scheduler, ... committed_time <T>` (DEBUG detcore::scheduler)
LOCAL : `[dtid N] updated rcb clock, new logical time: DetTime {...}, i.e. <T>s, ...` (TRACE detcore)

We walk the trace in order. committed_time updates a running "global". Each
per-dtid local-time line yields one sample: gap = global - local(dtid), paired
with the most recent committed_time seen (the scheduler state that thread is
interacting under). Times like `1_767_225_600.000_002_230s` -> integer ns.
"""
import re, sys, statistics as st

def to_ns(s):
    # "1_767_225_600.000_002_230s" -> ns int
    s = s.strip().rstrip('s').replace('_', '')
    if '.' not in s:
        return int(s)
    sec, frac = s.split('.')
    frac = (frac + '000000000')[:9]
    return int(sec) * 1_000_000_000 + int(frac)

COMMIT = re.compile(r'committed_time ([0-9_]+\.[0-9_]+s|\d[\d_]*ns)')
LOCAL  = re.compile(r'\[dtid (\d+)\].*?new logical time:.*?i\.e\. ([0-9_]+\.[0-9_]+s|\d[\d_]*ns)')

def norm(tok):
    if tok.endswith('ns'):
        return int(tok[:-2].replace('_',''))
    return to_ns(tok)

def main(path):
    glob = None
    samples = {}   # dtid -> list of gap ns
    first_commit = None
    max_commit = 0
    ncommit = 0
    for line in open(path, errors='replace'):
        mc = COMMIT.search(line)
        if mc:
            glob = norm(mc.group(1))
            if first_commit is None: first_commit = glob
            max_commit = max(max_commit, glob)
            ncommit += 1
            continue
        ml = LOCAL.search(line)
        if ml and glob is not None:
            dtid = int(ml.group(1))
            loc = norm(ml.group(2))
            gap = glob - loc
            samples.setdefault(dtid, []).append(gap)
    def pct(v, p):
        if not v: return 0
        k = (len(v)-1)*p
        f = int(k); c = min(f+1, len(v)-1)
        return v[f] + (v[c]-v[f])*(k-f)
    allgaps = []
    print(f"# committed_time updates: {ncommit}  span: {(max_commit-(first_commit or 0))/1e9:.6f}s global")
    print(f"{'dtid':>5} {'n':>6} {'min_ns':>12} {'med_ns':>14} {'p90_ns':>14} {'p99_ns':>14} {'max_ns':>16}")
    for dtid in sorted(samples):
        v = sorted(samples[dtid]); allgaps += v
        print(f"{dtid:>5} {len(v):>6} {min(v):>12} {int(st.median(v)):>14} {int(pct(v,.9)):>14} {int(pct(v,.99)):>14} {max(v):>16}")
    if allgaps:
        v = sorted(allgaps)
        print(f"{'ALL':>5} {len(v):>6} {min(v):>12} {int(st.median(v)):>14} {int(pct(v,.9)):>14} {int(pct(v,.99)):>14} {max(v):>16}")
        # gap in ms for readability
        print(f"# ALL gap ms: min {min(v)/1e6:.4f} med {st.median(v)/1e6:.4f} p90 {pct(v,.9)/1e6:.4f} p99 {pct(v,.99)/1e6:.4f} max {max(v)/1e6:.4f}")

if __name__ == '__main__':
    main(sys.argv[1])
