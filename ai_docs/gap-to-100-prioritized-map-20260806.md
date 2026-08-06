# Gap-to-100: the prioritized burndown toward maximally-strict parity

**Task:** `gap-to-100-prioritized-map` · **Date:** 2026-08-06 · Local, no egress, no validate run.
Every number below carries its provenance (#268): how it was obtained, and its denominator.

---

## 1. The reframe: this map is blocker-major, not cell-major

The task asks to "enumerate every program×backend×mode cell not yet green". I am deliberately
not delivering that list, and the reason is the finding:

> **The cells are not independently red. ~All of them are gated by five structural blockers,
> and four of those blockers are not per-program at all.**

A 895-row red list would imply 895 units of work. The measured truth is closer to **five
units of work, after which the per-cell burndown can begin for the first time.** Publishing
the row list first would misdirect the entire dispatch.

Concretely: DBI is 0/6 on full detlog parity and **not one** of those zeros is attributable
to guest behaviour — all six fail for the same three plumbing reasons. Enumerating 179 DBI
cells would produce 179 rows whose "why" column is identical.

---

## 2. The five structural blockers

| # | Blocker | Gates | Provenance |
| --- | --- | --- | --- |
| **B1** | **No cross-backend comparator exists.** `compare_two_runs` has exactly 2 live callers, both same-backend (run-twice; record-vs-replay). | **895/895 cells** — the parity axis cannot be *computed* by the product | observed, source read at hermit `b64d893a`; `run.rs:2759`, `record_start.rs:452` |
| **B2** | **DBI: `dtid` is the raw host TID.** 7 distinct values across 7 runs vs ptrace's constant `dtid 3`. Every DETLOG line carries a dtid ⇒ every line differs, and differs from DBI's *own* previous run. | all DBI cells (179) — parity **impossible by construction** | observed, 2026-08-06, `experiments/dbi-strict-parity_20260806/` |
| **B3** | **DBI: `--log-file` ignored; log goes to stderr, with no wall-clock prefix.** ptrace 85 149 B logfile / 0 stderr; DBI file absent / 375 DETLOG on stderr. No prefix ⇒ `extract_log_messages` collapses the stream to 1 message. | all DBI cells | observed, same artifact |
| **B4** | **Heap domain is the brk segment only** — captures **0.2%** of a program's non-exec anonymous memory (264 KiB of 106 608 KiB on a probe doing the three standard allocation shapes). Flags default off and no collector passes them. | the heap leg of **all** cells; 0/1200 anchor rows carry memory evidence | observed, 2026-08-05, `experiments/…heap-domain…`, host-native probe |
| **B5** | **KVM cannot complete a guest.** `--backend kvm run /bin/true` → CPU-timeout at 31 s, dCPU/dWall ≈ 1.0 (burned core). 5th confirmation; first at `b64d893a`. | all KVM cells (179) | observed, 2026-08-05, boxed cgroup measurement |

**Two enabling facts that are *not* blockers**, and change the order:

- **The shipped checker is sound.** `hermit log-diff` default mode: **6/6 mutants killed**
  (heap hash, stack hash, dropped record, reordering, syscall numeric result, syscall hex
  arg), **2/2 controls** correctly tolerated. *(observed, `experiments/parity-checker-mutation_20260806/`)*
  So no work is needed on the comparator itself — only on which mode consumers select.
- **`Stripped` mode costs exactly 2 of those 6.** Same mutants under `--unsafe-strip-lines`:
  **4/6**, and the two survivors are precisely the syscall-value class. Since bare
  `--verify` selects `Stripped`, that is the measured cost of the default. *(observed, same artifact)*

---

## 3. Per-backend state, with provenance

Denominator throughout: **179 ptrace-passing tests** (of the 200-test anchor; the live
corpus is now 235). "stdout-parity" is the legacy metric — sha256 of guest stdout only.

| backend | stdout-parity | full-detlog parity | blocking | provenance |
| --- | --- | --- | --- | --- |
| **e9patch** | 172/179 (96%) | **PASS** (172\|172 msgs, 0 diffs) — the project's **first** cross-backend full-detlog pass | — but see caveat | observed 2026-08-06, `experiments/e9patch-parity_20260806/` |
| **sabre** | 141/179 (79%) | unmeasured | stderr-DETLOG routing (already has a rescue: `extract_sabre_detlogs`) | anchor CSV, observed |
| **dbi** | 136/179 (76%) | **0/6 measured** | **B2, B3** | observed 2026-08-06 |
| **kvm** | 112/179 (63%) | unmeasurable | **B5** (cannot run) | anchor CSV + observed livelock |
| **liteinst** | 108/179 (60%) | unmeasured | unknown — never probed | anchor CSV only |

**The e9patch caveat, which matters for how the matrix is totalled.** e9patch is *not a
backend* — the banner says `e9patch preprocessing + ptrace runtime`. It inherits ptrace's
logging and determinization wholesale, which is exactly why it has none of DBI's gaps. And
on the tested guest it reported `candidate_sites=0; mapped_sites=0` — **it rewrote
nothing**. So the pass currently establishes *"the e9patch path preserves parity when it
does no rewriting"*, which is weaker than *"e9patch preserves parity"*. **Open, ~5-minute
job:** re-run on a guest with `mapped_sites > 0`.

**Compounding fact:** all five backends load the **same Detcore**. Parity between them is
blind to any Detcore-level defect *by construction* — so even a 100% parity matrix would
not be evidence that determinization is correct. That is a separate axis (correctness
oracles), currently **15/895** oracle-qualified, all 3 of them `meminfo`.

---

## 4. Prioritized burndown

Ordered by *cells unblocked per unit of work*, not by cell count.

| rank | action | unblocks | why here |
| --- | --- | --- | --- |
| **1** | **B2 — determinize DBI `dtid`** | 179 DBI cells | Cheapest single fix with the largest blast radius. Nothing else moves DBI while every line carries a host TID; fix this and B3 becomes mechanical. |
| **2** | **B3 — route DBI's log through `--log-file`** | same 179 | Precedent exists: `extract_sabre_detlogs` (`run.rs:75-98`) already does this for SaBRe. Prefer fixing routing for both over a second bespoke rescue. |
| **3** | **Measure SaBRe and LiteInst** (2 × ~30 min) | converts 2 of 5 backends from *unknown* to *known* | Cheapest information gain on the map. LiteInst has **never been probed** — its 108/179 is stdout-only. Do this before investing in either. |
| **4** | **B1 — build the cross-backend comparator** | makes the parity axis *computable* in-product | Bigger than 1–3 and can proceed in parallel; the harness in `experiments/dbi-strict-parity_20260806/compare.py` is a working reference implementation. |
| **5** | **B4 — fix the heap domain** (guest-allocated pages, provenance rule) | the heap leg of all cells | Currently every heap cell is a no-result; a naive "turn the flags on" would report a large **fake pass** (0.2%-full domain matching trivially). Must not precede the domain fix. |
| **6** | **B5 — KVM startup livelock** | 179 KVM cells | Highest cell count but lowest confidence of a quick fix, and untested at current main `f89c6976` whose top commit touches the exact scheduler path. **Re-probe at main before investing** — that check is ~10 minutes and may retire this whole row. |
| **7** | per-cell burndown | the actual residual | Only meaningful after 1–6. Until then, per-cell reds are explained by the blockers above, not by program behaviour. |

---

## 5. What "100%" must mean

A single parity percentage cannot express the goal, for two independent reasons already
measured:

1. **Vacuity.** stdout-hash equality has no negative side — two backends producing nothing
   match. The legacy 60–96% column is that metric.
2. **Shared-bug blindness.** Equivalence to ptrace cannot detect a bug ptrace shares — and
   all five backends share Detcore, so this is the default case for any determinization
   defect, not an edge case.

So the burndown target is **two numbers per cell**: bitwise-qualified (currently **0/895**)
and oracle-qualified (currently **15/895**). Today **high-confidence — both — is 0/895**.
Expect the legacy percentages to **fall** when recomputed honestly; that is a correction,
not a regression.

---

## 6. Limitations

- DBI's 0/6 and e9patch's PASS are **6 and 1 guests** respectively, not corpus sweeps. They
  are cited as *structural* findings (identical failure mode across all cells tested), not
  as corpus percentages.
- SaBRe and LiteInst full-detlog parity are **unmeasured** — I list them as unknown rather
  than inferring from their stdout numbers.
- stdout-parity figures come from the frozen 200-test anchor (`fullcorpus-scorecard.csv`,
  hermit `82a8e853`); the live corpus is 235 tests, so those percentages are stale in
  denominator even where the ratio holds.
- All measurements used `worktrees/covnode/hermit` @ `fc49593ac`, not current main
  `f89c6976`.
- "mode" in program×backend×mode was not enumerated as a separate axis: every measurement
  here is `--strict` + `--verify`-class. record/replay and chaos modes are out of scope of
  what I measured.
