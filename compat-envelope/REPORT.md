# Compat-Envelope Scorecard — Rendered Report (full-corpus)

Automated, machine-readable cross-backend compatibility measurement.
Task: `automated-compat-envelope-measurement` /
`scorecard-full-manifest-denominator`. Rendered from the CSVs in this directory.

- Hermit SHA: `82a8e853357584a3a567fd80812e015572a607c7` (current `main`)
- Reverie SHA: `a4f33d69a56ed4233a53b218c39d93807ffc8cd0`
- Host: 316-core devbig, release hermit binary, load ~77 during the sweep.
- **Denominator = the FULL e2e manifest corpus**, not the portable-CI subset.

## The denominator: 200 (not 28)

An earlier revision of this report headlined a ptrace denominator of **28**. That
was never the corpus size — it was the tests that are simultaneously
`lane=portable`, `ci=true`, **and** measured green at L2, an artifact of the
collector's `--ci-only` + `--lane portable` filter.

The **full e2e manifest corpus** (static parse of all 13
`hermit/tests/e2e/manifests/*.toml`, matches `grep -c '[[test]]'`) is:

- **202** `[[test]]` blocks total; **200** declare a **ptrace verify** cell
  (the 2 exceptions are the KVM-only application examples, which have no ptrace
  cell) → **200 is the true ptrace-verify denominator**, across **13 buckets**.
- **200 portable / 2 privileged** (corrected from the as-shipped 199 / 3; see the
  triage table). 0 tests need root; only 2 need `/dev/kvm`.

This report is now rendered over that full 200-cell corpus with **measured**
ptrace, KVM, and LiteInst columns at current `main`.

## Measured this run (full-corpus L2, hermit `82a8e853`)

Every one of the 200 verify-mode cells (184 compiled C + 16 shell/interpreter)
was run under `hermit run --strict --verify` (L2, DETLOG-bitwise self-verify) on
each available backend. This is a *what-can-each-backend-verify* sweep: it
bypasses the manifest `ci`/`enabled` gating and applies uniform lane flags
(portable → `--no-virtualize-cpuid --max-timeslice=disabled`) so the KVM/LiteInst
columns are apples-to-apples with the ptrace reference.

- **`deterministic` (det)** = backend `--strict --verify` exits 0 (self-verified
  bitwise-identical repeat).
- **`stdout parity`** = backend `--strict` piped stdout has the same SHA-256 as
  the ptrace `--strict` reference. This is measured independently of the
  backend's `--strict --verify` result.

**Measurement limit:** stdout parity is an upper bound on four-signal
cross-backend parity. These data do not compare the other three required
signals: the INFO log, stack detlog, and heap detlog. TTY behavior is also
outside this scorecard. A `100%` stdout-parity cell can therefore still diverge
on any of those unmeasured signals.

**Measured counterexample (2026-08-03):** the `backend-parity/exit_zero` guest
(`/bin/true`) at Hermit `e8a0d8d3be3b53985dc898bb8e5cbb696a6a719f` exited 0
under ptrace and DBI with `--strict --no-virtualize-cpuid
--max-timeslice=disabled`, and produced the same empty stdout (`e3b0c442...`).
After removing only the wall-clock log prefix, the DETLOG payloads differed in
every measured mode: INFO
`56c018b3...` vs `c0a4cb5e...`, stack `0e17fb5a...` vs `992c02fd...`, and heap
`56c018b3...` vs `bf3875dc...` (ptrace vs DBI). This is a real cell for which
stdout parity is green while three required full-parity signals are red. TTY
behavior was not measured.

### Full-corpus scorecard (absolute counts)

`ptrace` is the golden denominator (integer cells passing L2). Every other
backend shows `det / stdout parity` absolute counts, where `det` = backend
`--strict --verify` exits 0 and `stdout parity` = backend guest stdout is SHA-256-identical
to the plain-`--strict` ptrace reference. All six backends are now populated with
**measured** data at hermit `82a8e853` (uniform lane flags). Stdout parity is measured
against a race-free plain-`--strict` ptrace reference regenerated for this run
(`ptref235.out`); 20 cells where ptrace itself fails under plain `--strict` are
stdout-parity-unmeasured for every non-ptrace backend (det still measured).

