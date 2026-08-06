# Strict compat green-cell drop (2026-08-06)

This experiment is the fail-closed measurement harness for determining how many
of the frozen 235 test × 6 execution-path cells remain green when the
observation contract changes from Hermit's legacy lossy comparator to raw
repeat equality. Input preflight currently refuses the sweep because 31 of the
235 requested test sources are absent at the frozen Hermit SHA. No strict or
legacy numerator has been claimed.

The main denominator is exactly 1,410 cells and 2,820 executions. Each cell is
run twice. A strict green requires both executions to exit zero, nonzero log
event counts, and byte-for-byte equality of stdout, stderr, and the complete
ordered `--log=info --log-file` stream, plus identical exit code or terminating
signal. No prefix is stripped, no number or address is rewritten, and no log
line or event class is filtered. The old legacy verdict is computed from the
same two executions with the checked-in `hermit log-diff
--unsafe-strip-lines` comparator; it is never substituted from a historical
denominator.

Raw logs and build products stay under the assigned product slot's ignored
directory. This versioned directory contains the frozen inputs, typed JSONL,
hashes, verifier, and final report. `metadata.json` records the exact external
artifact root so hashes remain dereferenceable on the producing host.

## Frozen inputs

- Hermit: `4c70658e785834737cbe1524f77330c781a6f5ea`
- Reverie dependency: `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6`
- LiteInst2 checkout: `8bf704feb06a62e7a05bee3b237d70793e4e2689`
- LiteInst2 revision actually resolved by Hermit's Cargo.lock:
  `95ee5e6917fa33191eb41c3f1606ea8b03c1b78c`
- C corpus: 214 rows
- non-C corpus: 21 rows
- execution paths: ptrace, KVM, DBI, SaBRe, e9patch preprocessing with ptrace
  lifecycle, and LiteInst

The e9patch column is an execution-path compatibility measurement, not a claim
that e9patch is an independent Detcore backend.

`requested-manifest.json` freezes all 235 requested rows even when a source is
absent. Its SHA256 is
`078d5eb81fcac816ea4b58193770b17e0d85f94939b30ce9b79e3e07108615f4`.
This is deliberately distinct from `manifest.json`, which is not emitted until
all inputs exist and compile. `input-audit.json` binds that hash to the exact
repository SHAs and records `complete=false`, `full_sweep_allowed=false`, 235
observed/unique rows, 204 executable rows, and the complete missing-source
list.

The missing set comprises all 30 `performance/*` rows at C-corpus lines
178–207, whose `tests/e2e/performance/*.c` sources are absent, plus
`applications/timed-progress-bar` at non-C-corpus line 2. Hermit commit
`0c6cda68dbdd76cee3e77ed480f4533c6308486b` deliberately deleted the latter
wrapper and changed the product manifest to invoke
`./examples/timed-progress-bar.py` directly; the parent corpus was not updated.
The 30 performance wrappers and their three shared implementations exist on
closed PR #1444's branch (`origin/codex/long-running-multibackend-perf-tests`,
tip `e4586cc5ffb380a17fe1434bccfc67e84df4ca68`; component commits `952b1065`,
`469cfb50`, `e4586cc5`). A recovery audit compiled those sources 30/30, but the
whole closed PR must not be cherry-picked; they need a focused recovery onto
current main. The timed-progress wrapper was intentionally deleted by PR #1458
and another corpus row already invokes the same Python workload. The explicit
denominator decision therefore remains unresolved: retire the duplicate row
and report 234, or add a genuinely distinct workload to preserve 235.

## Reproduce

All commands run from this directory. The all-feature Hermit binary must first
be built at the frozen SHA:

```bash
cd /home/newton/work/dev-hermit/worktrees/strict-metric/hermit
CARGO_BUILD_JOBS=32 with-proxy cargo build --release -p hermit \
  --features third-party-backends --bin hermit

cd /home/newton/work/dev-hermit/experiments/strict_compat_green_cell_drop_20260806
./run.rs audit-inputs
./run.rs prepare --jobs 32
./verify.rs self-test
./run.rs run --kind calibration --jobs 6 --timeout-seconds 120
./verify.rs verify --kind calibration
```

`audit-inputs` currently exits nonzero after atomically writing typed evidence;
that is the required fail-closed result. Only after it, preparation, brackets,
and calibration all pass may the full sweep launch as a detached user unit with
a durable log:

```bash
systemd-run --user --unit=strict-compat-green-cell-drop-4c70658e \
  --working-directory=/home/newton/work/dev-hermit/experiments/strict_compat_green_cell_drop_20260806 \
  --setenv=HOME=/home/newton \
  --setenv=PATH=/home/newton/.cargo/bin:/usr/local/bin:/usr/bin:/bin \
  /bin/bash -lc 'exec ./run.rs run --kind full --jobs 16 --timeout-seconds 120 \
    > full-sweep.log 2>&1'
```

Then verify exact coverage and emit the summary, followed by the separate L3
spot checks (two short cells, all six paths, heap-only, stack-only, and both):

```bash
./verify.rs verify --kind full
./run.rs run --kind spot --jobs 6 --timeout-seconds 120
./verify.rs verify --kind spot
```

The verifier rejects missing or duplicate execution keys, missing or duplicate
cell keys, wrong denominators, zero-message greens, changed input/binary hashes,
tampered artifacts, and any producer verdict inconsistent with the raw bytes.
It also dereferences the input audit and both manifests, binds each recorded
command to the typed test/backend/mode and exact log path, and rejects malformed
exit/signal/timeout records.

## Results

Not measured: the input audit found 31 missing sources, so preparation refused
before guest compilation and no execution or numerator was produced.
`metadata.json` and `results.csv` encode that no-result state explicitly rather
than leaving blank fields to be mistaken for zero green cells.
`REPORT.md` is generated only after all 2,820 main executions are accounted for
exactly once and the verifier succeeds.
