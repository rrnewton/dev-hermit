# Backend RB-Readiness Assessment — 2026-08-04 (recurring #126/#127)

**Author:** `hermit-ci` (coordinator, opus-4.8). Research/assessment only — **no code inflow**.
**Question:** which non-ptrace backend is closest to *RB-ready* — i.e. can **deterministically run a
real reproducible build FASTER than ptrace** (the perf-graduation path off ptrace)?

> **RB-readiness is NOT compat percentage.** It is a conjunction:
> (1) **in-guest syscall path** (no per-syscall ptrace round trip — otherwise zero speedup), AND
> (2) **build-capable** (runs a real multi-process build tree: exec + fork + reap at parity), AND
> (3) **deterministic byte-identical output**, AND
> (4) **net wall-clock faster than ptrace on a compile-heavy workload** (must be MEASURED, not assumed).
> A backend at 89% corpus that cannot run a build, or that still traps every syscall to ptrace, is **not ready**.

Every number below carries its **date + derivation + source SHA**. This assessment **re-measured from source**;
it does not quote the 2026-08-03 note (whose headline figures are refuted — see §3).

---

## 1. Compat envelope — re-measured live 2026-08-04

**Source:** `compat-envelope/fullcorpus-scorecard.csv` (parent commit `d83a34b3`, Aug 3; data measured at
hermit `f80b1c09` / reverie `04a46b43`, Aug 4). **N = 205 cells/backend**, all `test_mode=verify`.
KVM is **not** in this file — its data is a separate, 3-days-older sweep
(`experiments/kvm_fullcorpus_scorecard_20260801/scorecard-kvm-full.csv`, hermit `82a8e853`, N=200).

Counting method (re-tallied from raw rows): det-pass = `deterministic==1`; parity blank ("NA") = unassessable
(22 rows/backend), so the parity-applicable denominator is **183** (184 for KVM).

| Backend | det-pass /205 | parity /205 | parity /applicable | measured-at (hermit SHA / date) |
|---|---|---|---|---|
| ptrace (ref) | 182 = 88.8% | reference | n/a | f80b1c09 / Aug 4 |
| **e9patch** | 182 = 88.8% | 181 = 88.3% | **181/183 = 98.9%** | f80b1c09 / Aug 4 |
| **dbi** | 158 = 77.1% | 144 = 70.2% | 144/183 = 78.7% | f80b1c09 / Aug 4 |
| **sabre** | 153 = 74.6% | 141 = 68.8% | 141/183 = 77.0% | f80b1c09 / Aug 4 |
| **liteinst** | 121 = 59.0% | 118 = 57.6% | 118/183 = 64.5% | f80b1c09 / Aug 4 |
| **kvm** | 130/200 = 65.0% | 112/200 = 56.0% | 112/184 = 60.9% | 82a8e853 / Aug 1 (older) |

**Backend-parity matrix** (`hermit/tests/backend-parity/matrix.tsv`, hermit `8f656b4d`, Aug 4; 28 tests,
**only ptrace/dbi/kvm**): ptrace 28/28 L1+L2; dbi 27/28 L1, 27/28 L2 (gap: pthread_lifecycle DynamoRIO
startup stall); kvm 23/28 L1, 22/28 L2.

---

## 2. Architecture gate — does the syscall path require a ptrace round trip?

**This is the decisive RB-readiness axis** (constraint 3): a patching backend whose Detcore syscall handling
still trips a per-syscall ptrace stop gets **zero per-syscall speedup** and is not perf-ready *regardless of
its compat %*. Source of truth: `reverie/BACKENDS.md` mechanism matrix, verified against code.

| Backend | Detcore syscall path | Class | Evidence |
|---|---|---|---|
| **DBI** | DynamoRIO DBT client, **in-guest**; coordinator RPC | **A (in-guest)** | `detcore-dbi/src/lib.rs:1281` pre-syscall cb; `backends.rs:11-16,412-434`; BACKENDS.md DBI row |
| **KVM** | hypercall + KVM VM-exit, in-process GlobalState | **A (in-guest)** | `reverie-kvm/src/error.rs:88-102`; `lib.rs:9-14`; BACKENDS.md KVM row |
| **SaBRe** | in-guest `.text` rewrite → plugin in guest ctx + RPC, **BUT always-on `PTRACE_SYSCALL` safety net** | **A + ptrace-supervisor HYBRID** | `detcore-sabre/src/lib.rs:9-290`; `sabre_ptrace.rs:9,165,337-408` |
| **e9patch** | AOT rewrite → SIGTRAP to **ptracer**; Detcore path 100% ptrace-hosted | **B (ptrace)** | `run.rs:1714-1720` (`runtime_backend()`→Ptrace); `reverie-e9patch/src/lib.rs:210-211` |
| **liteinst** (Hermit Mode B) | **ptrace host owns Detcore**; preload = hot-site hints only | **B (ptrace)** | `reverie-liteinst/src/backend.rs:199-237`; `hermit-cli/src/lib.rs:1531-1546` |

