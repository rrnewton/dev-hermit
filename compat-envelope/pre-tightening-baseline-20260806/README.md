# PRE-TIGHTENING compat-envelope baseline — 2026-08-06 (HISTORICAL)

> # ⚠️ PRE-TIGHTENING / HISTORICAL — THIS IS A *BEFORE* SNAPSHOT
>
> This file exists **only** to be the matched before-state for an upcoming
> **strictness tightening** of the compat-envelope comparison contract. It is a
> point-in-time measurement of the corpus as it behaved **under the OLD,
> LOOSER contract**, taken so that the after-state can be diffed against
> something measured hours — not days — earlier, on the same host, with the
> same corpus and the same backend matrix.
>
> **Do not quote these numbers as current compatibility status.** They will be
> superseded by the after-state run, and they are not expected to survive the
> tightening unchanged — a drop after tightening is the *point* of tightening,
> not a regression.
>
> **No cell below is a bitwise certification.** Every `deterministic=1` here was
> produced by the **stripped** comparator. See §4.

---

> **Independently replicated.** A second agent swept the same matrix
> concurrently at hermit `1fadc037` (one commit later) and at a different
> concurrency width; the two runs agree within **±1 cell of 205 per backend**.
> That bounds the measurement noise, so a before/after delta of |Δ| ≤ 1 per
> backend is not evidence of anything. See
> [`cross-check-w14.md`](cross-check-w14.md).

## 1. What this is, in one paragraph

A single-sweep, single-host, single-SHA measurement of the full compat-envelope
corpus across every Detcore backend that would run on this box, produced by the
tracked collector `compat-envelope/collect-fullcorpus.sh` and rendered by the
tracked renderer `compat-envelope/render-scorecard.rs`. Unlike the standing
`compat-envelope/SCORECARD-CURRENT.md` — whose Table 1 is a 2026-08-01 sweep at
Hermit `82a8e853` — every row here was measured in one window at one Hermit SHA,
so it has no mixed-provenance problem.

## 2. Provenance — bind every number to this table

| what | exact value |
| --- | --- |
| Hermit source | `4c70658e785834737cbe1524f77330c781a6f5ea` (`rrnewton/hermit origin/main, freshly fetched 2026-08-07T01:59Z`) |
| Hermit binary `--version` stamp | `hermit 0.2.0 (2026-08-07, g4c70658e7858)` |
| Reverie checkout | `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` (`rrnewton/reverie origin/main, freshly fetched 2026-08-07T01:59Z`) |
| Reverie the binary actually links | `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` (from `hermit/Cargo.lock`) |
| Parent producer commit | `1cbe6331986b3f83ae4bf7e886e2f2e0505ddadd` |
| Producer script | `compat-envelope/collect-fullcorpus.sh` sha256 `6f8b532d5e581bef125349e6f34ebb7065870e2eda7ef13799354674655fae11` |
| Renderer | `compat-envelope/render-scorecard.rs` sha256 `14cebb4fb9e11b3ae3902c963093a5ea540c9c3650560cd2e3a509600f4a52b8` |
| Corpus (C) | `compat-envelope/corpus/corpus-c.tsv` sha256 `54ed0ed059967a881cc9c0bdc1f24d5253fcd27cec54ca9f1c056e3adbf3c78c` |
| Corpus (non-C) | `compat-envelope/corpus/corpus-nonc.tsv` sha256 `3184ff833ac1fd7f84d5f7adba5efbd32fcfc4607a9b8242b24e0087139c9bf6` |
| Host | `devbig014`, 316 cores, kernel `6.18.39-0_fbk0_hardened_0_ga43d5727b443` |
| Toolchain | gcc 11.5.0 20240719 (Red Hat 11.5.0-15); rustc 1.99.0-nightly (26ae60a9e 2026-07-28) |
| Sweep window (UTC) | 2026-08-07T02:17:02Z → 2026-08-07T03:47:25Z |
| `run_utc` stamped on every row | `@1786069022` |

Sweep parallelism `--par 16`, per-cell `timeout 90s` on the run leg and
`timeout 120s` on the verify leg. `--no-assert` was passed: this is a
measurement, not the green-stays-green gate, so a ratchet-floor drop records a
number instead of aborting the sweep.

