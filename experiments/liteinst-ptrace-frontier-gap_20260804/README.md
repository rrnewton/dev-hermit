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

## Vacuity audit (2026-08-04)

Applies the night's reverie vacuity lens (memory `vacuous-test-audit-ci-tooling-slice-clean`:
"does the test FAIL if the mechanism does not run?") to the **108 liteinst PARITY cells**.
Measurement/inspection only — no boxed sweep, no source edits. Method: joined the 108
parity=1 liteinst cells from `compat-envelope/fullcorpus-scorecard.csv` (@82a8e853) to their
corpus sources (`corpus/corpus-c.tsv`, `corpus/corpus-nonc.tsv`) and inspected each program's
**stdout** emission (parity = piped-stdout-SHA-256 equivalence vs the ptrace golden reference,
ESTABLISHED `compat-envelope-scorecard-system`).

### Denominator restated (ESTABLISHED)

- **108 of 200 cells** parity=1, liteinst backend, hermit `82a8e853`, `--strict --verify`
  RECORDED double-run, `parity` field (col 15). Same stale-on-two-axes caveats as above
  (corpus 200→235; 82a8e853 flagship vs landable d973cc63).
- **parity ⊆ det**: 0 cells are parity=1 & det=0; 10 cells are det=1 & parity=0
  (print-memaddrs, proc-fdinfo, proc-fd-link-aliases, socket-cookie-{tcp,udp,unix},
  socket-timestamp-{timespec,timeval}, sysinfo, clock-determinism). So the 108 is a strict
  subset of the 118 det. (ESTABLISHED, awk join.)
- **parity is stdout-SHA-256 only** = upper bound; blind to INFO/detlog-stack/detlog-heap.
  Therefore parity has **no negative side by construction** (see below).

### (a) genuinely-bracketed vs (b) stdout-only — the split

The honest covered-count is (a); the 108 = (a)+(b).

- **(a) GENUINELY-BRACKETED (value-emitting) = 22 cells (ESTABLISHED by source inspection).**
  stdout literally carries a determinized nondeterministic quantity, so an inert liteinst
  mechanism would leak the raw host value into stdout and diverge from the ptrace golden —
  the ptrace differential is a *de-facto* (not planted) negative side. List in
  `/tmp/valueemit.txt`; the cleanest are: `getcpu` (CPU id → virtual 0), `pid-probe` /
  `record-getpid` (canonical pid), `uname` (canonical kernel release/version), `tcp-info-*`
  (canonicalized TCP_INFO bytes), `so-incoming-cpu-*` (virtual CPU 0), `adjtimex` /
  `clock-adjtime` / `syslog` / `setitimer` / `timer-create` (determinized time/timer values),
  `syscall-quick-wins` (determinized uids/gids), `cpuid-probe` (canonical CPUID vendor/sig).
  - CAVEAT: ~3-4 of the 22 emit a *deterministic data checksum*, not a canonicalized
    nondeterministic source (`io-uring-ring-determinism`, `mmap-stress-determinism` checksums;
    `rcx-canonicalization` mostly literal `=1` + 0/1 bits). Their checksum would match even if
    the determinization were inert, so they are weak brackets. **Tight genuine-bracket floor
    ~18; upper value-emit bound 22.**

- **(b) STDOUT-ONLY / POTENTIALLY-VACUOUS = 86 cells (108 − 22).** stdout is a fixed
  constant string emitted only on the self-check passing (`puts("...-ok")` /
  `puts("...deterministically refused")`); the determinized value itself is compared
  in-program and reported to **stderr** on failure (stderr is NOT in the parity hash). Whether
  such a cell is truly vacuous = **does the host natively satisfy the in-program assertion?**
  (the exact reverie failure mode). That is per-cell **BOX-BLOCKED** (needs native/undeterminized
  errno+value per syscall on the host); resolved only for cases where the canonical constant is
  manifestly non-host (e.g. `meminfo-*-deterministic` assert MemTotal==976562 KB ≪ devbig014
  RAM → host fails the assertion → inert liteinst → empty stdout → parity diverges → those 3
  ARE bracketed-via-gate).

  **Prime vacuity SUSPECTS — the error-canonicalization family = 43 of the 86 (ESTABLISHED count):**
  - 30 `*-enosys` (add-key, bpf, cachestat, futex-{requeue,waitv,wake}, keyctl, listmount,
    lsm-{get-self-attr,list-modules,set-self-attr}, map-shadow-stack, memfd-secret,
    perf-event-{hardware,open,software,watchpoint}, process-mrelease,
    remap-file-pages-{anonymous,memfd,tmpfile}, request-key, splice, statmount, sysfs,
    sysv-{sem,shm}, tee, ustat, vmsplice)
  - 4 `name-to-handle-*-eopnotsupp`; 3 `*-eperm` (kcmp, ptrace, ptrace-traceme);
    4 `*-refusal-probe` (acct, copy-file-range, process-vm-readv, process-vm-writev);
    2 dbi-error (dbi-unsupported-syscall, dbi-exec-failure).
  - Each prints a fixed "deterministically unavailable/refused" string on the expected error.
    For a syscall genuinely **absent** on the host (returns ENOSYS natively) the cell is
    **provably vacuous** — inert liteinst → host ENOSYS → same "ok" string → parity holds.
    For a syscall the host **implements** but Hermit refuses by policy (e.g. splice/tee/keyctl/
    perf_event_open on modern kernel 6.18) an inert liteinst would let the syscall succeed →
    assertion fails → empty stdout → parity diverges → bracketed-via-gate. Which enosys cells
    fall in which bucket is **BOX-BLOCKED** (needs per-syscall native errno on the host).
  - ~4 constant-string signal/delivery programs are effectively vacuous regardless of host
    (`hello-alarm`, `hello-signals`, `sigpipe-siginfo`, `dbi-self-sigqueue`: emit a fixed signal
    number / fixed si_code / fixed banner — no nondeterministic source in stdout).
  - Remaining ~39 are self-check "ok" gates (autobind/netns-cookie/proc/timer/memory/socket)
    whose bracketing depends on host≠canonical = BOX-BLOCKED, likely mostly bracketed-via-gate.

