# validate wall/CPU ratchet + resolution of the 2.9× vs 1.78× contradiction

**Date:** 2026-08-04  **Host:** devbig014 (316-core)  **Owner task:**
`did-the-dag-migration-make-builds-slower-nobody-checked`

## Question

Three tangled questions from the owner:

1. **Did the DAG migration make builds slower?** — and if we cannot tell, build
   the mechanism (a RATCHET) so the *next* infrastructure change cannot hide its
   cost, and state the retention needed to answer this class of question.
2. **Resolve a contradiction:** measured CPU/wall on 100 full-profile passes has
   **median 2.9×**, but the simulation says the achievable ceiling is **1.78×**
   (makespan flat from j=4). *Achieved cannot exceed achievable.* One is wrong.
3. (mid-task) The 600s-budget **tail is concurrency**, not slow code — validate
   the proxy, find the admission knee, and separate between-validate from
   within-validate contention.

## Can we answer Q1 directly? NO — and that is the finding.

The validate-run ledger (`ignored/validate-run-ledger.jsonl`) spans **1.6 days**
(2026-08-03/04). The DAG migration predates it entirely. **There is no
before-side and none can be reconstructed.** Producing a before/after slice from
this ledger would manufacture an answer — the exact failure this task exists to
prevent. So Q1's deliverable is the *mechanism* + *retention statement*, not a
number.

## Q2 — the contradiction is NOT a contradiction: the two numbers describe two LEVELS

validate parallelism is two-level. The whole-run CPU/wall **ratio conflates
them**, which is the entire source of the apparent paradox.

| number | level | what it is | constraint |
|---|---|---|---|
| **1.78×** | **A1 — OUTER** | DAG-node makespan speedup (concurrent *steps*) | deps + `hermit_guest=1` cap; flat from j=4 |
| **2.9×** | **A2 — TOTAL** | `cpu/wall` = time-avg busy cores = **outer × inner** | box cores (316); peak core-demand 159 |