**Build-capability (real process trees):**
- **DBI** — yes: fork-reconnect wire client; proven fork→execve→reap at ptrace parity, L2 byte-identical.
- **SaBRe** — yes: supervisor tracks the full tree (`PTRACE_O_TRACECLONE|FORK|VFORK|EXEC`, `sabre_ptrace.rs:337-347`).
- **KVM** — **no**: bounded single-process static-ELF personality (`reverie-kvm/src/lib.rs:9-14`); execve/spawn
  partially implemented and `/proc/self/exe` now virtualized for the root (`vm.rs:588`) — so the older
  "proc/self/exe not virtualized" finding is **partially stale** — but it is "not a general Linux guest kernel"
  and real multi-process build trees are out of scope.
- **e9patch** — n/a (AOT preprocessing, not a runtime backend).
- **liteinst** — **no**: fork/vfork/clone **fail closed** before either side resumes (`backend.rs:204`);
  single-process/single-thread only. A perf-capable in-guest "Mode A" exists but is **unused by Hermit** and
  also cannot run trees (RCB clock fixed at 0, no exec-bootstrap; `reverie-liteinst/CLAUDE.md`).

---

## 3. B-level correction — prior "DBI B3 = 130/152 = 85.5%" is REFUTED

Refuted three independent ways against live data (2026-08-04):
1. **The "152" corpus does not exist** anywhere in compat/parity data (live full corpus = 205 / 200). `152`
   appears only in unrelated perf `raw.tsv` files.
2. **DBI is not 130.** DBI det = 158, parity = 144 (of 205). `130` is **KVM's** det count (130/200) — a
   backend conflation.
3. **No backend is B3 under the authoritative model** (`ai_docs/backend-maturity-model.md`, `d83a34b3`):
   B3 = "majority parity (≥50% of the frozen full corpus) **AND** requires B2.4 (full compat envelope)" — a
   threshold **+ gate**, not a percentage→level map. Highest **proven** level = **ptrace B2.1**; dbi/sabre/kvm
   = **B2 base**; e9patch & liteinst not in the levels table. The doc explicitly warns *"parity rates are
   diagnostics for B2.1; they are not B3 measurements."*

### 3a. B-levels RE-DERIVED from the gate ladder against live data (2026-08-04)

Applying the maturity-model gate criteria to the live artifacts (NOT the doc's stale 07-28 "current levels"
table). The **B2.1 gate is the examples cross-backend scorecard** (all of `examples/` = `date.sh, devrand.sh,
race.sh, rand.py, timed-progress-bar.py` must be parity-clean vs ptrace). Live examples-parity from the Aug-4
fullcorpus scorecard (hermit `f80b1c09`):

| Backend | Examples parity (B2.1 gate) | Freshly-provable level @ date/SHA | Binding gate (failing live number) |
|---|---|---|---|
| ptrace | 5/5 (reference) | **B2.1** @ Aug 4 / `f80b1c09` | B2.2: only 163/181 C cells pass `--strict --verify` |
| e9patch | 5/5 | **B2.1** @ Aug 4 / `f80b1c09` *(caveat below)* | B2.2: 163/181 C cells |
| sabre | 4/5 (fails `example-date`) | **B2 base** @ Aug 4 / `f80b1c09` | B2.1: example-date parity=0 |
| dbi | 3/5 (fails `example-race`, `example-date`) | **B2 base** @ Aug 4 / `f80b1c09` | B2.1: 2 examples diverge |
| liteinst | 2/5 (fails race/date/devrand) | **B2 base** @ Aug 4 / `f80b1c09` | B2.1: 3 examples diverge |
| kvm | not freshly evaluable | **B2 base** @ Aug 1 / `82a8e853` | B2.1 gate **not evaluable** — Aug-1 KVM artifact lacks the `example-*` rows; Aug-4 scorecard excludes KVM |

**Verdict (freshly derived, 2026-08-04): no backend is above B2.x.** Ceiling = **B2.1**, held only by ptrace
(reference) and e9patch. **B3 is structurally blocked for every backend** because B3 *requires B2.4* (full
envelope, selected==expected) and B2.4 is unmet — no backend passes even 100% of the C subset (best is
163/181), and B2.4 completeness is not fully evaluable from the current scorecard buckets. So every parity
figure (e9patch 88%, dbi 70%, sabre 69%, liteinst 58%, kvm 56%) is a **diagnostic rate, not a conferred level**.

> **e9patch B2.1 caveat:** e9patch's 5/5 examples + 88% parity mirror ptrace's passes, and its Detcore path is
> 100% ptrace-hosted (§2). The B2 gate explicitly **fails on silent ptrace fallback**; the scorecard alone
> cannot prove e9patch instrumentation was actually active rather than falling through to ptrace. Its B2.1 is
> therefore provisional pending an active-instrumentation disclosure check. **This is the sharpest form of the
> constraint-4 point: the backend that LEADS on B-level and compat is the one architecturally disqualified for RB.**

