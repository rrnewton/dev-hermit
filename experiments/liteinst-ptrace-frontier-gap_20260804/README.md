# LiteInst → ptrace compat-coverage frontier gap (measurement only)

- **Date:** 2026-08-04 (UTC)
- **Parent HEAD:** d13ed42d1b3c22738a3f0b03323df6a7079f3353
- **Hermit primary HEAD:** b384187efd725c504d69281f043d442325d4fcb2
- **Task:** `liteinst-lane-restaffed-ratchet-toward-ptrace-envelope`
- **Scope:** MEASUREMENT AND ENUMERATION ONLY. No product source edited, no coverage
  added, no corpus expansion. Compat expansion is PAUSED for liteinst.

## Question

Precisely what compat-coverage cells does the golden ptrace backend cover (pass at L2)
that the liteinst backend does NOT, so the liteinst ratchet is ready the moment the
compat pause lifts?

## Method (no full re-sweep — used recorded scorecard + cheap inspection)

1. Read prior recorded numbers (memory: `liteinst-compat-ratchet-lane-saturated`,
   `liteinst-flagship-inguest-multiproc-state`, `dbi-compat-frontier-denominator-remeasured-20260804`,
   `compat-envelope-scorecard-system`, `backend-parity-matrix-l2-verify-lift`).
2. Confirmed the committed full-corpus scorecard denominator + backend det counts by
   direct read of `compat-envelope/fullcorpus-scorecard.csv` (1200 data rows = 6
   backends × 200 cells; `deterministic` field=col14, `parity` field=col15).
3. Computed the GAP = join on `(bucket|test_id|test_mode)` of cells where
   `ptrace deterministic==1 AND liteinst deterministic==0`.
4. Confirmed current corpus size from `compat-envelope/corpus/*.tsv`.
5. Checked in-flight liteinst PRs (`gh pr view 1397`).

## Denominator (stated exactly)

**ESTABLISHED — committed measurement (STALE on two axes, see below):**
LiteInst **L2 determinism = 118/200 (59%)**, **parity = 108/200** on the 200-cell
full-corpus scorecard, hermit **82a8e853** (2026-08-01), release binary, `--strict
--verify` per cell, recorded in `compat-envelope/fullcorpus-scorecard.csv`
(deterministic field). Reference column in the same file: **ptrace L2 = 179/200 (89.5%)**.

- The **parity 108** figure is *piped-stdout-SHA-256 equivalence only* — an UPPER BOUND
  on true parity, blind to INFO/detlog-stack/detlog-heap and all tty-conditional
  divergence (ESTABLISHED, `compat-envelope-scorecard-system` code review of
  `collect-envelope.rs::run_and_hash`). Do not headline it as full parity.
- Measurement was **recorded** (`hermit run --strict --verify` double-run per cell),
  not sampled.

**STALE on TWO axes (same failure mode as the DBI 130/152 & 156/200 figures):**
1. **Corpus axis:** the curated sweep corpus has grown **200 → 235 cells (+35)**.
   Current `compat-envelope/corpus/corpus-c.tsv` = 214 C + `corpus-nonc.tsv` = 21 non-C
   = **235**. The +35 (new `performance/` syscall-churn microbenchmarks ×30 + 5
   `example-*` cells) are **UNMEASURED for liteinst**. So `118/200` is a denominator
   that no longer exists. (ESTABLISHED: `wc -l corpus/*.tsv` = 235.)
2. **HEAD axis:** the scorecard is keyed to hermit `82a8e853` (2026-08-01); current
   hermit primary HEAD is `b384187e`. The landable liteinst backend was re-measured at
   `d973cc63` (2026-08-02) = **det 108/200 (54%)** — 10 fewer than the 82a8e853 row,
   because the 82a8e853 flagship carried confounds; the landable host-hybrid backend
   is the honest column. (ESTABLISHED, memory `liteinst-compat-ratchet-lane-saturated`.)

## Box-blocked denominator (flagged, not guessed)

The **real parity denominator** — the count of ptrace-passable cells on the *current
235-cell* corpus — is **BOX-BLOCKED in the agent sandbox**, exactly as the DBI
parity-denom is. It requires a ptrace `--strict --verify` sweep over all 235 cells,
which the sandbox cannot box (BpfJailer denies self-created cgroups; needs the
`systemd-run --user` producer path on a quiet host). Last measured ptrace L2 = 179/200
@82a8e853. **Do not quote a liteinst parity% against 235 until that ptrace re-sweep
runs.** HYPOTHESIS: ptrace-passable on 235 ≈ 179 + most of the +35 microbenchmarks
(they are single-threaded churn loops ptrace handles) ⇒ ~200-210; unverified.

## The GAP: ptrace covers (L2 det), liteinst does NOT

**ESTABLISHED: exactly 61 cells** where ptrace `deterministic==1` and liteinst
`deterministic==0`, computed by join on the committed 200-cell scorecard. This
**cross-checks** the independent 2026-08-02 landable re-measurement (82 liteinst det=0
reds − 21 also-ptrace-red = **61 liteinst-specific**). Both methods → 61. All 61 carry
liteinst `reason=liteinst-verify-fail-exit1` (generic; the real signatures below come
from the 2026-08-02 `--log` repro, ESTABLISHED there).