```
bucket                 corpus  ptrace     kvm         lite        dbi         sabre       e9patch
------------------------------------------------------------------------------------------------
applications                1   1/1        1/1         0/0         1/1         1/1         1/1
backend-parity-c            3   3/3        3/2         2/2         3/3         2/2         3/3
bin-c                       2   1/2        0/0         1/1         1/1         1/1         1/1
c-programs                159 149/159    111/99      112/103     133/120     137/123     149/147
chaos-c                     1   1/1        1/1         0/0         1/1         1/1         1/1
data-handling               2   0/2        0/0         0/0         0/0         0/0         0/0
debugger-c                  1   1/1        1/0         1/1         1/1         1/1         1/1
determinism-stress          4   2/4        2/2         0/0         1/1         2/2         2/2
determinism-stress-c       10   9/10       3/3         0/0         7/6         9/8         9/9
language-runtimes           6   6/6        3/2         0/0         3/1         4/1         6/3
shared-futex-c              4   0/4        0/0         0/0         0/0         0/0         0/0
system-utils                6   6/6        5/2         2/1         5/2         6/2         6/5
util-c                      1   0/1        0/0         0/0         0/0         0/0         0/0
------------------------------------------------------------------------------------------------
TOTAL                     200 179/200    130/112     118/108     156/137     164/142     179/173
```

### Same scorecard as `stdout-parity%, determinism%` of the ptrace-green count

(Rendered by
`render-scorecard.rs --csv fullcorpus-scorecard.csv --all --backends dbi,kvm,sabre,liteinst,e9patch`;
each backend cell is `stdout-parity%, determinism%` as a fraction of that
bucket's ptrace-green count. The two measurements are independent; the
stdout-only limitation above applies to every percentage in this table.)

```
bucket                  ptrace               dbi               kvm             sabre          liteinst           e9patch
------------------------------------------------------------------------------------------------------------------------
applications                 1        100%, 100%        100%, 100%        100%, 100%            0%, 0%        100%, 100%
backend-parity-c             3        100%, 100%         67%, 100%          67%, 67%          67%, 67%        100%, 100%
bin-c                        1        100%, 100%            0%, 0%        100%, 100%        100%, 100%        100%, 100%
c-programs                 149          80%, 89%          66%, 74%          82%, 92%          69%, 75%          98%, 99%
chaos-c                      1        100%, 100%        100%, 100%        100%, 100%            0%, 0%        100%, 100%
data-handling                0            0%, 0%            0%, 0%            0%, 0%            0%, 0%            0%, 0%
debugger-c                   1        100%, 100%          0%, 100%        100%, 100%        100%, 100%        100%, 100%
determinism-stress           2          50%, 50%        100%, 100%        100%, 100%            0%, 0%        100%, 100%
determinism-stress-c         9          67%, 78%          33%, 33%         89%, 100%            0%, 0%        100%, 100%
language-runtimes            6          17%, 50%          33%, 50%          17%, 67%            0%, 0%         50%, 100%
shared-futex-c               0            0%, 0%            0%, 0%            0%, 0%            0%, 0%            0%, 0%
system-utils                 6          33%, 83%          33%, 83%         33%, 100%          17%, 33%         83%, 100%
util-c                       0            0%, 0%            0%, 0%            0%, 0%            0%, 0%            0%, 0%
------------------------------------------------------------------------------------------------------------------------
TOTAL                      179          76%, 87%          63%, 72%          79%, 92%          60%, 66%          96%, 99%
```

### Headline numbers (for relay)

