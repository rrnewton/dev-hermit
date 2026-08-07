# Compat-Envelope Scorecard — Full-Corpus Denominator

> **SUPERSEDED for current numbers (2026-08-06).** This is a 2026-08-04 analysis
> of the denominator question. For the current rendering of every CSV in this
> directory — with the column schema, interpretation rules, provenance, and the
> stripped-vs-bitwise certification limit — read
> [`SCORECARD-CURRENT.md`](SCORECARD-CURRENT.md). The corpus reasoning below
> still stands; the tables are stale.

Machine-readable cross-backend compatibility measurement, rendered in the owner's
two-table format. The ptrace denominator is the **FULL e2e manifest corpus**, not
the portable-CI subset.

> **See `REPORT.md` for the authoritative MEASURED full-corpus scorecard** at
> current `main` (hermit `82a8e853`): ptrace **179/200** L2, KVM det 130/200,
> LiteInst det 118/200, all as real per-cell measurements. This file retains the
> corpus enumeration, the L1-sweep corroboration, and the portable/privileged
> triage; its L2-green column below (`28`) is the older *calibrated-CI subset*,
> now superseded by the 179 measured over the whole corpus.

## The denominator correction (why this file replaces the "28" scorecard)

The phase-1 scorecard reported a ptrace denominator of **28**. That was wrong as a
*corpus* number: 28 = tests that are simultaneously `lane=portable`, `ci=true`,
**and** measured passing `--strict` verify at L2. It was an artifact of the
collector's `--ci-only` filter, **not** the size of the test set.

The full corpus (13 manifests, current checkout) is:

- **202** `[[test]]` blocks total.
- **200** of them declare a **ptrace verify** cell (the 2 exceptions are the
  KVM-only application examples, which have no ptrace cell) → **200 is the true
  ptrace-verify denominator**.
- **199 are `lane=portable`; 3 are `lane=privileged`** as shipped. Corrected to
  **200 portable / 2 privileged** (see triage below).

So the corpus is ~200, not 28. The 28 is the *currently-calibrated-green* subset,
now reported as calibration progress against the full denominator.

Sources: static parse of `hermit/tests/e2e/manifests/*.toml`
(`corpus-manifest.csv`, 200 rows); L2 green from the canonical release run
(`scorecard.csv`, hermit `9429005c`); L1 ptrace/KVM from the quiet-host C-corpus
sweep (`experiments/kvm_b3_corpus_sweep_20260730/results.tsv`, hermit `9cd955f9`,
183 rows).

---

## Table 1 — Hermit: full Detcore program compat (full ptrace-verify corpus)

Rows are the 13 e2e manifest buckets. **`ptrace corpus`** is the full
ptrace-verify denominator per bucket. **`L2 green`** = measured passing `--strict`
verify (canonical release run). **`L1 green`** = passing `--strict` 3× byte-
identical exit+stdout (quiet-host sweep; C corpus only). **`kvm stdout parity (L1)`** =
KVM output bitwise-identical to ptrace in that sweep.

| bucket                | ptrace corpus | L2 green | L1 green (sweep) | kvm stdout parity (L1) |
|-----------------------|--------------:|---------:|-----------------:|----------------:|
| c-programs            |           159 |        8 |          149/159 |          99/159 |
| determinism-stress-c  |            10 |        1 |            8/10  |           2/10  |
| system-utils          |             6 |        5 |            2/6   |           0/6   |
| language-runtimes     |             6 |        6 |            n/a   |           n/a   |
| shared-futex-c        |             4 |        0 |            0/4   |           0/4   |
| determinism-stress    |             4 |        4 |            1/4   |           1/4   |
| backend-parity-c      |             3 |        0 |            3/3   |           2/3   |
| data-handling         |             2 |        2 |            n/a   |           n/a   |
| bin-c                 |             2 |        0 |            1/2   |           0/2   |
| util-c                |             1 |        0 |            0/1   |           0/1   |
| debugger-c            |             1 |        0 |            1/1   |           0/1   |
| chaos-c               |             1 |        0 |            1/1   |           1/1   |
| applications          |             1 |        1 |            n/a   |           n/a   |
| **TOTAL**             |       **200** |   **28** |      **166/183** |     **105/183** |

- **ptrace corpus = 200** — the true full-manifest denominator. `n/a` in the L1/KVM
  columns = that bucket is not part of the C-corpus sweep (shell/interpreter
  buckets), **not** a fail.
- **L2 green = 28** — the calibrated-green subset from the canonical release run.
  The remaining ~172 ptrace-verify cells are **portable-but-uncalibrated**
  (`ci=false` during the C-corpus migration), not privileged — this is the
  calibration frontier, not a compat wall.