| category | count | root cause | disposition |
|---|---|---|---|
| MT / thread-clone | ~32 | `clone`/`clone3` → `-524 ENOTSUPP`; liteinst rejects app-created threads (Hermit permits ≤1 guest thread). ToolHost process-wide spinlocks held across blocking RPC. | **OWNER-GATED** flagship **#1466** thread-lifecycle. Single biggest unlock (~70% of gap). |
| wait/reap mediation | ~8 | non-root parent blocking in `wait4`/`waitid` deadlocks at teardown; `TracerBuilder::<()>` lifecycle supervisor only re-injects the ROOT wait. | **OWNER-GATED** (subset of #1466 lifecycle; nested-fork wait/reap gap). |
| time / vDSO + interpreter | ~12 | in-guest vDSO clock leaks host wall-clock (fast path issues no syscall instruction → never trapped); + shell/interpreter/subprocess-time cells; socket-timestamp ns-fraction is intrinsic cross-backend. | **OWNER-GATED** (vDSO interception-model change) / intrinsic. |
| exec | 3 | `dbi-execveat-unsupported`, `record-replay-fd-close`, `vforkexec` — post-start exec denied; needs persistent protected bootstrap FD. | **OWNER-GATED** exec bootstrap. |
| nostdlib | 3 | `hello-nostdlib`, `pread64-nostdlib`, `racewrite-nostdlib`: `-nostdlib -static -no-pie` → no dynamic loader → LD_PRELOAD handshake can't complete. | **ARCHITECTURAL INTRINSIC** to preload-based instrumentation. |
| ptrace-guest | 2 | `ptrace-attach-eperm`, `ptrace-seize-eperm`: guest-issued ptrace EPERM parity. | intrinsic/backend. |
| arch_prctl | 1 | `arch-prctl-determinism`: liteinst runtime owns `%gs` base for dispatch, so guest `ARCH_SET_GS` doesn't stick (backend reports `gs_base=0`). | **FIXED IN-FLIGHT** — see cheapest next cell. |

Category boundaries within MT vs wait/reap vs time are name-based (HYPOTHESIS at
per-cell granularity); the aggregate MT-dominance (~42 of 61 when wait/reap is folded
into MT), the ENOTSUPP/nostdlib/exec/arch_prctl signatures, and the 61 total are
ESTABLISHED via the 2026-08-02 `--log` repro + the scorecard join. Full 61-cell list:
`gap-cells.txt` (this dir).

## Cheapest next cell (when the pause lifts)

**`c-programs/arch-prctl-determinism` — ESTABLISHED, and it is ALREADY IMPLEMENTED
in-flight as PR #1397.**

- PR #1397 `codex/liteinst-full-corpus-scorecard` "Preserve LiteInst arch-prctl GS
  state" — OPEN, not draft, MERGEABLE, **+49 / -4 across 3 files, all Detcore**
  (`detcore/src/{lib.rs,syscalls/misc.rs,tool_local.rs}`), NOT liteinst-backend source.
- Measured cell delta in the PR: `arch-prctl-determinism` parity 0/det 0 → **parity
  1/det 1** (dynamic-ELF, single-process, single-thread liteinst scope).
- Mechanism: Detcore shadows the requested `ARCH_SET_GS` value per-thread and returns
  it from `ARCH_GET_GS` when the backend cannot expose the register update; complete
  backends (ptrace) stay on the real kernel path.
- **This partially REFUTES the earlier memory characterization** ("backend
  register-ownership architecture, not a small fix"): the cell is closable with a
  ~45-line **backend-agnostic Detcore fallback**, not by giving liteinst `%gs`
  ownership. Because it is a Detcore-side shadow that leaves complete backends
  unchanged, it does not require the paused liteinst-backend work.

**Runner-up / highest LEVERAGE (most cells per fix):** flagship **#1466** MT
thread-lifecycle — unlocks ~42 of the 61 gap cells at once, but is OWNER-GATED
(Reverie API Policy + likely post-facto-human-review) and expensive. Drive the owner,
do not open per-cell ratchets against the MT bucket.

**Honest bottom line (ESTABLISHED, memory-confirmed):** apart from arch-prctl (#1397),
**ZERO clean, in-lane, non-owner-gated liteinst determinization ratchets exist** — the
lane is saturated on the landable backend. Every remaining residual is owner-gated
(MT/exec/vDSO) or architecturally intrinsic (nostdlib/preload, socket-ns).

## Caveats

- Gap computed on the committed **200-cell** scorecard; the +35 new cells are unmeasured
  for both backends, so the 61 could grow when 235 is swept (the +35 are mostly
  single-threaded microbenchmarks; HYPOTHESIS: they add few liteinst-specific reds).
- Real parity denominator on 235 is BOX-BLOCKED (needs boxed ptrace re-sweep).
- `parity 108` is piped-stdout-hash (upper bound), not full detlog parity.

## Reproduction

```
cd compat-envelope
# denominator + counts:
awk -F, 'NR>1&&$11=="liteinst"{t++;if($14=="1")d++;if($15=="1")p++}END{print d"/"t" parity "p}' fullcorpus-scorecard.csv
# gap (ptrace det, liteinst not det):
awk -F, 'NR>1&&$11=="ptrace"&&$14=="1"{print $8"|"$9"|"$10}' fullcorpus-scorecard.csv|sort >/tmp/pt
awk -F, 'NR>1&&$11=="liteinst"&&$14=="1"{print $8"|"$9"|"$10}' fullcorpus-scorecard.csv|sort >/tmp/li
comm -23 /tmp/pt /tmp/li            # 61 cells
wc -l corpus/*.tsv                  # 235 current corpus
```