*B-levels re-derived by walking the gate ladder (`ai_docs/backend-maturity-model.md`, parent `d83a34b3`)
against the live Aug-4 fullcorpus scorecard + parity matrix; the doc's own "current levels" table (hermit
`adbfaca3`, 2026-07-28) was NOT quoted.*

---

## 4. RB-readiness scorecard (the decision quantity)

| Backend | in-guest syscall path? | build-capable? | deterministic (L2)? | RB-ready? |
|---|---|---|---|---|
| **DBI** | ✅ (DBT) | ✅ fork→exec→reap, L2 proven | ✅ (m4/multiprocess_fork_exec byte-identical) | **Closest — needs net wall-clock measurement** |
| **SaBRe** | ⚠️ in-guest **+ always-on ptrace supervisor** | ✅ | partial (77% applicable parity) | Best patching candidate; **blocked on retiring the ptrace safety net** |
| **KVM** | ✅ (VM-exit) | ❌ single-process static ELF | ✅ within its bound | No — cannot host a build tree |
| **e9patch** | ❌ ptrace-hosted Detcore | n/a (AOT) | high (98.9%) but via ptrace | **No — highest compat, zero runtime speedup** |
| **liteinst** | ❌ ptrace-hosted (Mode B) | ❌ fork fail-closed | — | No — zero speedup + no trees |

---

## 5. Recommendation

**DBI is the single backend closest to RB-ready.** It is the only non-ptrace backend that is *both*
in-guest on the syscall path *and* build-capable with proven L2 byte-identical fork→execve→reap (the build
process-tree contract). **SaBRe is the strongest patching-family perf-leader candidate** (the gVisor-systrap
analog: Detcore runs in-guest via `.text` rewrite + RPC) but is **architecturally gated** by its always-on
`PTRACE_SYSCALL` supervisor — it is not yet zero-ptrace-per-syscall, so it is a *candidate*, not scored ready.

**e9patch is the poster child for constraint 4:** highest compat in the whole matrix (98.9% applicable parity)
yet **not RB-ready at all**, because its Detcore path is 100% ptrace-hosted (zero per-syscall speedup) — compat
% is the wrong axis. **liteinst** is likewise ptrace-bound (Mode B) and fails closed on fork. **KVM** is in-guest
but confined to a single-process static-ELF personality.

### The honest blocker before any "DBI is RB-ready" claim
1. **NET WALL-CLOCK IS UNMEASURED.** Prior notes cite DBI ~16.5× faster/syscall but ~11× slower on
   branch-bound compute. On a compile-heavy build the *net* wall-clock could go either way and **must be
   measured**, not assumed. This assessment does not re-measure perf (no builds; research-only).
2. **uname/hostname leak** (prior finding, unverified this pass): detcore `misc.rs:592` — DBI reports
   `has_uts_namespace=true` but never sets the UTS hostname → could break byte-identical output if a build
   embeds the host FQDN. Verify before an RB trial.

### Next step for a follow-up *implementation* task (not this research task)
`hermit run --backend dbi --no-namespace` on a pre-pidfd (nix 2.3.16) self-contained derivation, `-j1
--cores 1`, witness = sha256 of output across N=3 fresh `/nix/store` runs, **recording net wall-clock vs
ptrace**. Evaluate SaBRe in parallel (with the ptrace-supervisor tax noted). Modern nix needs
`pidfd_send_signal` (detcore-Unsupported) and real-package builds are egress-blocked offline — use a
self-contained derivation.

---

## 6. Confidence / staleness flags

- **Cross-SHA composition:** the 5-backend fullcorpus (`f80b1c09`, Aug 4) and KVM (`82a8e853`, Aug 1) are
  different SHAs — KVM's row is not directly comparable; do not place it on the same measurement line without this note.
- **Date-inversion:** fullcorpus CSV committed to parent Aug 3 but its embedded `hermit_sha` has commit date
  Aug 4 (post-hoc rebase / clock skew) — treat exact measurement time with mild caution.
- **`backend-maturity-model.md` "current levels" table** is a stale snapshot (hermit `adbfaca3`, 2026-07-28,
  computed on the 5-example set) relative to the 205-row live sweep; its *definitions* are current, its *level
  assignments* predate the newest data.
- **matrix.tsv** covers only ptrace/dbi/kvm (28 tests) — not a substitute for the 205-row envelope; omits
  e9patch/sabre/liteinst.
- **`parity` blank ≠ pass** — report parity out of the applicable denominator (183/184) when a parity-clean
  *rate* is the decision quantity.

---

*Method: two source-grounded re-measurement passes on 2026-08-04 — (a) live re-tally of the fullcorpus
scorecard CSV + parity matrix with per-figure SHA/date provenance, and (b) a syscall-path architecture audit
against `reverie/BACKENDS.md` and backend code. No builds run; no code changed. Parent HEAD at authoring: see
commit.*
