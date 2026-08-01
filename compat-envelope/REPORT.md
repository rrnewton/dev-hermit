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
- **`parity`** = backend `--strict` stdout is bitwise-identical to the ptrace
  `--strict` reference **and** the cell is deterministic (parity ⊆ det).

### Full-corpus scorecard (absolute counts)

`ptrace` is the golden denominator (integer cells passing L2). `kvm` / `liteinst`
show `det / parity` absolute counts. `dbi` / `sabre` / `e9patch` are `n/a` on the
e2e corpus buckets in this checkout (see notes + the backend-parity matrix below);
`n/a` = **ran zero cells here, not a confirmed fail**.

```
bucket                  corpus   ptrace L2     kvm det/par   lite det/par   dbi  sabre  e9patch
---------------------------------------------------------------------------------------------
applications                 1       1/1          1/1           0/0         n/a   n/a    n/a
backend-parity-c             3       3/3          3/2           2/2         n/a   n/a    n/a
bin-c                        2       1/2          0/0           1/1         n/a   n/a    n/a
c-programs                 159     149/159      111/99        112/103       n/a   n/a    n/a
chaos-c                      1       1/1          1/1           0/0         n/a   n/a    n/a
data-handling                2       0/2          0/0           0/0         n/a   n/a    n/a
debugger-c                   1       1/1          1/0           1/1         n/a   n/a    n/a
determinism-stress           4       2/4          2/2           0/0         n/a   n/a    n/a
determinism-stress-c        10       9/10         3/3           0/0         n/a   n/a    n/a
language-runtimes            6       6/6          3/2           0/0         n/a   n/a    n/a
shared-futex-c               4       0/4          0/0           0/0         n/a   n/a    n/a
system-utils                 6       6/6          5/2           2/1         n/a   n/a    n/a
util-c                       1       0/1          0/0           0/0         n/a   n/a    n/a
---------------------------------------------------------------------------------------------
TOTAL                      200     179/200      130/112       118/108       n/a   n/a    n/a
```

### Same scorecard as `parity%, determinism%` of the ptrace-green count

(Rendered by `render-scorecard.rs --csv fullcorpus-scorecard.csv --all`; each
backend cell is a fraction of that bucket's ptrace-green count, `det% ≥ parity%`.)

```
bucket                  ptrace               kvm          liteinst
------------------------------------------------------------------
applications                 1        100%, 100%            0%, 0%
backend-parity-c             3         67%, 100%          67%, 67%
bin-c                        1            0%, 0%        100%, 100%
c-programs                 149          66%, 74%          69%, 75%
chaos-c                      1        100%, 100%            0%, 0%
data-handling                0            0%, 0%            0%, 0%
debugger-c                   1          0%, 100%        100%, 100%
determinism-stress           2        100%, 100%            0%, 0%
determinism-stress-c         9          33%, 33%            0%, 0%
language-runtimes            6          33%, 50%            0%, 0%
shared-futex-c               0            0%, 0%            0%, 0%
system-utils                 6          33%, 83%          17%, 33%
util-c                       0            0%, 0%            0%, 0%
------------------------------------------------------------------
TOTAL                      179          63%, 72%          60%, 66%
```

### Headline numbers (for relay)

| metric | value | notes |
|--------|------:|-------|
| **Corpus / denominator** | **200** | ptrace-verify cells (202 `[[test]]`; 13 buckets). Replaces the "28". |
| **ptrace L2 green** | **179/200 (89.5%)** | measured at `82a8e853`, uniform flags. |
| ptrace L2 green (default flags) | 178/200 (89.0%) | preemption on; cross-validates the denominator is **flag-robust**. |
| **KVM** | det **130/200 (65%)**, parity **112** | of 184 parity-measurable cells (16 non-C: ptrace-side fail → parity unmeasured). |
| **LiteInst** | det **118/200 (59%)**, parity **108** | hybrid (reverie-liteinst patch runtime + ptrace Detcore). |
| DBI | n/a on corpus | feature binary not built at this SHA in this slot; real data in the matrix below (21/23 L2). |
| SaBRe | n/a | loader not built in this checkout. |
| e9patch | n/a | AOT+ptrace; no e2e-corpus cells; empty scorecard. |
| **Portable / privileged** | **200 / 2** | was 199 / 3; `cpuid-probe` reclassified portable. |

Honest caveats:

- **`shared-futex-c` (0/4)** and **`data-handling` (0/2)** fail under **every**
  backend including ptrace, and under **both** flag configs — genuine failures
  (segfaults / output divergence), not a preemption-flag artifact.
- The **179 ptrace** figure is under uniform flags for backend comparability.
  Default hermit flags give **178** (one cell — `applications/timed-progress-bar`
  — times out with preemption on but passes with it off), so the denominator does
  not depend on the flag choice.
- **DBI/SaBRe/e9patch `n/a`** reflects binary availability in *this* checkout, not
  a compat wall. DBI needs the `third-party-backends` feature build (not present
  in the default `82a8e853` release binary here); SaBRe needs its loader built;
  e9patch is AOT+ptrace and has no e2e-corpus cells. Where DBI *is* built, it has
  real data — see the backend-parity matrix.

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

Honest finding (host with `/dev/kvm`): KVM is **fully self-deterministic**
(`100%` determinism) but surfaces a **constant 4 fewer syscalls** to the shared
Tool callback than ptrace (true 12→8, echo 15→11, pwd 16→12) → `0%` parity. A
real **B1.5 Guest-contract interception-surface gap**, measured and confirmed 0
(no `?`), not a determinism defect.

## Legend (anti-fakery markers)

- `det / parity` (absolute table) — cells self-verified L2 / cells also
  bitwise-identical to ptrace. parity ⊆ det.
- `parity%` / `determinism%` (percent table) — fraction of that bucket's
  ptrace-green count. det% ≥ parity% by construction.
- `X%?` — parity never measured (UNKNOWN, not a confirmed 0).
- `X%~` — partial parity coverage.
- `n/a` — backend ran **zero** cells here (binary absent / not built). **Not** a
  confirmed fail. A real red (ran + failed) is visually distinct from "not
  runnable here".

## How to regenerate

```bash
# Full-corpus L2 sweep (reuses guests compiled under target/kvm-fullcorpus):
experiments/ptrace_fullcorpus_scorecard_20260801/sweep.sh            # ptrace L2 (uniform flags)
NOFLAGS=1 ROWS=.../rows-default OUTCSV=.../scorecard-ptrace-default.csv \
  experiments/ptrace_fullcorpus_scorecard_20260801/sweep.sh          # ptrace L2 (default flags)
experiments/ptrace_fullcorpus_scorecard_20260801/sweep-liteinst.sh   # LiteInst det + parity
experiments/kvm_fullcorpus_scorecard_20260801/sweep.sh               # KVM (C cells)
experiments/kvm_fullcorpus_scorecard_20260801/sweep-nonc.sh          # KVM (shell/interpreter cells)

# Merge + render:
cat scorecard-ptrace.csv <(tail +2 kvm-all.csv) <(tail +2 scorecard-liteinst.csv) > fullcorpus-scorecard.csv
compat-envelope/render-scorecard.rs --csv compat-envelope/fullcorpus-scorecard.csv --all
```

## Data files

- `fullcorpus-scorecard.csv` — merged 600-row full-corpus scorecard (ptrace + kvm
  + liteinst, 200 cells each) at hermit `82a8e853`. **This is the machine-readable
  denominator artifact.**
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
