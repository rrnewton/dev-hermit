# Fixed-179 cross-backend INFO, stack, and heap parity sweep

## Outcome

On the fixed population of 179 historical deterministic ptrace-pass tests, no
candidate backend produced a strict full-depth pass:

| backend | INFO-under-stack+heap flags | stack | heap |
| --- | ---: | ---: | ---: |
| DBI | 0 / 179 | 0 / 179 | 0 / 179 |
| KVM | 0 / 179 | 0 / 179 | 0 / 179 |
| SaBRe | 0 / 179 | 0 / 179 | 0 / 179 |
| LiteInst | 0 / 179 | 0 / 179 | 0 / 179 |
| e9patch | 0 / 179 | 0 / 179 | 0 / 179 |

This is a measured gap report, not a claim that the backends have zero useful
coverage. A pass required a stable, nonempty ptrace reference/control pair,
explicit backend engagement, successful exits, equal stdout, and an exact
nonempty dimension comparison. Failed, absent, or non-engaged cells remain in
the denominator.

Pure INFO-only was **not run**. INFO was captured in the same invocation as
`--detlog-stack --detlog-heap`, so its tier is named `INFO-under-stack+heap`
throughout this report.

## Provenance

- Measurement Hermit: `0041130ccb0daa54ffe7dce2792c1f1495c57e58`
- Runtime Reverie pin: `0ae0c01b5e4c9fbf85c97adc66c2740f280727df`
- Runtime LiteInst2 pin from Hermit's `Cargo.lock`:
  `95ee5e6917fa33191eb41c3f1606ea8b03c1b78c`
- Installed Hermit binary SHA-256:
  `0ee947522db96beeefd657c970ac18ada8f932212c0bb11dc60fa6f058e43300`
- Denominator: 179 distinct test IDs, SHA-256
  `1dd6b79c57f790eb2585206fccdb2228ee003623c66ed2079efa0fb890a6bb10`
- Collection: 1,253 / 1,253 run receipts (`179 x 7` arms) and 3,222
  comparison rows (`179 x 6` targets `x 3` dimensions).
- The raw 6.9 GiB corpus is intentionally not committed. This directory
  preserves the complete compact comparison rows, denominator, producer,
  independent verifier, and both-side comparator bracket.

The historical denominator comes from the 179 distinct rows with
`run_id=ptrace-fullcorpus-scorecard`, `outcome=pass`, and `deterministic=1` in
`compat-envelope/fullcorpus-scorecard.csv`, whose source Hermit is
`82a8e853357584a3a567fd80812e015572a607c7`. The preserved producer
`summary.json` contains a malformed SHA in its human-readable `origin` field;
the denominator bytes and digest are correct, and `metadata.json` records the
correct full SHA and the source CSV digest.

At publication time the fresh tips were parent
`c25bdb72992347735727a0c9f4391ab8d21ec08f`, Hermit
`75506005d873a76f62be00b1d82696188651047a`, Reverie
`6144323c5dab8b521278fce206f8774360c2b05f`, and standalone LiteInst2
`8bf704feb06a62e7a05bee3b237d70793e4e2689`. The Hermit measurement is not
relabeled as a run at the newer tip. The complete `0041130..75506005` source
diff is only two logically equivalent Clippy rewrites (`a == !b` to `a != b`,
and negated `is_some_and` to `is_none_or`); no collector, memory, dispatch, or
logging behavior changed.

## Method and pre-launch cost

The fixed denominator was frozen before execution. A naive design with a
separate reference/candidate pair for five candidates and three signals would
have required 5,370 invocations and an estimated 188.9 serialized minutes.
The executed combined design captured all three dimensions in one invocation
and shared the ptrace reference/control arms: 1,253 runs, with a historical
duration lower-bound estimate of 54.4 serialized minutes before flag and
boxing overhead. The filesystem-mtime collection window is recorded in
`metadata.json`; it is provenance, not a backend timing result.

Each ID ran these arms: ptrace reference, ptrace control, DBI, KVM, SaBRe,
LiteInst, and e9patch. `run-one.sh` records exit status, stdout, engagement,
source channel, and events. DBI and SaBRe use a labeled `stderr_fallback` when
their trace is not wholly routed to the requested log file. e9patch is engaged
only when `mapped_sites > 0`. These conditions travel with every row in
`results.csv`.

The producer contract is `BitwiseInfoV1`: remove the host wall-clock logger
prefix, ordinalize only explicit `<hostaddr 0x...>` markers, and compare every
other byte exactly. An independent bounded-memory verifier
(`analyze-raw-corpus.py`) is stricter: it removes only the wall-clock prefix.
The raw audit found `<hostaddr>` markers in 0 / 1,245 event files, so the
producer's marker canonicalizer was inert and both verifiers exercise the same
payload bytes.

