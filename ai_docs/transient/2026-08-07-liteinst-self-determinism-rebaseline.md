# LiteInst re-baselined under candidate (1) self-determinism — and it fails, for a real defect

**Date:** 2026-08-07
**Author:** impl agent, opus-5 (task `ratchet-liteinst-strict-parity-after-dso-path-fix`, agent hermit-w28)
**Metric:** candidate **(1) self-determinism** (liteinst vs a liteinst golden), as decided by the owner.
This supersedes this task's own "comparing bitwise against the ptrace golden" clause, which was
candidate-(2) language.

## Bottom line

**LiteInst is not self-deterministic.** Cells ratcheted: **0** — but for the first time the reason is
a *located product defect* rather than a metric artifact. Its committed **virtual time** drifts
run-to-run inside the `/proc/self/maps` self-scan, first observable at COMMIT turn 10 on both guests.

## Setup and denominators

Binary `scratch/p4/bin/hermit`, `hermit 0.2.0 (2026-08-07, g590fcc9eeb03)`, sha256 `14813a81140c98b9…`;
DSO `libreverie_liteinst.so` sha256 `ef9c4051aff30388…` staged beside it. Env pinned with
`--base-env minimal` (an unpinned env makes Z host-dependent). Engagement witness verified on every
run: `traps=1 hooks=31` — so none of these is a zero-engagement artifact.

Guests: **2**. Runs per guest: **3**. Pairs per guest: **3**. Record counts stable across all runs
(31, 31, 31 and 32, 32, 32).

## Result — bitwise (resource AND committed virtual time)

| guest | Z | run1v2 | run1v3 | run2v3 | pairs fully identical |
|---|---:|---:|---:|---:|---:|
| `/bin/true` | 31 | **10/31** | 31/31 | **10/31** | **1 / 3** |
| `/bin/echo hello` | 32 | **10/32** | **10/32** | 32/32 | **1 / 3** |

Bimodal, and identically so on both guests: runs fall into classes; within a class they are
byte-identical, across classes they diverge at **exactly record 10**. Guests with different Z
diverging at the same index points to one shared cause, not per-guest noise.

## The defect, verbatim

```
[9]  run1: COMMIT turn 9,  ... {Path("/proc/self/maps"): R}, committed …006_611_650s
[9]  run2: COMMIT turn 9,  ... {Path("/proc/self/maps"): R}, committed …006_611_650s   SAME
[10] run1: COMMIT turn 10, ... {Path("/proc/self/maps"): R}, committed …007_489_380s
[10] run2: COMMIT turn 10, ... {Path("/proc/self/maps"): R}, committed …007_489_500s   DIFFERS
```

Same resource; **committed virtual time differs by 120 ns** and every later record inherits the skew.
This sits inside the fixed ~24-record `/proc/self/maps` self-scan LiteInst performs independent of the
guest. Virtual time is the quantity the product treats as sacred, so this is a genuine determinism
defect, not a rendering artifact.

## Old vs new, published side by side

| guest | OLD: vs ptrace golden (prefix) | NEW: self-determinism (worst pair) |
|---|---|---|
| `/bin/true` | 2 / 5 | **10 / 31** |
| `/bin/echo hello` | 2 / 6 | **10 / 32** |

The denominators are not comparable — old Z is the *ptrace* record count, new Z is the *liteinst*
count — which is exactly why the two definitions cannot share a number and why the re-baseline had
to be published rather than converted.

What the switch does buy: candidate (1) dissolves the injected-DSO problem entirely, because both
sides carry the DSO record. The old 2/5 was dominated by a legitimate insertion; the new 10/31 is
dominated by a real defect. **The metric now points at something worth fixing.**

## Why the shared manifest was not run

The task calls for the shared e2e manifest. Running it would not change the answer: the backend fails
self-determinism on a 5-record `/bin/true`, so the defect is upstream of every manifest cell. Cells
cannot be ratcheted while the backend is nondeterministic against itself. Fix the virtual-time drift
first, then the manifest sweep becomes meaningful.

## Limits

2 guests, 3 runs each — enough to establish nondeterminism (one differing pair suffices) but **not**
enough to characterise the class structure. Not measured: how many outcome classes exist, whether the
drift is bounded, whether it is load-dependent, or whether ptrace shows the same drift in a comparable
self-scan (ptrace has no such scan, so there is no direct control). Single host, single binary.

## Reproduction

```bash
cd scratch/p4/bin
for i in 1 2 3; do ./hermit --log=info run --backend liteinst --base-env minimal -- /bin/true > r$i.log 2>&1; done
# extract 'COMMIT turn N, dettid … using resources {…}, on previously committed …s'
# pairwise longest common prefix over those full records
```