**Host load is part of the measurement, not a footnote.** The box is shared with
~18 other agents and a second, independent full-corpus sweep was running
concurrently. Sampled 1-minute load average over the sweep window:
min 29.69, median 57.5, max 1054.85 across 212 samples on
316 cores (see `loadavg.tsv`). Read `duration_ms` in the CSV as
contended wall time, never as a benchmark. The pass / determinism / parity
fields are what this artifact reports.

## 3. The matrix — corpus × backend

- **Corpus:** 235 nominal cells (`corpus/corpus-c.tsv` + `corpus/corpus-nonc.tsv`),
  of which **205 were measurable** on this Hermit SHA. Every backend was
  run over the same 205 cells, so the columns are directly comparable.
- **Backends measured:** `ptrace`, `dbi`, `sabre`, `e9patch`, `liteinst`.
- **Backends absent (zero rows, `n/a`, NOT a zero score):** `kvm`.
- **Rows in `scorecard.csv`:** 1025.

**Denominator: 182.** As in the standing scorecard, a non-ptrace backend's
percentage is a fraction of the ptrace cells that themselves passed the golden
`--strict --verify` leg. Cells where ptrace did not pass are excluded from every
backend percentage — they are a ptrace gap, not a backend gap.

## 4. The comparison contract (the thing about to be tightened)

Per cell, per backend, exactly two Hermit invocations:

```
det     hermit [--backend B] run --strict --verify [--no-virtualize-cpuid --max-timeslice=disabled if lane=portable] --base-env minimal -e LC_ALL=C -e TZ=UTC -- <guest>
parity  hermit run --strict [lane flags] --base-env minimal -e LC_ALL=C -e TZ=UTC -- <guest>   (plain --strict, ptrace, NOT --verify)
```

- **`deterministic`** = the `--verify` leg exited 0. `--verify` with no
  `--verify-strict` selects the **stripped** comparator, which normalizes
  before comparing. Every row in `scorecard.csv` carries this explicitly in the
  `verify_compare` column, so the tier travels with the value rather than being
  inferred from prose.
- **`stdout_parity`** = SHA-256 of the backend's piped guest stdout equals
  SHA-256 of the ptrace reference's. The reference is captured with plain
  `--strict` (never `--strict --verify`, which double-runs internally and emits
  no guest stdout to the parent).

**What that contract does NOT establish.** `stripped` normalization has been
measured to miss DETLOG-only, address-value, and path-string divergence while
still printing `Determinism verified` — see Limitation L1 of
`compat-envelope/SCORECARD-CURRENT.md`. A green cell here rules out stdout and
exit-status divergence and nothing more. INFO logs, stack detlogs, heap detlogs,
and TTY behaviour are outside the observable entirely. **This looseness is the
motivation for the tightening; these numbers are what the loose contract said.**

## 5. Rendered scorecard

`render-scorecard.rs --csv scorecard.csv --all --backends dbi,kvm,sabre,e9patch,liteinst`

```
Compat-envelope scorecard  (run: ALL (last-writer-wins), denominator: verify = tests passing golden ptrace strict+replay (verify))
Input CSV: compat-envelope/pre-tightening-baseline-20260806/scorecard.csv
Each backend cell is `stdout-parity%, determinism%` of the ptrace count. The two measurements are independent.
CAVEAT: stdout-parity% compares piped guest stdout SHA-256 only. It is an upper bound on four-signal cross-backend parity; INFO logs, stack detlogs, and heap detlogs are not measured. TTY behavior is also outside this scorecard.
stdout-parity suffix: `?` = the observable was never compared for that bucket (UNKNOWN, not confirmed 0); `~` = partial coverage (some cells unmeasured).
`n/a` = backend not runnable here (binary absent / not enabled) — 0 cells run, NOT a confirmed fail.

bucket                  ptrace               dbi               kvm             sabre           e9patch          liteinst
------------------------------------------------------------------------------------------------------------------------
applications                 1        100%, 100%               n/a        100%, 100%        100%, 100%        100%, 100%
backend-parity-c             3        100%, 100%               n/a          67%, 67%        100%, 100%          67%, 67%
bin-c                        1        100%, 100%               n/a        100%, 100%        100%, 100%        100%, 100%
c-programs                 148          84%, 87%               n/a          83%, 86%          99%, 99%          74%, 76%
chaos-c                      1        100%, 100%               n/a        100%, 100%        100%, 100%            0%, 0%
data-handling                0            0%, 0%            0%, 0%            0%, 0%            0%, 0%            0%, 0%
debugger-c                   1        100%, 100%               n/a        100%, 100%        100%, 100%        100%, 100%
determinism-stress           3          33%, 33%               n/a        100%, 100%        100%, 100%            0%, 0%
determinism-stress-c         9          67%, 78%               n/a          44%, 44%        100%, 100%            0%, 0%
language-runtimes            7          29%, 57%               n/a          29%, 57%        100%, 100%          14%, 14%
shared-futex-c               0            0%, 0%            0%, 0%            0%, 0%            0%, 0%            0%, 0%
system-utils                 8          50%, 88%               n/a          50%, 88%        100%, 100%          25%, 25%
util-c                       0            0%, 0%            0%, 0%            0%, 0%            0%, 0%            0%, 0%
------------------------------------------------------------------------------------------------------------------------
TOTAL                      182          79%, 85%               n/a          78%, 84%          99%, 99%          65%, 66%
```