### (3) Negative control — NONE by construction (ESTABLISHED, this is itself the finding)

**No liteinst parity cell has a real negative control** (a planted divergence confirmed
*caught*). The parity metric is stdout-SHA-256 *equality* with the ptrace reference; by
construction it only ever asserts two hashes are equal — it never plants a violating case and
confirms refusal. The 22 value-emitting cells get an *implicit* differential (an inert liteinst
would diverge from the ptrace golden), but that is a compare-against-reference, not a planted
negative control internal to the cell; for the 86 constant-"ok" cells even that differential
collapses when the host already conforms (both backends print "ok"). This mirrors the reverie
finding: stdout-SHA-256 parity has a positive side only.

### (4) Cheapest next cell — #1397 arch-prctl: still cheapest, and NOT vacuous (bracketed-via-gate)

- **Still the cheapest in-lane ratchet (ESTABLISHED):** PR #1397 is the only clean,
  non-owner-gated, pause-safe liteinst determinization ratchet (~45 LOC, all Detcore-side
  shadow-GS, backend-agnostic). Unchanged from the frontier finding above.
- **NOT vacuous — but it is a self-check gate, not value-emitting (ESTABLISHED by reading
  `tests/c/arch_prctl_determinism.c`).** The program prints only `puts("arch-prctl-deterministic")`
  on success; every determinized value (the ARCH_SET_GS/ARCH_GET_GS round-trip
  `expect_result("changed GS value", observed_gs, requested_gs)`, CPUID state, XCOMP/SHSTK
  normalization) is checked in-program and reported to **stderr** on failure. So the GS value
  does NOT reach stdout. It is bracketed *via the gate*: pre-#1397 liteinst reports gs_base=0
  (ESTABLISHED, gap analysis above) → the "changed GS value" assertion fails → stdout goes
  empty → parity vs ptrace diverges. The measured #1397 delta flips det 0→1 **and** parity 0→1
  together, so the parity flip is causally the assertion flip — the strongest binding a
  self-check cell can have short of a planted negative control. It shares the family weakness
  (no value in stdout, no explicit negative control), but it is genuinely bound, not vacuous.

### Bottom line

Restated: **108/200 parity (stdout-SHA-256, @82a8e853, RECORDED) = 22 genuinely-bracketed
(value-emitting; tight floor ~18) + 86 stdout-only**, of which **43 are the error-canonicalization
suspect family** (provably vacuous where the host is ENOSYS-native, bracketed-via-gate where
Hermit refuses a host-implemented syscall — the discriminator is BOX-BLOCKED). **No cell has a
planted negative control; stdout-SHA-256 parity has no negative side by construction.** #1397
remains the cheapest next cell and is bracketed-via-gate (NOT vacuous), though it emits only a
pass-gate string, not the GS value.

### Reproduction (extends the block above)

```
cd compat-envelope
# 108 parity cells:
awk -F, 'NR>1&&$11=="liteinst"&&$15=="1"{print $8"|"$9"|"$10}' fullcorpus-scorecard.csv|sort
# parity ⊆ det check (empty = subset):
comm -23 <(awk -F, 'NR>1&&$11=="liteinst"&&$15=="1"{print $9}' fullcorpus-scorecard.csv|sort) \
         <(awk -F, 'NR>1&&$11=="liteinst"&&$14=="1"{print $9}' fullcorpus-scorecard.csv|sort)
# errno-family suspect count (43): grep the parity ids for enosys|eopnotsupp|eperm|refusal|unsupported-syscall|exec-failure
# value-emit vs const-stdout: inspect each source's non-stderr printf/puts for a % conversion fed by a syscall/proc value
```
