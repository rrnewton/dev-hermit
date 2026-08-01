# KVM full-corpus scorecard sweep — parity% + determinism% (2026-08-01)

## Question

The compat-envelope scorecard (`compat-envelope/scorecard.csv`) had **zero KVM
rows**. The owner wants real KVM DATA in the scorecard: for every cell in the
full verify-mode corpus, how does the Reverie **KVM backend** compare to the
golden **ptrace** reference?

Two per-cell dimensions, both under `--strict`:

- **parity%** — KVM stdout is byte-identical (SHA-256) to ptrace stdout, both
  exiting 0. This is cross-backend agreement against the golden reference.
- **determinism%** (L2) — KVM `--strict --verify` exits 0, i.e. KVM's own
  double-execution is bitwise-repeatable. det% >= parity% by construction (a
  cell can be self-deterministic yet diverge from ptrace).

The denominator is 235's authoritative `compat-envelope/corpus-manifest.csv`
(200 verify-mode cells). This sweep measures the **full 200** — a strict
superset of the prior KVM sweep (`kvm_b3_corpus_sweep_20260730`, 183 C cells).

## Method

Direct `hermit run --backend kvm` invocation, **bypassing manifest
enabled/disabled gating**. This is required: the test harness
(`ci/test_harness.sh run --mode verify --backend kvm`) only runs
manifest-ENABLED backend cells, so it cannot measure KVM determinism on the 177
cells where KVM is manifest-disabled ("no required test cells"). The bypass is
the same methodology as `kvm_b3_corpus_sweep_20260730`. Research/data-collection
only — no product code changed.

Per cell, three timed runs (env `LC_ALL=C TZ=UTC`, portable-lane flags
`--no-virtualize-cpuid --max-timeslice=disabled`):

1. `hermit run --strict $flags -- <guest>`               → ptrace reference hash
2. `hermit run --backend kvm --strict $flags -- <guest>` → KVM run + parity hash
3. `hermit run --backend kvm --strict --verify $flags -- <guest>` → L2 det (timed)

- **det = 1** iff the KVM `--verify` run exits 0.
- **parity = 1** iff both the ptrace and KVM single runs exit 0 and their
  stdout hashes are equal; **parity = blank** (unmeasurable) iff the ptrace
  reference itself does not exit 0 (16 cells), so cross-backend agreement is
  undefined — reported honestly, never counted as a KVM pass or fail.

Timeouts: 30 s per single run, 60 s per verify run.

### Two cell groups

- **184 C cells** (`sweep.sh`, `corpus.tsv`): compiled from the `.c` sources +
  `extra_sources` in the current primary manifests, into a gitignored build
  tree. Parallel (`PAR=12`).
- **16 non-C cells** (`sweep-nonc.sh`, `corpus-nonc.tsv`): interpreter/shell
  guests (`.sh --run` wrappers → python/perl/gawk/bash/openssl, and shell
  pipelines). Four of these (`archive-roundtrip`, `shell-pipeline`,
  `process-chains`, `thread-contention`) require the harness fixture protocol
  (native `--prepare` to build fixtures + `E2E_TMPDIR`/`E2E_FIXTURE_DIR`); those
  were re-run faithfully with that env so the ptrace side succeeds and the KVM
  result is a genuine measurement, not a harness artifact.

## Results

`hermit 82a8e853…`, `reverie a4f33d69…`, host devbig014 (real `/dev/kvm`).

**Combined 200-cell KVM verify-mode column:**

| dimension          | count            | note                                       |
|--------------------|------------------|--------------------------------------------|
| determinism (L2)   | **130/200 = 65.0%** | KVM `--strict --verify` exits 0             |
| parity vs ptrace   | **112/184 = 60.9%** | of measurable cells                         |
| parity unmeasurable| 16               | ptrace reference itself non-zero exit        |
| outcome pass/fail  | 130 / 70         | pass ≡ det=1                                 |

- **C cells (184):** det 122/184 (66.3%); parity 106/168 measurable (63.1%);
  16 ptrace-side-fail (unmeasurable). Matches the 07-30 sweep (105/183 =
  63.3%) — same numbers at a fresh, denominator-aligned SHA, now with the added
  L2 det dimension.
- **non-C cells (16):** det 8/16; parity 6/16. KVM runs deterministically for
  `python-random`, `python-hash-determinism`, `gawk-random`, `openssl-passwd`,
  `proc-uptime`, `random-device`, `timed-progress-bar`, `thread-output`
  (byte-parity on 6 of these). It fails the subprocess-spawning / fixture-heavy
  cells (`bash-loop-pipe-time`, `python-io-subprocess-time`,
  `perl-io-subprocess-time`, `date-nanoseconds`, `archive-roundtrip`,
  `shell-pipeline`, `process-chains`, `thread-contention`) — genuine KVM reds
  (ptrace passes; KVM exit 1 / timeout).

**Stability check:** the 6 verify-timeout C cells (`robust-futex-test`,
`dbi-pid-virtualization`, `fp-reduction-nondeterminism`, `mmap-fork-shared`,
`signal-order`, `thread-stress`) were all `kvm_exit=124` DIFF in the 07-30
sweep too — reproducible KVM thread/futex/signal/fork hangs, not load-induced
false negatives.

## Scorecard emission

Output is in the exact 19-column `scorecard.csv` schema
(`backend=kvm`, `test_mode=verify`, `run_mode=expansion`,
`run_id=kvm-fullcorpus-scorecard`), keyed on
`(bucket,test_id,test_mode,backend)`. Rows concatenate losslessly into
`compat-envelope/scorecard.csv` — disjoint from every existing row by
`backend=kvm` (the file had 0 KVM rows before), so this is a pure +200-row
append that cannot collide with 235's concurrent ptrace-denominator expansion.

`cell_state` carries the manifest KVM-gating state for C cells (7 enabled, 177
disabled — KVM is manifest-enabled for only 7) and `expansion` for the 16 non-C.

### Render / denominator coordination (task: scorecard-full-manifest-denominator)

`render-scorecard.rs` computes each backend cell as a percentage of the
**ptrace** row count per bucket. At this SHA the committed scorecard has ptrace
rows for only 48 of 200 cells, so the rendered KVM percentages are currently
denominator-limited to those buckets. The **raw KVM data is complete for all
200 cells** (this experiment's `scorecard-kvm-full.csv`); the full-corpus KVM
percentages (65% det / 61% parity) surface in the render automatically once 235
lands the 200 ptrace rows. No KVM re-run is needed then.

## Files

- `corpus.tsv` / `corpus-nonc.tsv` — the 184 C + 16 non-C cell definitions.
- `sweep.sh` / `sweep-nonc.sh` — the measurement drivers.
- `scorecard-kvm.csv` (184 C), `scorecard-kvm-nonc.csv` (16 non-C),
  `scorecard-kvm-full.csv` (**authoritative 200-cell union**).
- `metadata.json` — SHAs, host, toolchain, commands.
- `rows/`, `rows-nonc/` — per-cell raw rows (assembled into the CSVs).

## Reproduction

```bash
cd ~/work/dev-hermit
# builds hermit debug binary if absent: (cd hermit && cargo build)
PAR=12 bash experiments/kvm_fullcorpus_scorecard_20260801/sweep.sh
PAR=8  bash experiments/kvm_fullcorpus_scorecard_20260801/sweep-nonc.sh
# fixture-dependent non-C cells need native --prepare + E2E_TMPDIR/E2E_FIXTURE_DIR
# (see sweep-nonc.sh comments); requires real /dev/kvm.
```