Each backend cell is `stdout-parity%, determinism%`. The two are independent
signals: a backend can reproduce its own wrong answer (100% determinism, 0%
parity) or match ptrace once without being reproducible.

**Do not read every `0%, 0%` as a red.** The buckets `data-handling`, `shared-futex-c`, `util-c` have a
**zero ptrace denominator** in this sweep — `0/0` formats as `0%` but carries no
information. A genuine red is `0%, 0%` over a non-zero denominator (`liteinst` on
`chaos-c`, `determinism-stress` and `determinism-stress-c` is a genuine red).
Read the `X/Y` counts in the TSV projection below before calling any cell a
failure.

### Exact fractions, with the measured/ran counts beside every percentage

```
bucket	ptrace	dbi_stdout_parity_pct	dbi_det_pct	dbi_stdout_parity_measured	dbi_ran	kvm_stdout_parity_pct	kvm_det_pct	kvm_stdout_parity_measured	kvm_ran	sabre_stdout_parity_pct	sabre_det_pct	sabre_stdout_parity_measured	sabre_ran	e9patch_stdout_parity_pct	e9patch_det_pct	e9patch_stdout_parity_measured	e9patch_ran	liteinst_stdout_parity_pct	liteinst_det_pct	liteinst_stdout_parity_measured	liteinst_ran
applications	1	100.0	100.0	1/1	1/1	0.0	0.0	0/1	0/1	100.0	100.0	1/1	1/1	100.0	100.0	1/1	1/1	100.0	100.0	1/1	1/1
backend-parity-c	3	100.0	100.0	3/3	3/3	0.0	0.0	0/3	0/3	66.7	66.7	3/3	3/3	100.0	100.0	3/3	3/3	66.7	66.7	3/3	3/3
bin-c	1	100.0	100.0	1/1	1/1	0.0	0.0	0/1	0/1	100.0	100.0	1/1	1/1	100.0	100.0	1/1	1/1	100.0	100.0	1/1	1/1
c-programs	148	83.8	87.2	148/148	148/148	0.0	0.0	0/148	0/148	83.1	86.5	148/148	148/148	99.3	99.3	148/148	148/148	74.3	76.4	148/148	148/148
chaos-c	1	100.0	100.0	1/1	1/1	0.0	0.0	0/1	0/1	100.0	100.0	1/1	1/1	100.0	100.0	1/1	1/1	0.0	0.0	1/1	1/1
data-handling	0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0
debugger-c	1	100.0	100.0	1/1	1/1	0.0	0.0	0/1	0/1	100.0	100.0	1/1	1/1	100.0	100.0	1/1	1/1	100.0	100.0	1/1	1/1
determinism-stress	3	33.3	33.3	3/3	3/3	0.0	0.0	0/3	0/3	100.0	100.0	3/3	3/3	100.0	100.0	3/3	3/3	0.0	0.0	3/3	3/3
determinism-stress-c	9	66.7	77.8	9/9	9/9	0.0	0.0	0/9	0/9	44.4	44.4	9/9	9/9	100.0	100.0	9/9	9/9	0.0	0.0	9/9	9/9
language-runtimes	7	28.6	57.1	7/7	7/7	0.0	0.0	0/7	0/7	28.6	57.1	7/7	7/7	100.0	100.0	7/7	7/7	14.3	14.3	7/7	7/7
shared-futex-c	0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0
system-utils	8	50.0	87.5	8/8	8/8	0.0	0.0	0/8	0/8	50.0	87.5	8/8	8/8	100.0	100.0	8/8	8/8	25.0	25.0	8/8	8/8
util-c	0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0	0.0	0.0	0/0	0/0
TOTAL	182	79.1	85.2	182/182	182/182	0.0	0.0	0/182	0/182	78.0	83.5	182/182	182/182	99.5	99.5	182/182	182/182	64.8	66.5	182/182	182/182
```