Virtual time was never rounded, frozen, coarsened, reset, or stripped. Syscall
ordinals/results, guest and region addresses, sizes, flags, hashes, and
multiline content remain exact. This sweep compares observed trajectories; it
does not independently certify continuous-time correctness or change any time
implementation.

## Before and after

The historical stdout-only figures are upper bounds, not parity claims. The
current observations are also shown only to expose how much a stdout proxy
overstates the deeper result.

| backend | historical stdout equality / 179 | current raw stdout equality / 179 | current engagement-qualified stdout / 179 | strict INFO / 179 | strict stack / 179 | strict heap / 179 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DBI | 136 | 138 | 137 | 0 | 0 | 0 |
| KVM | 112 | 123 | 123 | 0 | 0 | 0 |
| SaBRe | 141 | 136 | 136 | 0 | 0 | 0 |
| LiteInst | 108 | 114 | 114 | 0 | 0 | 0 |
| e9patch | 172 | 174 | 4 | 0 | 0 | 0 |

`strict-verdict.json` preserves every mutually exclusive non-pass bucket. Each
backend/dimension bucket sums to exactly 179. The universal ptrace control
gate, before candidate classification, was:

| dimension | exact ptrace-control pass | divergence | zero result | reference run failure | missing fixture |
| --- | ---: | ---: | ---: | ---: | ---: |
| INFO-under-stack+heap | 154 / 179 | 21 | 0 | 3 | 1 |
| stack | 160 / 179 | 14 | 1 | 3 | 1 |
| heap | 167 / 179 | 4 | 4 | 3 | 1 |

The comparator bracket is non-vacuous. On
`backend-parity-c/pid-probe`, the positive exact comparison accepted nonzero
records for INFO `302 / 302`, stack `81 / 81`, and heap `2 / 2`. A one-record
tamper was refused at index 0 in all three dimensions.

## Measured gaps and owner gate

- KVM produced no qualifying stack or heap records in the strict verifier.
- DBI produced qualifying heap evidence in only 1 / 179 cells; 129 cells that
  reached candidate classification had zero heap records.
- DBI and SaBRe rely on explicitly labeled stderr fallback evidence.
- e9patch engaged the rewrite path for only 4 / 179 IDs; all four eligible
  INFO comparisons diverged.
- The ptrace reference/control oracle itself diverged in 21 INFO, 14 stack,
  and 4 heap cells. One retained fixture,
  `applications/timed-progress-bar`, was absent at the measurement SHA.
- The runner did not pass the current collector's
  `--base-env minimal -e LC_ALL=C -e TZ=UTC` flags. These are raw
  current-launch-path observations, not environment-normalized semantic
  parity.

The live source confirms that patching arms are not ready for performance
optimization while a ptracer remains in their syscall path:

- LiteInst's CLI calls `run_host_with_preload::<Detcore>`; Reverie documents
  that ptrace owns the sole Tool/GlobalTool and every installed hook returns
  through the ptrace-host SIGTRAP path.
- e9patch reports `preprocessing + ptrace runtime`; Reverie keeps ptrace for
  lifecycle, shared-library syscalls, signals, timers, and arbitrary `Guest`
  operations. Its public `Backend::run` remains ptrace-hosted.
- SaBRe's CLI calls `sabre_ptrace::run`, which attaches, resumes with
  `PTRACE_SYSCALL`, handles syscall stops, and resumes through ptrace.

Accordingly, this experiment makes no patching-backend performance claim and
proposes no performance optimization. Those cells are parity/reachability
evidence with ptracer provenance.

## Reproduction and audit

From this directory:

```sh
python3 verify-published.py
```

This checks all source-file digests, denominator uniqueness, the complete
`179 x 6 x 3` key space, summary recomputation from CSV rows, strict bucket
arithmetic, zero candidate passes, and both comparator-bracket directions.
Re-running the raw sweep requires adapting the preserved scripts' absolute
workspace paths and reconstructing the omitted raw corpus. The scripts are
published byte-for-byte as executed so such adaptation is visible rather than
mistaken for the original run.

This is a research artifact on `rrnewton/dev-hermit:main`. It has no product
branch, PR, or worktree slot. Related Hermit PRs #1709 (diagnostic diff
plumbing, head `bc461a2608e2d7dca2f56293312e9bc2aa270182`) and #1778
(INFO-tier mutation plumbing, head
`e58457a303c6b0bef0102cf013f5c2309f51999c`) were not modified.
