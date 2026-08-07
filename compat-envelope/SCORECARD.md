# Compat-Envelope Scorecard — Full-Corpus Denominator

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

> ### This table is a SEPARATE dataset — it is NOT a subset of Table 1
>
> Table 2 is **disjoint** from Table 1: the intersection is empty on both the
> bucket axis and the program axis (0 rows with `bucket=reverie-examples` and 0
> rows with a `counter1-`/`counter2-` `test_id` appear in `scorecard.csv`,
> `fullcorpus-scorecard.csv`, or `corpus-manifest.csv`). It measures a
> **different boundary** — the Reverie B1.5 `Guest`/`Tool` callback surface, not
> Detcore program compat — over a **different corpus** (synthetic static-busybox
> applets, not `hermit/tests/e2e/manifests/*.toml`).
>
> **Table 1's 200 and Table 2's 6 are not commensurable. Never sum them, and
> never express one as a percentage of the other.**

**Provenance.** Hermit `2f3689bd8830ab6b59dacea6cb72951f4d0d899e`; Reverie
`a4f33d69a56ed4233a53b218c39d93807ffc8cd0`; `run_id=reverie-20260801`, run
2026-08-01; `lane=portable`, `reps=2`, guest `/usr/sbin/busybox`. Source:
`reverie-scorecard.csv`. (Table 1's numbers come from different runs at different
hermit SHAs — see its own citations. This is why each table stamps its own.)

The shared Reverie `counter` Tool (counter1 + counter2) over three busybox
applets = **six programs**, run through each backend's launcher. The parity
figure compares **callback totals only**; it is not stdout parity and not
four-signal cross-backend parity.

### 2a — the six programs, one row each

| # | program (`test_id`) | tool | guest argv | ptrace syscalls | kvm syscalls | Δ | kvm outcome | kvm det | kvm parity |
|---|---------------------|------|-----------|----------------:|-------------:|--:|-------------|--------:|-----------:|
| 1 | `counter1-true`    | counter1 | `true`    | 12 | 8  | −4 | diverge | 1 | 0 |
| 2 | `counter1-echo-hi` | counter1 | `echo hi` | 15 | 11 | −4 | diverge | 1 | 0 |
| 3 | `counter1-pwd`     | counter1 | `pwd`     | 16 | 12 | −4 | diverge | 1 | 0 |
| 4 | `counter2-true`    | counter2 | `true`    | 12 | 8  | −4 | diverge | 1 | 0 |
| 5 | `counter2-echo-hi` | counter2 | `echo hi` | 15 | 11 | −4 | diverge | 1 | 0 |
| 6 | `counter2-pwd`     | counter2 | `pwd`     | 16 | 12 | −4 | diverge | 1 | 0 |
| | **TOTAL** | | | **6/6 pass** | **0/6 parity, 6/6 det** | **−4 const** | | | |

All six ptrace rows are `outcome=pass`, `deterministic=1`, `parity=` (empty
because **ptrace is the reference** and has no parity value to carry — this is
the one legitimately empty cell in the table, and it is empty by definition, not
by omission).

- **KVM** is fully self-deterministic (`100%`, 6/6 identical reruns) but surfaces
  a **constant 4 fewer syscalls** to the shared Tool callback than ptrace →
  `0%` tool-count parity. The delta is −4 on every one of the six, which is what
  makes this a structural **B1.5 Guest-contract interception-surface gap**,
  measured and confirmed 0 (no `?`), not a determinism defect and not noise.

### 2b — backend × program coverage, with typed absence reasons

Every known backend gets a row for every program. A backend with no data is
**not** a backend that failed — the reason is stated explicitly. Terminology is
**DBT**; the legacy name `dbi` is accepted on input and normalized.

| backend | counter1-true | counter1-echo-hi | counter1-pwd | counter2-true | counter2-echo-hi | counter2-pwd |
|---|---|---|---|---|---|---|
| ptrace   | measured (ref) | measured (ref) | measured (ref) | measured (ref) | measured (ref) | measured (ref) |
| kvm      | measured | measured | measured | measured | measured | measured |
| **DBT**  | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` |
| **SaBRe**| `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` |
| **LiteInst** | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` |