## 6. Execution counts — what actually ran

Raw outcome tallies straight off `scorecard.csv`, before any percentage is taken.
These are the counts the denominators are built from; a percentage without them
is unqualified.

| backend | rows | pass | diverge | timeout | fail | skip | det=1 | stdout_parity=1 | stdout_parity=blank |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ptrace` | 205 | 182 | 21 | 2 | 0 | 0 | 182 | 0 | 205 |
| `dbi` | 205 | 157 | 41 | 7 | 0 | 0 | 157 | 144 | 22 |
| `sabre` | 205 | 152 | 47 | 6 | 0 | 0 | 152 | 142 | 22 |
| `e9patch` | 205 | 182 | 22 | 1 | 0 | 0 | 182 | 181 | 22 |
| `liteinst` | 205 | 121 | 84 | 0 | 0 | 0 | 121 | 118 | 22 |

`stdout_parity=blank` means *unmeasured*, not failed: the ptrace reference for
that cell was itself unusable, so no comparison was possible.

## 7. No-results — recorded, never silently dropped

| class | scope | cells | why it produced no result | evidence |
| --- | --- | ---: | --- | --- |
| `corpus-source-missing` | all backends (whole rows absent) | 30 | the parent corpus lists 30 `performance/*` cells whose C sources `tests/e2e/performance/*.c` do not exist in Hermit at this SHA, so no guest was built and `collect-fullcorpus.sh` skipped the cell without writing a row | `cc1: fatal error: .../tests/e2e/performance/mutex-contention.c: No such file or directory` x30 in `sweep-transcript.txt`; the fixtures live on Hermit `57efc446b` = open PR rrnewton/hermit#1727, which is not an ancestor of Hermit main |
| `backend-unrunnable` | `kvm` column (all 205 cells) | 205 | KVM does not complete even a trivial non-strict guest on this host, so `have_backend` dropped it and it contributed zero rows; the column is `n/a` (not measurable), never a zero score | `hermit --backend kvm run -- /bin/true` did not return within 180s at host 1-min load 160/316 cores; the same binary returns rc=0 for ptrace, dbi, sabre, e9patch and liteinst |
| `parity-reference-unusable` | 4 measured non-ptrace backends x 22 cells | 88 | the golden ptrace plain `--strict` reference run failed for these 22 cells, so `stdout_parity` is recorded blank (unmeasured) rather than a false empty-vs-empty match | `ptv.fail` marker written by `collect-fullcorpus.sh`; identical 22-cell set for dbi, sabre, e9patch and liteinst (verified by set comparison); the 22 test_ids are enumerated in README section 7 |
| `observable-not-applicable` | `ptrace` column (all 205 cells) | 205 | ptrace IS the stdout-parity reference, so it has no `stdout_parity` value by construction — its blank column is a definition, not a gap | `stdout_parity` is blank on all 205 ptrace rows; the renderer reports ptrace as an integer denominator, not as a percentage pair |
| `verify-timeout-environment-suspect` | ptrace 2, dbi 7, sabre 6, e9patch 1, liteinst 0 | 16 | the `--verify` leg hit the 120s cap and is recorded `outcome=timeout, deterministic=0` — a conservative RED, not a no-result. Each was already re-run serially by the collector's repair pass and still timed out, but the box carried a concurrent second full-corpus sweep and a 1-min load excursion to 1054 on 316 cores, so these are the rows most likely to move on a quieter host | `outcome=timeout` rows in `scorecard.csv`; `repair: re-running N timed-out <backend> cell(s) serially` lines in `sweep-transcript.txt`; load excursion in `loadavg.tsv` |

### The 22 cells whose ptrace stdout reference was unusable

Derived from `scorecard.csv`, not restated by hand; the set is byte-identical
across `dbi`, `sabre`, `e9patch` and `liteinst`, which is what you would expect
if the cause is the shared reference rather than any one backend.

- `applications/timed-progress-bar`
- `bin-c/robust-futex-test`
- `c-programs/dbi-wait-lifecycle`
- `c-programs/epoll-determinism`
- `c-programs/ipc-determinism`
- `c-programs/liteinst-advanced`
- `c-programs/mmap-determinism`
- `c-programs/nanosleep-threads-simple`
- `c-programs/record-replay-lseek-seek-cur`
- `c-programs/signal-determinism`
- `c-programs/socket-ioctl-timestamp`
- `c-programs/thread-sync-determinism`
- `data-handling/archive-roundtrip`
- `data-handling/shell-pipeline`
- `determinism-stress-c/thread-contention`
- `determinism-stress/process-chains`
- `determinism-stress/thread-contention`
- `shared-futex-c/qemu-exec-init`
- `shared-futex-c/qemu-hello`
- `shared-futex-c/qemu-init`
- `shared-futex-c/qemu-net-init`
- `util-c/pmu-skid`

## 8. The collector's ratchet floors do NOT apply to this run

the collector's per-backend det floors are NOT satisfiable on this corpus and must not be read as regressions

the floors are absolute cell counts calibrated on a 235-cell corpus; only 205 cells are buildable at this Hermit SHA, so ptrace (floor 214) and e9patch (floor 214) exceed the number of cells that can even run

| backend | det | of measurable cells | collector floor | floor reachable? |
| --- | ---: | ---: | ---: | --- |
| `ptrace` | 182 | 205 | 214 | no — floor exceeds the cell count |
| `dbi` | 157 | 205 | 190 | reachable, not met — unranked |
| `sabre` | 152 | 205 | 199 | reachable, not met — unranked |
| `e9patch` | 182 | 205 | 214 | no — floor exceeds the cell count |
| `liteinst` | 121 | 205 | 118 | reachable, met |

So the four `REGRESSION:` lines the collector printed compare an absolute count
taken over 205 cells against a floor calibrated over 235, and **none of them is
evidence that a backend got worse**:

- For `ptrace` and `e9patch` the floor is provably unsatisfiable — it exceeds
  the number of cells that exist to run.
- For `dbi` and `sabre` the floor is numerically reachable, but the shortfall
  cannot be separated from the 30 missing cells without re-calibrating: whatever
  share of those 30 the backend used to pass was counted into the floor and is
  now unavailable. Treat these two as **unranked against the floor**, not as
  passes and not as regressions.
- `liteinst` clears its floor, which is a lower bar than it looks for the same
  reason.

`--no-assert` was passed precisely so this would be recorded rather than abort
the sweep. Re-calibrating the floors is out of scope here (this task makes no
product fixes); it needs either PR rrnewton/hermit#1727 landed so the 30
`performance/*` cells build again, or floors expressed as rates.

## 9. Producing the matched AFTER-state

Change **only** the comparison contract. Everything else in §2 and §3 must be
held fixed, or the diff measures the wrong variable:

```
HERMIT_REPO=<hermit checkout> REVERIE_REPO=<reverie checkout> HERMIT_BIN=<hermit>/target/release/hermit \
  compat-envelope/collect-fullcorpus.sh --backends ptrace,dbi,sabre,e9patch,liteinst \
  --par 16 --no-assert --out <out>.csv
```

Then render with the same renderer, and diff §5 and §6 against this file. If the
corpus, the Hermit SHA, the host, or the backend set differs, say so and mark the
affected rows unranked rather than reporting a delta.

## 10. Regenerating this file

```
compat-envelope/pre-tightening-baseline-20260806/generate.py           # rewrite README.md
compat-envelope/pre-tightening-baseline-20260806/generate.py --check   # assert zero drift
```

`generate.py` reads only `scorecard.csv`, `metadata.json`, and `no-results.csv`,
and calls no clock — so an unchanged input set regenerates byte-identically.
