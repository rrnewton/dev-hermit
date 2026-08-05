# Staging-branch drain: measured result + cost both ways (2026-08-04)

Task `staging-branch-merge-all-prs-test-once` (hermit-lander). Analysis of the staging run
executed earlier today; states the cost serial-vs-staging the owner asked for, and reports the
attribution outcome (the design risk the owner flagged).

## What was run (prior agent, ~12:47–12:49)
- `staging/drain-all` cut from hermit `origin/main` `b384187`, 61 PRs merged `--no-ff`
  (11 patching-backend PRs excluded per owner "land LAST"): **18 rode in clean, 43 conflicted, 0 no-ref**.
- Reverie pin bumped ONCE `79517704` → `114b3df` (latest reverie main) at the staging tip.
- ONE FULL validate at commit-anchored `aff18cf` (systemd-run producer path, boxed).

## Result: RED — and cheaply attributed (the key finding)
Validate FAILED in **2m19s wall / 14m35s CPU across 316 cores**. Exact cause, from the DAG's
per-step report (no bisect needed):

```
[check.script_sigpipe] prelude-cache-key.sh: FAIL — prelude cache-key is stale in 1 consumer(s):
  scripts/validate.rs (have: MISSING, want: 088ae17fa4a1)
  Fix: scripts/lib/prelude-cache-key.sh --write   (then commit the result)
safe-ci-dag-runner: FAIL - 2 passed, 1 failed, 3 aborted, 41 skipped in 0.4s
```

- **A single mechanical lint**, not a product break, not a build/test failure. A merged PR changed
  `scripts/lib/rust_script_prelude.rs` (and added `prelude-cache-key.sh` — it is NOT on main) but
  `scripts/validate.rs` was not restamped. One-line fix + commit.
