#!/usr/bin/env python3
"""Compute seeds/runs-to-reproduce stats per (bug, strategy) from results.tsv.

Metric model: each hermit strategy is deterministic given a seed, so the swept
seeds 1..N are a fixed sample of the strategy's reachable outcomes. "Random seed
selection" is modelled by shuffling that sample; seeds-to-first-repro is the
1-based index of the first seed whose signature hits the target, bootstrapped
over many shuffles (without replacement -> the honest finite-pool analog of the
geometric 1/p). Censored at >N when a shuffle never hits.

Target per bug:
  chunk_recover: baseline = the signature of `strict` seed 1 (a NON-exploring run
                 = sigA, the intermediate progress tick present). TARGET = any
                 signature != baseline (a distinct thread interleaving, sigB).
  demo08_check:  deterministic BUG_ON abort. TARGET = the modal signature (the
                 abort transcript), which every run produces -> trivially 1.
"""
import csv, statistics, random, collections, sys, os

random.seed(1234)  # reproducible bootstrap
HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results.tsv")
SUM = os.path.join(HERE, "summary.tsv")
TRIALS = 5000

rows = list(csv.DictReader(open(RES), delimiter="\t"))
IMG2 = os.path.join(HERE, "results_img2.tsv")
if os.path.exists(IMG2):
    rows += list(csv.DictReader(open(IMG2), delimiter="\t"))
BUGS = ["chunk_recover", "chunk_recover_img2", "demo08_check"]
by = collections.defaultdict(list)          # (bug,strat) -> [(seed,sig)]
for r in rows:
    by[(r["bug"], r["strategy"])].append((int(r["seed"]), r["sig12"]))

# baseline sig per bug = strict/seed1
baseline = {}
for (bug, strat), lst in by.items():
    if strat == "strict":
        s1 = [sig for sd, sig in lst if sd == 1]
        if s1:
            baseline[bug] = s1[0]

# modal sig per bug (for demo08 target = the abort)
modal = {}
allsig = collections.defaultdict(collections.Counter)
for (bug, strat), lst in by.items():
    for _, sig in lst:
        allsig[bug][sig] += 1
for bug, c in allsig.items():
    modal[bug] = c.most_common(1)[0][0]

def stf(hits):
    """bootstrap seeds-to-first-hit over shuffles; hits=list[bool]. returns
    (hit_rate, mean, median, p90) with '>N' when censored."""
    n = len(hits)
    p = sum(hits) / n if n else 0.0
    if p == 0.0:
        return p, None, None, None
    firsts = []
    idxs = list(range(n))
    for _ in range(TRIALS):
        random.shuffle(idxs)
        f = next((i + 1 for i in range(n) if hits[idxs[i]]), n + 1)  # n+1 = censored
        firsts.append(f)
    mean = statistics.mean(firsts)
    med = statistics.median(firsts)
    p90 = sorted(firsts)[int(0.9 * len(firsts)) - 1]
    return p, mean, med, p90

def coupon_both(sigs):
    """bootstrap seeds-to-observe-BOTH interleavings (chunk_recover only)."""
    uniq = set(sigs)
    if len(uniq) < 2:
        return None
    a, b = list(uniq)[:2]
    n = len(sigs); idxs = list(range(n)); outs = []
    for _ in range(TRIALS):
        random.shuffle(idxs)
        seen = set(); k = 0
        for i in idxs:
            k += 1; seen.add(sigs[i])
            if len(seen) >= 2:
                break
        outs.append(k if len(seen) >= 2 else n + 1)
    return statistics.mean(outs), statistics.median(outs), sorted(outs)[int(0.9*len(outs))-1]

out = open(SUM, "w")
w = csv.writer(out, delimiter="\t")
w.writerow(["bug", "strategy", "n", "coverage_sigs", "target",
            "hit_rate", "stf_mean", "stf_median", "stf_p90", "cover_both_mean"])

def fmt(x):
    if x is None: return "never"
    if isinstance(x, float): return f"{x:.2f}"
    return str(x)

order = ["plain", "strict", "chaos", "ctr", "ctr_ts"]
print(f"\nseeds-to-reproduce benchmark  (TRIALS={TRIALS} shuffles; '>N'/never = censored at pool size N)\n")
for bug in BUGS:
    str12 = {k[1]: v for k, v in by.items() if k[0] == bug}
    if not str12:
        continue
    n = len(next(iter(str12.values())))
    base = baseline.get(bug)
    tgt_desc = ("sigB = interleaving != baseline(strict/seed1)"
                if bug.startswith("chunk_recover") else
                f"BUG_ON abort sig {modal[bug]}")
    print(f"### {bug}   (N={n} seeds/strategy; target: {tgt_desc})")
    print(f"{'strategy':<8} {'covrg':>5} {'hit-rate':>9} {'stf_mean':>9} "
          f"{'stf_med':>8} {'stf_p90':>8}  {'exit(s)':<10}")
    for strat in order:
        if strat not in str12:
            continue
        lst = sorted(str12[strat])
        sigs = [sig for _, sig in lst]
        exits = collections.Counter(r["exit"] for r in rows
                                    if r["bug"] == bug and r["strategy"] == strat)
        cov = len(set(sigs))
        if bug.startswith("chunk_recover"):
            hits = [sig != base for sig in sigs]
        else:
            hits = [sig == modal[bug] for sig in sigs]
        p, mean, med, p90 = stf(hits)
        cb = coupon_both(sigs) if bug.startswith("chunk_recover") else None
        w.writerow([bug, strat, len(lst), cov,
                    "sigB" if bug.startswith("chunk_recover") else "abort",
                    f"{p:.3f}", fmt(mean), fmt(med), fmt(p90),
                    fmt(cb[0]) if cb else "n/a"])
        exstr = ",".join(f"{k}:{v}" for k, v in sorted(exits.items()))
        print(f"{strat:<8} {cov:>5} {p*100:>8.1f}% {fmt(mean):>9} "
              f"{fmt(med):>8} {fmt(p90):>8}  {exstr:<10}"
              + (f"  cover-both~{cb[1]:.0f} seeds" if cb else ""))
    print()
out.close()
print(f"summary -> {SUM}")