| metric | value | notes |
|--------|------:|-------|
| **Corpus / denominator** | **200** | ptrace-verify cells (202 `[[test]]`; 13 buckets). Replaces the "28". |
| **ptrace L2 green** | **179/200 (89.5%)** | measured at `82a8e853`, uniform flags. |
| ptrace L2 green (default flags) | 178/200 (89.0%) | preemption on; cross-validates the denominator is **flag-robust**. |
| **KVM** | det **130/200 (65%)**, stdout parity **112** | of 184 stdout-parity-measurable cells (16 non-C: ptrace-side fail → stdout parity unmeasured). |
| **LiteInst** | det **118/200 (59%)**, stdout parity **108** | hybrid (reverie-liteinst patch runtime + ptrace Detcore). |
| **DBI** | det **156/200 (78%)**, stdout parity **137** | `--features third-party-backends` binary at `82a8e853`; stdout parity of 180 measurable cells. |
| **SaBRe** | det **164/200 (82%)**, stdout parity **142** | real SaBRe loader (`libdetcore_sabre.so`, coordinator RPC), not ptrace fallback. |
| **e9patch** | **AUTHORITATIVE (whole-corpus re-sweep `c7531a83`): 183/184 (99.46%) stdout parity AND det on the ptrace-green denominator.** *(The `82a8e853` column below — det 179/200, stdout parity 173 — is SUPERSEDED.)* | e9patch AOT rewrite + ptrace runtime; tracks ptrace closely because the runtime *is* ptrace. The **only measured stdout gap** is `rcx-canonicalization` (inherent instruction relocation); the stdout-parity gap collapsed 7 → 1 vs `82a8e853` as landed detcore fixes (stdio-inode `ee746bde`, proc-fd fixture `c7531a83`, backend-independent virtual clock) flipped in. Evidence: `experiments/e9patch_fullcorpus_resweep_c7531a83_20260802/`. |
| **Portable / privileged** | **200 / 2** | was 199 / 3; `cpuid-probe` reclassified portable. |

Honest caveats:

- **`shared-futex-c` (0/4)** and **`data-handling` (0/2)** fail under **every**
  backend including ptrace, and under **both** flag configs — genuine failures
  (segfaults / output divergence), not a preemption-flag artifact.
- The **179 ptrace** figure is under uniform flags for backend comparability.
  Default hermit flags give **178** (one cell — `applications/timed-progress-bar`
  — times out with preemption on but passes with it off), so the denominator does
  not depend on the flag choice.
- **DBI/SaBRe/e9patch are now measured on the full corpus** using a
  `--features third-party-backends` binary at `82a8e853`
  (`scratch/featured235-target/release/hermit`). Ordering by determinism:
  e9patch (179) ≈ ptrace (179) > sabre (164) > dbi (156). e9patch tracks ptrace
  because its runtime *is* ptrace (only the guest code is AOT-rewritten by
  e9patch); SaBRe is a genuine ELF-loader backend (`libdetcore_sabre.so` +
  coordinator RPC, confirmed via `--log=trace`); DBI is DynamoRIO in-process
  and pays the most from preemption/re-entrancy limits (see the DBI notes).
- **Stdout-reference correctness.** Stdout parity is measured against a plain-`--strict`
  ptrace reference (`ptref235.out`) regenerated race-free for this run, because
  the earlier `--verify`-based reference emitted no guest stdout (double-run mode
  suppresses it), which had spuriously deflated every backend's stdout parity. 20 cells
  where ptrace itself errors under plain `--strict` are stdout-parity-unmeasured (not
  counted as 0) for all non-ptrace backends; det remains measured on all 200.