- **L1 green = 166/183** and **kvm stdout parity = 105/183** come from the quiet-host
  sweep and corroborate that the uncalibrated tail is mostly *passing* under
  ptrace (166/183 = 91% at L1); L2 `--strict --verify` calibration is the phase-2
  sweep. The `105/183` matches the KVM figure cited to the owner.
- **DBI/SaBRe** columns are omitted from this corpus view: DBI is measured only on
  the calibrated c-programs slice (**8/8 bitwise-identical to ptrace, 8/8
  self-deterministic at L2** — `100%, 100%`); SaBRe is `n/a` here (loader not built
  in this checkout). See the calibrated-subset scorecard in `scorecard.csv` /
  `REPORT.md` for the per-backend stdout-parity/determinism cells and the
  four-signal limitation.

## Table 2 — Reverie: Tool callback-count parity (B1.5+ backends)

The shared Reverie `counter` Tool (counter1 + counter2), run through the ptrace
launchers vs the KVM launchers over a static-busybox guest corpus.

The first percentage compares callback totals only; it is not stdout or
four-signal cross-backend parity.

| bucket            | ptrace | kvm        |
|-------------------|-------:|:-----------|
| reverie-examples  |      6 | 0%, 100%   |
| **TOTAL**         |  **6** | **0%, 100%** |

- **KVM** is fully self-deterministic (`100%` determinism, 6/6 identical reruns)
  but surfaces a **constant 4 fewer syscalls** to the shared Tool callback than
  ptrace (true 12→8, echo 15→11, pwd 16→12) → `0%` tool-count parity. A real **B1.5
  Guest-contract interception-surface gap**, measured and confirmed 0 (no `?`),
  not a determinism defect.

---

## Portable-vs-privileged triage (folded in)

Fresh triage of every `lane=privileged` test (task
`triage-portable-vs-privileged-tests`):

| test id                            | requires (privileged token) | genuinely privileged? | verdict |
|------------------------------------|-----------------------------|-----------------------|---------|
| applications/kvm-python-examples   | `kvm`, `python3`            | **YES** — needs `/dev/kvm` | privileged (KVM-backend example; no ptrace cell) |
| applications/kvm-shell-environment | `kvm`                       | **YES** — needs `/dev/kvm` | privileged |
| backend-parity-c/cpuid-probe       | `cpuid`                     | **NO** | **mis-classified → portable** — CPUID is an unprivileged x86_64 instruction on every host incl. GitHub `ubuntu-latest`; needs no `/dev/kvm`, root, or special hw. The `cpuid` `requires` token is a capability tag, not a privilege gate. (Confirmed portable: it PASSES ptrace+KVM with PARITY in the sweep.) |

- **Corrected counts: 200 portable / 2 privileged** (0 require root; 2 require
  `/dev/kvm`). The owner's expectation — *the majority should be portable* — is
  confirmed and then some: **99% of the corpus is portable**.
- The "28" was never a privilege artifact. The lever to grow the scorecard's
  portable denominator is to **drop `--ci-only`** and count all 200 ptrace-verify
  cells, plus L2-calibrate the ~172 `ci=false` cells — not to reclassify privilege.

## TOTAL-row numbers (for relay)

- **Corpus / denominator** — **200 ptrace-verify cells** (202 tests; 13 buckets).
  True total, replacing the "28".
- **Measured L2 green** (REPORT.md, hermit `82a8e853`) — ptrace **179/200
  (89.5%)** (flag-robust: 178/200 under default flags); KVM det **130/200**,
  stdout parity 112; LiteInst det **118/200**, stdout parity 108.
- **Calibrated-CI subset** — **28** at L2 (older canonical release run; the
  `--ci-only ∩ portable` slice, not the corpus).
- **Sweep corroboration** — ptrace **166/183** L1, KVM **105/183** L1 (quiet host).
- **Portable/privileged** — **200 / 2** (was 199 / 3; cpuid-probe reclassified).
- **Reverie** — ptrace **6**; KVM **0% tool-count parity, 100% determinism**.

## Regenerate

```bash
# Full-corpus enumeration (static, no test execution):
#   corpus-manifest.csv = every ptrace-verify cell + calibration state + L1 data
# Calibrated-subset scorecard (needs a quiet host / boxed cgroup lane for L2):
compat-envelope/collect-envelope.rs --mode regression --lane portable \
  --repo <hermit-checkout> --backends ptrace,dbi,sabre --with-parity \
  --csv compat-envelope/scorecard.csv
compat-envelope/render-scorecard.rs --csv compat-envelope/scorecard.csv --all
```