"Achieved cannot exceed achievable" holds **within a level**. 2.9 (A2, total) vs
1.78 (A1, outer) compares **across levels**, so there is no violation: A2 = A1 ×
(inner per-node width). The excess over 1.78 is **inner compile-node thread
fan-out** (~27 concurrent `rustc` in the build's fat middle).

**Proof it is inner fan-out (results.csv, `cache_state` segment):** CPU/wall is
sharply cache-state-split —

- **cold 8.6×** — builds run wide, inner rustc fan-out dominates A2.
- **warm 2.4×** — builds are cached, the spine is `--test-threads=1` test nodes
  (inner≈1), so A2 **collapses toward A1** (2.4 ≈ 1.78 + small residue).

If 2.9× were outer DAG parallelism it could not move with build-cache state; it
does, by 3.6×. **1.78× describes A1 (outer). 2.9× describes A2 (total),
dominated by inner build-node threads.** Both are correct; neither is wrong.

The A2 *achievable* ceiling is far above 2.9× (bounded by box cores / the 159
peak core-demand), so the achieved 2.9× is nowhere near any A2 ceiling — it is
just what this cache-mixed workload draws on average.

## Q3 — the 600s tail is between-validate concurrency (a THIRD contention axis)

Beyond outer×inner *within* one validate, many validates share the box and its
cargo state. Counting validates (any profile) whose window overlaps each full
pass:

- **Proxy validated:** `<600s` avg **6.3** concurrent vs `≥600s` avg **11.0**
  (owner measured 5.3 / 10.0; the ~1 gap is self-inclusion). The effect
  **survives the cache confound** (persists warm-only), so concurrency is a real
  independent driver, not cache in disguise.
- **The knee (warm, results.csv `concurrency_warm`):** budget-met is **100% at
  ≤4 concurrent, ~90% through 7, 56% at ≥12**; median wall 465–484s through
  conc 7, then 748s at 8–11. **Admission limit for the drain: ~6–7 concurrent
  validates** (100% at ≤4, ≥90% through 7).
- **Between vs within split:** as concurrency rises, **CPU/wall stays ~2.4** (it
  does *not* scale) while **wall inflates 465→582→748s**. So the tail is
  **scheduling / time-slice contention** — wall is stretched while real work per
  run is ~flat — *not* a per-run `-j` explosion. CPU-seconds does creep +33%
  (1041→1383s), i.e. some genuine shared-cargo thrash; wall inflation outpaces
  it, so admission control (cap concurrent validates) is the dominant lever, not
  lowering inner `-j`. **Re-measure after hermit-238b (one-fat-build collapse):**
  if shared cargo state is the common cause, collapsing to one build should
  shrink both the CPU-s creep and the wall inflation.

> **Cannot cross-check the proxy against historical box load:** box load is not
> retained (`ci-hub/bin/load-probe` is point-in-time). That absence *is* the
> retention finding (below).

## Deliverable 1 — THE RATCHET

`ci-hub/validate/wall_cpu_ratchet.py` — a lint over the same ledger validate.sh
already writes (no parallel store). It ratchets **wall and CPU-seconds
separately** (never the conflated ratio) against a trailing robust baseline
(median + K·MAD, relative floor) of same-`(profile, cache_state, host)` passing
runs that **finished before the target started** — so a crossing names the
**first commit** to exceed the band.

Because the measurements above prove wall is confounded by cache_state and by
concurrency, the ratchet **holds both constant**:

- baselines are **bucketed by cache_state** (warm never compared to cold);
- a wall crossing measured while **concurrency was elevated** vs the baseline is
  marked **CONFOUNDED** (scheduling, exit 0) and **not blamed on the commit**;
- **CPU-seconds is not concurrency-sensitive**, so a CPU crossing fires
  regardless — that is the two-level discriminator operationalized.

Split alarms localize the level: `wall↑ cpu≈` = outer parallelism lost (a dep
serialized the DAG / a cap tightened); `cpu↑` = a node's total work grew.

Exit codes: `0` within band (or confounded), `2` insufficient baseline
(no-result, never a pass/fail), `3` genuine regression at the commit.

```
# every run (or as a CI lint step), after the ledger row is appended:
python3 ci-hub/validate/wall_cpu_ratchet.py \
  --ledger ignored/validate-run-ledger.jsonl check       # exit 3 => regression
python3 ci-hub/validate/wall_cpu_ratchet.py report        # current normals + retention warning
```

Tests: `python3 -m unittest ci-hub/validate/tests/test_wall_cpu_ratchet.py`
(7 tests, both sides bracketed: alarm fires on a real crossing, stays silent on
a confounded one, CPU fires under concurrency, cold≠warm bucketing).

## Deliverable 2 — RETENTION needed

The ledger is a JSONL append log; the scalar columns the ratchet needs
(commit, profile, cache_state, host, started/finished, real/user/sys_seconds)
are ~200 bytes/row. At the observed rate (~100 full passes / 1.6 days ≈ 60/day,
plus other profiles) the whole ledger is **≈10 MB/year compacted** — negligible.

- **A robust per-commit baseline** forms in **<1 day** (≥8 same-bucket warm
  passes accrue in hours). So the *ratchet* needs only days of history.
- **Answering "did a migration months ago change build cost"** needs retention
  spanning the change **plus a stable window on each side**. Infra changes here
  arrive weekly-to-monthly, so the floor is **≥90 days** of full rows.
- **Recommendation: retain the scalar ledger PERMANENTLY** (append-only, never
  rotated). The cost is trivial and the entire failure mode is "we didn't keep
  it long enough." If raw `gates[]` arrays must be pruned to save space, prune
  *only* those; keep the per-run scalars forever. `report` prints a RETENTION
  WARNING while span < 90 days.
- **Also retain box-load history** (a periodic `load-probe` sample) so a
  concurrency proxy can be checked against real load next time — its absence is
  why Q3's proxy can only be validated internally today.

## Deliverable 3 — going forward

Capture a BEFORE baseline on any infrastructure change that could affect timing
(N full passes at fixed cache_state and concurrency on the pre-change SHA). The
absence of one is why Q1 is unanswerable now; the ratchet makes the after-side
automatic, but the before-side still has to be taken deliberately.

## Reproduction

```
python3 ci-hub/validate/wall_cpu_ratchet.py report          # bucket table
python3 -m unittest ci-hub/validate/tests/test_wall_cpu_ratchet.py
# results.csv regenerated by the inline analysis over the ledger (see metadata.json)
```

Numbers are cache- and concurrency-dependent snapshots of a 1.6-day,
single-host (devbig014) ledger; they are *current normals*, not universal
constants. Their purpose is to calibrate the ratchet, which recomputes them
continuously.