Absence vocabulary (emitted in the CSV's `absence_reason` column; an **empty**
value means the cell was genuinely measured):

| token | meaning |
|---|---|
| `not_collected` | backend is known and the tool supports it, but it was not in the requested `--backends` set. Nobody asked. |
| `unsupported` | the tool has no launcher for that backend, or the backend name is unknown to the collector. |
| `unavailable` | host/artifact gate unmet — no `/dev/kvm`, or the launcher binary is not built. |
| `no_result` | the launcher ran but emitted no parseable syscall count. |

- **DBT / SaBRe / LiteInst are `unsupported`, which is a LAUNCHER GAP, not a
  backend fault.** The reverie examples ship no `reverie-{dbt,sabre,liteinst}-counter*`
  binaries, so there is nothing to invoke. Adding an entry to that tool's
  `launchers` map in `collect-reverie-compat.rs` is the only change needed to
  start measuring them.
- **Do not read these cells as failures.** All three backends produce 200 real
  rows each in `fullcorpus-scorecard.csv` from this same checkout, so host
  capability is demonstrated. Their absence here is specific to the Reverie
  example launchers.
- **KVM has data** because both of its gates were satisfied: a `kvm` launcher
  exists for both tools (`reverie-kvm-counter1/2`) **and** `/dev/kvm` is present
  on the measuring host. Either gate failing would have produced `unavailable`,
  not silence.

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
- **Reverie** (Table 2, a **disjoint** dataset — never add it to the 200) —
  **6 programs** (counter1/counter2 × true/echo-hi/pwd). ptrace **6/6 pass**
  (reference); KVM **0/6 tool-count parity, 6/6 determinism**, constant −4
  syscalls. DBT / SaBRe / LiteInst: **`unsupported` — no example launcher
  exists**, which is a launcher gap, NOT a backend failure. At hermit
  `2f3689bd`, reverie `a4f33d69`, run 2026-08-01.

## Regenerate

**Table 1** (Hermit Detcore envelope):

```bash
# Full-corpus enumeration (static, no test execution):
#   corpus-manifest.csv = every ptrace-verify cell + calibration state + L1 data
# Calibrated-subset scorecard (needs a quiet host / boxed cgroup lane for L2):
compat-envelope/collect-envelope.rs --mode regression --lane portable \
  --repo <hermit-checkout> --backends ptrace,dbi,sabre --with-parity \
  --csv compat-envelope/scorecard.csv
compat-envelope/render-scorecard.rs --csv compat-envelope/scorecard.csv --all
```

**Table 2** (Reverie Guest/Tool boundary — a *separate* producer and a *separate*
CSV; the two tables are assembled into this document independently):

```bash
# Emits one row per (tool x guest x KNOWN backend) = 6 programs x 5 backends = 30
# rows, every unmeasured cell carrying a typed absence_reason. Requires the
# reverie counter launchers to be built in reverie/target/debug.
compat-envelope/collect-reverie-compat.rs \
  --repo <hermit-checkout> --csv compat-envelope/reverie-scorecard.csv
```

- **Regeneration is idempotent.** The collector REPLACES this bucket's rows
  instead of appending, so re-running converges rather than accumulating
  duplicates; rows from other buckets in the same CSV are preserved untouched.
  Pinning `--run-id` and `--run-utc` makes two runs **byte-identical**, which is
  what `tests/test_collect_reverie_backends.sh` check 8 asserts (3 consecutive
  runs, same sha256, 30 rows each). `--append` restores the old accumulating
  behaviour and is check 9's control.
- **Coverage/absence tests:** `compat-envelope/tests/test_collect_reverie_backends.sh`
  — 10 checks covering all five backends, all four absence tokens, both
  bracketing directions, DBT/legacy-`dbi` naming, unknown-backend refusal,
  idempotence, and foreign-bucket preservation.
- **Launcher provenance caveat (open).** The collector stamps `reverie_sha` from
  the reverie *checkout* HEAD, but measures whatever binaries are in
  `target/debug`. If those binaries are stale relative to HEAD, the row's SHA
  overstates what was measured. Rebuild the launchers before regenerating, or
  treat the stamped SHA as the checkout, not the binary.