- **e9patch ratchet ADVANCED — the earlier "7 inherent gaps / SATURATED" claim is
  RETRACTED.** That claim was made at `82a8e853`. Re-measured at `e8ddd925` (exact
  portable-lane flags `--strict --no-virtualize-cpuid --max-timeslice=disabled`,
  real corpus guests), **6 of the 7 flipped to stdout-parity-green + L2-deterministic**:
    - `proc-fd-link-aliases` → green via **`ee746bde` "Stabilize stdio inode
      identity across backends"** (fd 1's `readlink` now emits a fixed
      `pipe:[1001]` = `DET_SPECIAL_INODE_OFFSET+1`, independent of loader activity
      — read the diff, this is the confirmed cause).
    - `date-nanoseconds`, `bash-loop-pipe-time`, `perl-io-subprocess-time`,
      `python-io-subprocess-time` → green: the **virtual clock is now
      backend-independent** (byte-identical CLOCK_REALTIME/MONOTONIC ns and
      subprocess durations across ptrace vs e9patch), so my earlier
      "e9loader-prologue advances the virtual clock" attribution was **stale/wrong**
      at this SHA.
    - `print-memaddrs` → green: stack + malloc `%p` addresses byte-identical
      across backends.
  - **Only `rcx-canonicalization` remains a genuine inherent gap** (class-a
    instruction relocation: e9tool moves the in-ELF `syscall` into a trampoline,
    so the guest's own SYSRET `%rcx` return-RIP no longer equals its inline label;
    e9 exits 1 / 0 bytes / det-verify exit 1). This one is truly "impossible by
    construction" — no interception backend that relocates the syscall can pass it.
  - **Scope caveat RESOLVED — whole-corpus re-sweep at `c7531a83`.** The 7-cell
    re-check above left the full column stale at `82a8e853`; a fresh
    `collect-fullcorpus.sh --backends ptrace,e9patch` sweep at current main
    (hermit `c7531a83`, reverie `ef5ffebc`, 235-cell corpus, 184 ptrace-green)
    now gives the authoritative figure: **e9patch 183/184 (99.46%) stdout parity AND
    det on the ptrace-green denominator.** The stdout-parity gap is down to **1** — the
    sole cell where ptrace passes det but e9patch does not is
    `rcx-canonicalization`; there are **zero** stdout-parity-only gaps, and the 21
    stdout-parity-unmeasured cells are all ones where **ptrace itself fails** (qemu-*,
    thread-contention, ipc/signal/mmap-determinism, shell-pipeline, pmu-skid, …),
    which e9patch mirrors rather than regresses. The product-side ratchet is thus
    at its achievable bar, this time confirmed by whole-corpus measurement, not
    inference. Evidence: `experiments/e9patch_fullcorpus_resweep_c7531a83_20260802/`
    (full 410-row CSV). Prior 7-cell recheck:
    `experiments/e9patch_compat_ratchet_recheck_20260802/`; superseded original:
    `experiments/e9patch_compat_last_cells_rootcause_20260801/`.

## Backend-parity matrix (complementary — where DBI has real data)