- **Attribution cost ≈ 0.** The feared failure mode ("staging is red, you don't know which PR, now
  bisect") did NOT occur: the DAG named the exact file and fix in 0.4s. The `keep_going` DAG runner
  gives free per-step attribution — this is the property that makes staging safe here.
- **The build blocker that stalled the serial drain appears CLEARED by the bump.** The privileged
  DAG *compiled `reverie-dbi` at `114b3df`* and passed 7/7 (96s). The portable DAG short-circuited
  at the 0.4s lint BEFORE its build ran, so full portable build/test green is not yet proven — but
  the reverie-dbi build panic that blocked drain at the old pin did not recur at 114b3df.

## Cost both ways (owner's explicit ask), today's numbers
Validate ≈ 528s median; backlog of ~21 orphaned+floor-clearing READY PRs; measured **100% receipt
orphan rate on rebase** (each landing rewrites the SHA the next PR's receipt is keyed to).

- **Serial drain.** Each landing rebases everything behind it and voids its receipt → forced
  re-validate. Not N×528s but roughly **Σₖ k·528 ≈ N²/2·528**. For N=21 that is ~1.1e5 s (**~32h**)
  worst case; even amortized it is several×11,000s, plus N rebases. This is the quadratic the owner
  identified; it is why serial draining is self-defeating.
- **Staging.** ONE conflict-resolution pass (concentrated on ~7 registry files, below) + ONE
  validate (~528–715s) + a bisect budget on failure. **Measured bisect budget this run = ~0** (lint
  named itself). Even pricing a full bisect (log₂61 ≈ 6 validates ≈ 3,200s), staging total is
  ~4,000–7,000s — **roughly ¼ to 1/20 of serial**, and O(1) in N for the validate dimension.

Staging wins decisively, and the empirical run refutes the attribution objection for this backlog.

## Honest scope limits (do not overclaim)
- **Landing value of THIS branch is small.** Of the 18 clean riders only **6 are READY**
  (1200 1213 1412 1430 1470 1514); 12 are DRAFTS (must not land). The 43 conflicted PRs — which
  include most READY floor-clearing candidates — are NOT in the branch.
- **Conflicts are a ~7-file registry-accumulator problem, not deep code.** Collision frequency:
  test-files.json 21, backend-parity/README.md 15, matrix.tsv 13, backend-parity-c.toml 10,
  ci/expected-e2e-plan.json 5, run_matrix.py 4, ci/dag/portable.json 3. TSV/markdown/toml are
  union-safe; the 3 JSON files need **format-aware merge**, not blind union. This is the single
  highest-leverage follow-up and it is file-level (7 files), not per-PR.
- **The branch is now STALE.** main advanced `b384187` → `f80b1c09`. Per the soft/hard-green rule,
  staging must be re-cut/rebased onto current main; a clean rebase → soft (inherited) green, a
  conflict-resolving rebase → green VOID, hard re-validate required.
- **Adversarial review already failed the reverie sub-batch 0/9** (member-specific blockers).
  Merging staging→main is a coordinator-gated landing, not a lander unilateral action; drafts and
  unreviewed members must not ride in.

## Recommended next actions (executable)
1. Re-cut `staging/drain-all` from current `f80b1c09`; merge the READY, review-passed set only.
2. Apply the one-line fix `scripts/lib/prelude-cache-key.sh --write` (restamp `scripts/validate.rs`)
   as part of the staging tip, then re-validate ONCE. Expect the portable DAG to then reach build;
   at 114b3df reverie-dbi builds, so a full green is now plausible.
3. Build the format-aware union merger for the 3 JSON registries — unblocks the 43 conflicted PRs
   that carry the real drain value.
4. Coordinate with the rebase-front work: rebased floor-blocked heads are the staging inputs;
   validate-after-push (push rewrites head → pre-push receipt is dead).

Existing origin staging refs: `staging/drain-all` (bd5272b), `staging/membership-passing-1470`,
`staging/membership-passing-1470-1551-1544`, `staging/patching-coalesce`.

---

## CORRECTION (lander, 2026-08-04T20:45Z) — corrected baseline + failure differential flips the verdict

The section above used a **528s blend** and a **pre-soft-green serial model**. Both inputs are now
superseded. Re-priced, **staging does NOT clearly win** over disciplined serial. Two changes:

### Input 1 — corrected per-validate baseline (owner)
- WARM median **500s** (n=94); COLD median **732s** (n=14). Not a 527/528s blend.
- Failure differential (NEW): **cold 70% fail vs warm 46% fail** (ledger-corroborated 79%/55% deduped,
  see [[cache-state-cold-fail-is-build-surface-not-slot-or-oom]]). The cold excess is **not noise** — it
  is the **DynamoRIO build SPOF** (cc1plus OOM-kill / 0-byte `scheduler_impl.cpp.o` link, forced by
  `build.workspace --features third-party-backends` on 31 dependents). Cold builds max surface and hits
  it; warm has it cached. **A large part of cold's 70% is DETERMINISTIC, not retryable** — it blocks,
  it doesn't retry-to-green.
- Retry-inflated expected wall per GREEN validate (upper-bound, treats fails as flaky-retryable):
  WARM `500/(1−0.46)=926s`; COLD `732/(1−0.70)=2440s`.

### Input 2 — serial is NO LONGER quadratic (the founding premise is stale)
The `ci-hub/landing/rebase_wrapper.py` **soft-green** machinery landed TODAY (`744b3877` rebase+soft-green,
`02e02bd` receipt_at_Z; see [[rebase-wrapper-soft-green-confidence-levels]]). A **clean** rebase onto new
main **inherits** green (soft) — no re-validate; only a conflict-resolving rebase voids it. That **breaks
the 100% receipt-orphan → N² re-validate** loop this whole task was premised on. Disciplined serial
(rebase-front + soft-green) is now ~**linear** (≤ N warm validates; clean re-rebases land free).

### Cost both ways, corrected (N = landable PRs; formula + N=21 & N=30)

| model | validates | per-green | N=21 | N=30 |
|---|---|---|---|---|
| **S1 legacy naive serial** (batch-validate-ahead, 100% orphan, NO soft-green) | N(N+1)/2 warm | 926s | ~214k s (**~59h**) | ~431k s (**~120h**) |
| **S2 disciplined serial** (rebase-front + soft-green inheritance, landed today) | ≤ N warm | 926s | ~19.4k s (**~5.4h**) | ~27.8k s (**~7.7h**) |
| **staging** (1 cold validate + attribution/bisect over 61 merged) | 1 + up to log₂61≈6 cold | 2440s | **~0.7h best … ~4.7h worst** | same (O(1) in N) |

### Verdict — say so
- Staging **decisively beats S1** (legacy naive) — ~12–85×. That is the prior memo's win. **But S1 is no
  longer the alternative**: the soft-green wrapper landed, so the realistic serial is S2.
- Staging (~0.7–4.7h) **vs S2 (~5.4–7.7h): NO CLEAR WINNER.** Staging's best case beats S2; its worst
  case ≈ S2. And staging carries **three risks S2 does not**:
  1. **Cold + max build surface → maximum DynamoRIO-SPOF exposure.** Staging is blocked on the SAME
     SPOF as serial, hit HARDEST. Its 70% is largely the deterministic SPOF → **retry won't clear it;
     it's gated on the DynamoRIO fix, full stop** (matches the 19:15 HOLD note).
  2. **A cold RED is ambiguous.** The owner is right that "a red staging branch does not say which PR
     broke it" — the 12:49 run got a *clean self-naming lint* (attribution ≈ 0), but a **flaky/SPOF
     build red** is indistinguishable from a culprit red without re-running; attribution-is-free was a
     lucky property of that specific failure, not a guarantee.
  3. **Small realized denominator.** Only ~6 of 18 clean riders are READY (rest drafts); the 43
     high-value conflicted PRs aren't in staging without the format-aware JSON registry merger. O(1)
     amortized over ~6 landings is a weak win.

**BOTTOM LINE:** Priced with the corrected cold/warm split and the today-landed soft-green wrapper,
**staging does not clearly win.** Run it **speculatively** (it costs nothing if abandoned, per owner) but
do **not** treat it as the cost-winning strategy — the disciplined rebase-front + soft-green serial is
comparable and lower-risk. **Both are currently BLOCKED on the DynamoRIO build SPOF**; that fix, not the
staging-vs-serial choice, is the gating item. The merge-gate change is **HELD for review** (owner).

---

## CORRECTION 2 (lander, 2026-08-04T21:0xZ) — the CORRECTION above under-credited staging; split by conflict component

Owner pushed back (21:0xZ): the queue grew 132→134 in 4 min; serial cannot keep pace with production, so
re-do the arithmetic **before** more rebasing. The flaw in CORRECTION 1 is that it treated **all** of serial
as soft-green-linearized. It is not. **soft-green only inherits green on a CLEAN rebase; a conflict-resolving
rebase VOIDS it** (see [[rebase-wrapper-soft-green-confidence-levels]]). So the backlog splits into two
regimes, and the answer is different in each.

### The real structure (glue-filtered conflict graph, `experiments/hermit-coalesce-conflict-graph_20260804`)
Naive file-overlap = **one blob of 100** (every parity PR appends the same ~7 registry files). After
**glue-filtering** those append-only files (resolved ONCE by the registry-union merger, prototyped 12/12 at
`experiments/staging-drain-registry-merge_20260804`), the real structure of 71 main-targeted PRs is:
- **33 FREE singletons** — conflict with nothing on real source. soft-green makes these ~free serially.
- **Component B (15)** — the `tests/backend-parity/run_matrix.py` chain; bound by exactly ONE real file.
- **Component A (23)** — core+CI-infra+patching entanglement; holds **7/8 patching PRs → land LAST** (owner).
- B and A share no real-conflict file → **parallel lanes**. Clustering cost is ~one fetch (3s) + 15s probes.

### Two regimes — soft-green helps ONE of them
| set | soft-green? | serial cost | staging cost |
|---|---|---|---|
| **33 free singletons** | YES (clean rebase inherits) | ~linear, ~free re-rebases | no advantage |
| **Component B (15)** — 1 shared real file | **NO** (conflict-resolve voids green) | **C strictly-serial validates** (each landing forces the rest to conflict-rebase→void→re-validate; the landing lock forbids parallelism) | **1 validate + bisect-on-red** |
| **Component A (23)** — patching, land LAST | **NO** | C strictly-serial validates | 1 validate + bisect (separate lane) |

### Per-component crossover (this is the number the owner asked for)
- Staging(C) ≈ `1 + p_red·log₂(C)` validates (keep_going DAG gives per-step attribution ≈ free when the red
  self-names, as the 12:49 lint did; worst case a real bisect is log₂C).
- Serial(C) ≈ `C` strictly-serial validates on a conflict component (soft-green void).
- Crossover `1 + log₂(C) < C` ⇒ **C ≥ 3. Staging wins for any conflict component of ≥ 3 PRs.**
- Our components are **15 and 23** ⇒ staging wins there **~3–6×**: B is 1–5 validates vs 15; A is 1–5.5 vs 23.

### Whole-backlog, corrected (grouped staging vs disciplined serial)
- **Grouped staging** = 33-free lane (1 validate) + B lane (1 validate) + A lane (1 validate, deferred) +
  one-time glue merge + bisect budget ≈ **3–9 validates total, O(1) in N, 2 lanes parallel** ≈ 0.8–6 h.
- **Disciplined serial** = 33 free cheap (soft-green) **+ 38 conflict-component validates STRICTLY SERIAL**
  (B 15 + A 23, cannot parallelize: landing lock + conflict chains) at warm-green 926 s ≈ **~9.8 h for the
  conflict set alone**, during which production adds ~12 more.

### VERDICT — corrected, both ways stated plainly
1. **Staging WINS on the conflict-bearing value set (Components B + A)** — decisively, crossover C≥3, our
   components 15/23. The owner's superlinear-serial intuition is **correct for exactly this set**: soft-green
   is void where PRs share real conflict files, so serial pays C strictly-serial validates. CORRECTION 1's
   "no clear winner" was wrong because it assumed soft-green linearized all of serial.
2. **Staging offers no advantage on the 33 free singletons** — soft-green already makes those cheap serially.
   These are also the low-value set (many drafts); the READY value concentrates in B + the patching-heavy A.
3. **Staging is NOT the gating lever.** Both strategies red on the **DynamoRIO build SPOF** (deterministic,
   not retryable; staging hits it HARDEST — cold + max build surface). **Empirically the queue grew 106→134
   today because NOTHING drained** — that is the SPOF, not an N² artifact. **Fix the SPOF first**; it gates
   both. Owned by hermit-dbi/hermit-perf (`build.workspace --features third-party-backends` on 31 dependents;
   fix = `--exclude` the 3 crates, see [[dynamorio-gates-31-portable-nodes-via-build-workspace-feature]]).
4. **The owner's clustering mitigation WORKS but ONLY after glue-filtering.** Without the registry-union
   merger it collapses to one blob of 100 (mitigation defeated). With it: 3 groups, blast-radius of a red
   bounded to ≤23 (A), ≤15 (B), or 1 (a free singleton). Grouping bounds ambiguity; it does not erase the
   flaky/SPOF-red-vs-culprit-red ambiguity without a re-run.

### ACTIONABLE SEQUENCE (in dependency order)
1. **Fix the DynamoRIO build SPOF** — gating for BOTH strategies; nothing drains until it lands. [dbi/perf]
2. **Apply the registry-union merger once** (`experiments/staging-drain-registry-merge_20260804`, 12/12) to
   resolve the ~7 glue files at the staging tip.
3. **Stage Component B (15) + READY free singletons** as one conflict-free branch; ONE validate; land.
4. **Component A + the patching cluster LAST**, as a separate lane (owner-designated ordering).

---

## FINAL CROSSOVER (lander, 2026-08-04T21:1xZ) — the number, with the owner's fresh inputs

Owner-supplied inputs (this spawn), superseding all blends above:
- **WARM validate = 500s** (median, n=96). Best warm seen today = **338s** (`8fa1d468`).
- **COLD validate = 719s** (median, n=16). A fresh staging branch runs COLD → priced at **719s**, not 500s.
- **Orphan rate = 100%** (measured): every landing invalidates the SHA-keyed receipts behind it.

### The two cost models

**SERIAL** (owner's model): `N × warm  +  the re-validates each landing's orphaning forces`.
With 100% orphan, landing the k-th PR orphans the ~ (N−k) still queued → they re-validate.
That is the triangular sum: **N(N+1)/2 warm validates** (first one cold):
`SERIAL(N) = 719 + (N(N+1)/2 − 1)·500`.

**STAGING**: `1 rebase (≈free) + 1 COLD validate + expected bisect on red`.
Bisect budget ranges from **0** (the keep_going DAG self-named the culprit in 0.4s on the 12:49 run — attribution was free) to a worst-case **log₂N cold** re-runs:
`STAGING(N) = 719 · (1 + b),  b ∈ [0, log₂N]`.

### Crossover N (STAGING < SERIAL)

| N | SERIAL (quad, 100% orphan) | STAGING best (b=0) | STAGING worst (b=log₂N) | winner |
|---|---|---|---|---|
| 1  | 719      | 719   | 719   | tie |
| 2  | 1,219    | 719   | 1,438 | **staging** (best); tie-ish worst |
| 3  | 2,219    | 719   | 1,859 | **staging** |
| 5  | 7,219    | 719   | 2,388 | **staging** |
| 10 | 27,219   | 719   | 3,108 | **staging** |
| 21 | 110,000  | 719   | 3,876 | **staging** (~28–150×) |
| 42 | 452,000  | 719   | 4,596 | **staging** (~98–600×) |

**CROSSOVER = N ≥ 2.** Against the owner's stated (measured, 100%-orphan) serial model, staging wins
for any backlog of 2 or more PRs, and the margin explodes quadratically. The current backlog is
**42 ready / 76 open** — three to four orders of magnitude past crossover.

### The one model where the crossover is higher — and why it doesn't rescue serial here
If serial avoids the orphan penalty (single-front lander that validates ONLY the front PR, or a clean
soft-green rebase that *inherits* green), serial is **linear**: `SERIAL_lin(N) = 719 + (N−1)·500`.
Against that, staging's crossover is **N≈2** (best-case bisect) to **N≈4** (worst-case full bisect).
Still tiny. So even under the most charitable serial model, staging wins by N≈4, and we have N=42.

**There is no N in this backlog at which serial wins.** Staging wins. Said plainly.

### The honest caveat that actually gates this (not the crossover)
The crossover says "stage it." The **blocker is not the strategy — it's the DynamoRIO build SPOF**
(`build.workspace --features third-party-backends` forced on 31 dependents; cc1plus OOM / 0-byte
`.o` link). BOTH strategies red on it; **staging hits it hardest** (cold + max build surface → the 70%
cold-fail rate is largely this deterministic, non-retryable break). And a *flaky/SPOF* cold red is
NOT self-attributing the way the 12:49 lint was — that free attribution was a property of that specific
failure, not a guarantee. So: **the crossover math endorses staging unconditionally; the operational
precondition is landing the DynamoRIO isolation fix (PR #1607) first.** Until then a staging validate
reds on a known cause and yields no new information.