A separate 23-test backend-parity suite (`bucket=backend-parity`, distinct from
the e2e-corpus `backend-parity-c`), measured L2 self-verify at the same hermit
`82a8e853` (`ignored/backend-parity-scorecard.csv`, PR #1357):

```
backend      L2 verify pass
----------------------------
ptrace            23/23
dbi               21/23
kvm               21/23
```

This is the DBI/KVM parity frontier toward the golden ptrace reference; the two
DBI gaps and two KVM gaps are the tracked known deltas (e.g. KVM `waitid` ECHILD,
DBI `exit_status`).

## Portable-vs-privileged triage (folded in)

Fresh triage of every `lane=privileged` test (task
`triage-portable-vs-privileged-tests`):

| test id                            | requires | genuinely privileged? | verdict |
|------------------------------------|----------|-----------------------|---------|
| applications/kvm-python-examples   | `kvm`, `python3` | **YES** — needs `/dev/kvm` | privileged (KVM-backend example; no ptrace cell) |
| applications/kvm-shell-environment | `kvm`    | **YES** — needs `/dev/kvm` | privileged |
| backend-parity-c/cpuid-probe       | `cpuid`  | **NO** | **mis-classified → portable** — CPUID is an unprivileged x86_64 instruction on every host incl. GitHub `ubuntu-latest`; needs no `/dev/kvm`, root, or special hw. Confirmed portable: passes ptrace + KVM parity in the sweep. |

- **Corrected counts: 200 portable / 2 privileged** (0 need root; 2 need
  `/dev/kvm`) → **99% of the corpus is portable**.
- The "28" was never a privilege artifact. The lever to grow the *portable*
  scorecard denominator is to drop `--ci-only` and L2-calibrate the `ci=false`
  tail (the C-corpus migration), **not** to reclassify privilege.

## Reverie B1.5 Guest/Tool envelope (ptrace vs KVM)

The shared Reverie `counter` Tool (counter1 + counter2), run through the ptrace
launchers vs the KVM launchers over a static-busybox guest corpus.

```
bucket                  ptrace               kvm
------------------------------------------------
reverie-examples             6          0%, 100%
------------------------------------------------
TOTAL                        6          0%, 100%
```

This table's first percentage is **tool-count parity**, not stdout parity: it
compares the shared Tool's callback total between ptrace and KVM.

Honest finding (host with `/dev/kvm`): KVM is **fully self-deterministic**
(`100%` determinism) but surfaces a **constant 4 fewer syscalls** to the shared
Tool callback than ptrace (true 12→8, echo 15→11, pwd 16→12) → `0%` tool-count parity. A
real **B1.5 Guest-contract interception-surface gap**, measured and confirmed 0
(no `?`), not a determinism defect.

## Legend (anti-fakery markers)

- `det / stdout parity` (absolute table) — cells self-verified L2 / cells whose
  piped stdout is bitwise-identical to ptrace. These are independent signals.
- `stdout-parity%` / `determinism%` (percent table) — fraction of that bucket's
  ptrace-green count. Neither percentage implies the other.
- `X%?` — stdout parity never measured (UNKNOWN, not a confirmed 0).
- `X%~` — partial stdout-parity coverage.
- `n/a` — backend ran **zero** cells here (binary absent / not built). **Not** a
  confirmed fail. A real red (ran + failed) is visually distinct from "not
  runnable here".

## How to regenerate

```bash
# Full-corpus L2 sweep (reuses guests compiled under ignored/kvm-fullcorpus):
experiments/ptrace_fullcorpus_scorecard_20260801/sweep.sh            # ptrace L2 (uniform flags)
NOFLAGS=1 ROWS=.../rows-default OUTCSV=.../scorecard-ptrace-default.csv \
  experiments/ptrace_fullcorpus_scorecard_20260801/sweep.sh          # ptrace L2 (default flags)
experiments/ptrace_fullcorpus_scorecard_20260801/sweep-liteinst.sh   # LiteInst det + stdout parity
experiments/kvm_fullcorpus_scorecard_20260801/sweep.sh               # KVM (C cells)
experiments/kvm_fullcorpus_scorecard_20260801/sweep-nonc.sh          # KVM (shell/interpreter cells)

# DBI / SaBRe / e9patch (needs a --features third-party-backends binary):
HERMIT_BIN=scratch/featured235-target/release/hermit BACKEND=dbi \
  experiments/ptrace_fullcorpus_scorecard_20260801/sweep-backend.sh   # (also sabre, e9patch)

# Regenerate the plain --strict ptrace STDOUT reference (ptref235.out) — the
# --verify-based ptv.out is empty (double-run suppresses stdout), so stdout
# parity must use this; then recompute the backend stdout-parity columns:
HERMIT_BIN=scratch/featured235-target/release/hermit \
  experiments/ptrace_fullcorpus_scorecard_20260801/gen-ptrace-parity-ref.sh
experiments/ptrace_fullcorpus_scorecard_20260801/recompute-parity.sh

# Merge + render (ptrace+kvm+liteinst+dbi+sabre+e9patch = 1200 rows):
cat scorecard-ptrace.csv <(tail +2 kvm-all.csv) <(tail +2 scorecard-liteinst.csv) \
    <(tail +2 scorecard-dbi.csv) <(tail +2 scorecard-sabre.csv) \
    <(tail +2 scorecard-e9patch.csv) > fullcorpus-scorecard.csv
compat-envelope/render-scorecard.rs --csv compat-envelope/fullcorpus-scorecard.csv \
  --all --backends dbi,kvm,sabre,liteinst,e9patch
```

## Data files

- `fullcorpus-scorecard.csv` — merged 1200-row full-corpus scorecard (ptrace + kvm
  + liteinst + dbi + sabre + e9patch, 200 cells each) at hermit `82a8e853`. **This
  is the machine-readable denominator artifact.**
- `corpus-manifest.csv` — the 200 ptrace-verify cells + lane + calibration state.
- `ignored/backend-parity-scorecard.csv` — the 23-test backend-parity matrix
  (ptrace/dbi/kvm L2).
- `experiments/ptrace_fullcorpus_scorecard_20260801/` + `…/kvm_fullcorpus_scorecard_20260801/`
  — raw per-cell sweeps (rows, logs, per-backend CSVs, default-flags cross-check).

## Enshrinement (validate + CI)

The gate is wired into the outer-repo `Makefile` and a CI workflow (unchanged):
`make validate` → `make compat-envelope` (release + DBI feature) runs the
regression gate; `.github/workflows/compat-envelope.yml` has a portable
`ubuntu-latest` ptrace-denominator job (always-on) and a privileged self-hosted
full-backend job (DBI + SaBRe + KVM/reverie), rrnewton-guarded. A full-corpus L2
sweep across the `ci=false` tail is the phase-2 expansion (`expansion-dag.rs`
boxed cgroup lane) for a load-independent denominator.
