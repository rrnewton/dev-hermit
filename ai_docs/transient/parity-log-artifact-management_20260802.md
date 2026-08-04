# Parity log artifact management

Date: 2026-08-02

Task: `parity-log-artifact-management`

## Decision

Use one deterministic `tar.zst` transport artifact per
`lane x mode x manifest-block` job. Keep the build artifact separate. Both are
one-day, same-workflow transport artifacts and are deleted after the reducer
runs. The reducer publishes only the compact scorecard/JUnit output and selected
failure diagnostics with the normal 14-day diagnostic retention.

Use the same directory and archive contract locally. A local validate run writes
under one ignored run root and hands paths to the reducer; GitHub uploads the
same archives between jobs. The reducer must not know whether its inputs came
from a local directory or `actions/download-artifact`.

## Current state

### Local full-corpus validate

The parent `make validate` builds once and invokes the 235-test full-corpus
collector ([Makefile](https://github.com/rrnewton/dev-hermit/blob/3f391f88e854d62be233c25012d1895f03b937a1/Makefile#L137-L154)). The collector:

- Uses `hermit/ignored/kvm-fullcorpus` as both a reusable guest build tree and
  the raw result tree
  ([collect-fullcorpus.sh](https://github.com/rrnewton/dev-hermit/blob/3f391f88e854d62be233c25012d1895f03b937a1/compat-envelope/collect-fullcorpus.sh#L40-L49)).
- Stores ptrace references and every backend's stdout/stderr directly beside
  the compiled guest
  ([ptrace capture](https://github.com/rrnewton/dev-hermit/blob/3f391f88e854d62be233c25012d1895f03b937a1/compat-envelope/collect-fullcorpus.sh#L132-L159),
  [backend capture](https://github.com/rrnewton/dev-hermit/blob/3f391f88e854d62be233c25012d1895f03b937a1/compat-envelope/collect-fullcorpus.sh#L162-L178)).
- Overwrites filenames on the next run. There is no run ID, completion index,
  checksum manifest, archive, or garbage-collection boundary.
- Deletes temporary scorecard rows after merging them, but leaves the raw files
  in the shared tree indefinitely
  ([merge](https://github.com/rrnewton/dev-hermit/blob/3f391f88e854d62be233c25012d1895f03b937a1/compat-envelope/collect-fullcorpus.sh#L245-L247)).

Hermit's separate `validate.sh` writes one combined log to `/tmp` but deletes
its detailed `target/validation/hermit-validate.*` workspace on exit
([setup](https://github.com/rrnewton/hermit/blob/c7531a837a65d388707e3b14642a6ba36b660267/validate.sh#L249-L267),
[cleanup](https://github.com/rrnewton/hermit/blob/c7531a837a65d388707e3b14642a6ba36b660267/validate.sh#L480-L503)).
That is useful human output, but is not a machine-readable cross-job contract.

### GitHub Actions

The existing portable fanout already has the correct build-artifact lifetime:
one build, `retention-days: 1`, then proactive deletion after all cells
([upload](https://github.com/rrnewton/hermit/blob/c7531a837a65d388707e3b14642a6ba36b660267/.github/workflows/ci-portable-fanout.yml#L110-L134),
[cleanup](https://github.com/rrnewton/hermit/blob/c7531a837a65d388707e3b14642a6ba36b660267/.github/workflows/ci-portable-fanout.yml#L191-L220)).

Its cell result handling is not sufficient for parity reduction:

- The workflow passes `--results target/e2e/.../results.jsonl` and uploads that
  result directory
  ([cell](https://github.com/rrnewton/hermit/blob/c7531a837a65d388707e3b14642a6ba36b660267/.github/workflows/ci-portable-fanout.yml#L169-L189)).
- The harness still writes stdout/stderr captures below its independent default
  `ignored/e2e/runs/$RUN_ID` root
  ([roots](https://github.com/rrnewton/hermit/blob/c7531a837a65d388707e3b14642a6ba36b660267/ci/test_harness.sh#L15-L20),
  [captures](https://github.com/rrnewton/hermit/blob/c7531a837a65d388707e3b14642a6ba36b660267/ci/test_harness.sh#L716-L813)).
- Therefore the uploaded per-cell artifact contains JSON/JUnit/summary, but not
  the raw captures needed by a later parity comparison.
- Each result artifact is retained for 14 days and there is no reducer or
  cleanup of result artifacts.

The monolithic portable and privileged workflows similarly upload structured
lane results for 14 days. They do not pass full-corpus parity references between
jobs.

## Measured sizes

All values are byte counts from retained same-machine evidence. Generated
archives were written to `/tmp`; no binary or archive is committed.

| Evidence set | Files / scope | Raw bytes | `tar.zst` | `tar.gz` | Direct Actions-like ZIP |
|---|---:|---:|---:|---:|---:|
| Exact full corpus at Hermit `82a8e853`, Reverie `a4f33d69` | 200 tests x 6 backends, 4,620 canonical stdout/stderr/fail files | 1,156,883 | **119,621** | 154,334 | 1,576,258 |
| Phase-2 INFO-heavy frontier | 226 backend cells + 105 shared ptrace refs, complete run directory | 169,417,050 | **9,380,478** | 10,324,466 | 12,168,130 |

The exact 200-test archive SHA-256 is
`8e6fed0a1f0a0801373be9d45936d30f8674a85e2a5ddc5cc7a3d7aa818c9668`.
The INFO-heavy archive SHA-256 is
`6e8097b9d478645c45f9055f30ae9cfdbbbc5e5f80040eb71ba368f8ba75e773`.

Pre-tarring the 4,620 small files makes the GitHub payload about 13 times
smaller than uploading the files directly. Uploading a precompressed tar with
Actions compression level 0 adds only ZIP framing; the measured 119,621-byte
tar became 119,879 bytes.

### Actual shard sizes

This is an on-disk measurement, not an estimate. The source is the retained
complete run under `hermit/target/kvm-fullcorpus`, selected by the 1,200 rows in
`compat-envelope/fullcorpus-scorecard.csv`: 200 unique tests, six backends, and
one `verify` mode. Each shard key is the scorecard's
`lane x mode x manifest bucket`. The archives contain only the canonical
ptrace/backend parity stdout, stderr, verify output, and failure markers;
compiled guests, build logs, caches, and unrelated manual files are excluded.

| Shard | Tests | Files | Raw bytes | Allocated bytes | `tar.zst` bytes |
|---|---:|---:|---:|---:|---:|
| `portable/verify/applications` | 1 | 23 | 7,599 | 65,536 | 1,694 |
| `portable/verify/backend-parity-c` | 2 | 46 | 9,825 | 139,264 | 2,334 |
| `portable/verify/bin-c` | 2 | 47 | 24,348 | 118,784 | 3,459 |
| `portable/verify/c-programs` | 159 | 3,666 | 877,766 | 10,719,232 | 90,014 |
| `portable/verify/chaos-c` | 1 | 23 | 5,122 | 65,536 | 1,688 |
| `portable/verify/data-handling` | 2 | 48 | 11,560 | 98,304 | 1,707 |
| `portable/verify/debugger-c` | 1 | 23 | 5,168 | 73,728 | 1,704 |
| `portable/verify/determinism-stress-c` | 10 | 231 | 60,369 | 589,824 | 8,220 |
| `portable/verify/determinism-stress` | 4 | 94 | 24,245 | 225,280 | 3,532 |
| `portable/verify/language-runtimes` | 6 | 138 | 36,522 | 352,256 | 5,118 |
| `portable/verify/shared-futex-c` | 4 | 96 | 42,016 | 258,048 | 5,342 |
| `portable/verify/system-utils` | 6 | 138 | 42,694 | 397,312 | 5,460 |
| `portable/verify/util-c` | 1 | 24 | 4,291 | 49,152 | 1,395 |
| `privileged/verify/backend-parity-c` | 1 | 23 | 5,358 | 73,728 | 1,872 |
| **Full corpus union** | **200** | **4,620** | **1,156,883** | **13,225,984** | **119,621** |

The 14 separately compressed shard archives total 133,539 bytes. That is
13,918 bytes larger than the single full-corpus archive because each shard pays
its own tar and compression framing. The largest shard is `c-programs` at
90,014 bytes compressed; the smallest is `util-c` at 1,395 bytes. The complete
measurement, file lists, archives, and hashes are under
`/tmp/parity-log-shard-measurement-20260802/`.

"Raw bytes" is the sum of file content lengths. The same tiny files occupy
13,225,984 bytes of allocated filesystem blocks, which is why pre-tarring is
also useful locally even before network compression.

There is no complete 235-test x six-backend run retained on disk. The current
expanded 235-test estimate below is therefore capacity planning only and must
not be reported as a measurement.

Simple scaling to the current 235-test x 6-backend corpus gives about 140 KB at
the normal capture level. Scaling the deliberately INFO-heavy frontier by run
unit gives about 40 MB. The latter is a capacity bound, not a claimed measured
235-test result. Either is small enough for per-block transport; the flood case
justifies explicit size limits.

The deterministic archive command is:

```bash
LC_ALL=C find "$shard_root" -type f -print0 | LC_ALL=C sort -z >files.list0
tar --null --files-from=files.list0 \
  --sort=name --mtime='UTC 1970-01-01' \
  --owner=0 --group=0 --numeric-owner \
  --zstd -cf "$artifact"
sha256sum "$artifact" >"$artifact.sha256"
```

`files.list0` must be outside `$shard_root` or explicitly excluded.

## Shard contract

Archive name:

```text
parity-v1-<run-key>-<lane>-<mode>-<manifest-block>.tar.zst
```

GitHub `run-key` is `<github.run_id>-<github.run_attempt>` so a rerun cannot
collide with immutable artifacts from an earlier attempt. Local `run-key` is a
timestamp plus source SHA and PID.

Archive layout:

```text
parity-v1/
  index.json
  results.jsonl
  captures/<test-id>/<backend>/run.stdout
  captures/<test-id>/<backend>/run.stderr
  captures/<test-id>/<backend>/verify.stdout
  captures/<test-id>/<backend>/verify.stderr
  observations/<test-id>/<backend>/<declared-artifact-path>
  complete
```

`index.json` is the authority, not directory discovery. Schema 1 includes:

- run key, repository SHA, dirty flag, lane, mode, block ID, and schema version;
- build artifact name, build artifact digest, and Hermit binary SHA-256;
- the exact planned cell keys and their manifest/test SHA-256 values;
- for each capture: relative path, byte count, SHA-256, exit/timeout/signal
  class, and whether a size limit was hit;
- result counts and an archive-content digest.

The producer writes `complete` only after all expected cells and hashes are in
the index. A failed or timed-out test still produces a complete shard. A job
crash produces a missing/incomplete shard, which the reducer reports as an
infrastructure error rather than a test failure.

The reducer must fail closed on:

- missing or duplicate `(lane, mode, block)` shards;
- a source SHA, build digest, binary digest, manifest hash, or schema mismatch;
- missing cells, extra cells, unsafe archive paths, checksum mismatch, or an
  absent `complete` marker;
- a capture exceeding the declared limit.

The reducer compares hashes first and opens raw captures only for mismatches.
This avoids copying whole ptrace reference logs into every backend result while
retaining complete diagnostics.

## Local validate flow

Use one isolated ignored root:

```text
ignored/parity/<run-key>/
  build/
  shards/<lane>/<mode>/<block>/
  transport/*.tar.zst
  reduced/{scorecard.csv,junit.xml,summary.json,failures/}
```

1. The build node writes the binary and fixtures under `build/` and records the
   binary digest.
2. Every shard writes only within its own directory, then packs itself using the
   common packer.
3. The reducer consumes `transport/*.tar.zst`; no upload/download adapter is
   involved locally.
4. On green completion, remove expanded shard directories and keep the compact
   reduced result. Keep transport archives only when `PARITY_KEEP_ARTIFACTS=1`
   or the reducer fails.
5. Never reuse raw output paths across run keys. Guest build caching can remain
   content-addressed and separate from evidence.

This replaces the current overwrite-prone result/build directory without making
local validation depend on GitHub.

## GitHub DAG flow

```text
build-once
  -> shard[ lane x mode x manifest-block ]
  -> reduce-parity
  -> cleanup-transport
```

Build artifact:

```text
hermit-build-v1-${{ github.run_id }}-${{ github.run_attempt }}
```

Shard artifact:

```text
parity-v1-${{ github.run_id }}-${{ github.run_attempt }}-<lane>-<mode>-<block>
```

Every matrix job uploads exactly one tar plus its SHA file with:

```yaml
if: always()
uses: actions/upload-artifact@v4
with:
  name: parity-v1-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.slug }}
  path: |
    ignored/parity/transport/${{ matrix.slug }}.tar.zst
    ignored/parity/transport/${{ matrix.slug }}.tar.zst.sha256
  if-no-files-found: error
  retention-days: 1
  compression-level: 0
```

The test command's exit code must be saved so packing/upload runs even after a
test failure; a final step then returns the saved status. Do not use one shared
artifact name: upload-artifact v4 artifacts are immutable and same-name matrix
uploads conflict.

The reducer runs with `if: always()` after the matrix, downloads with a pattern,
and leaves each artifact in its own directory:

```yaml
uses: actions/download-artifact@v4
with:
  pattern: parity-v1-${{ github.run_id }}-${{ github.run_attempt }}-*
  path: ignored/parity/incoming
  merge-multiple: false
```

Separate directories make duplicate/collision detection straightforward. The
reducer validates expected shard count against the generated plan before
comparing results.

After reduction, upload `scorecard.csv`, JUnit, summary, and only mismatching or
failing captures as one 14-day diagnostic artifact. A final `if: always()` job
with `actions: write` deletes every build and parity transport artifact for the
current run attempt. `retention-days: 1` is the cancellation/failure backstop.

## Limits and operational choices

- One artifact per matrix job stays far below Actions' 500-artifacts-per-job
  limit even if the DAG has hundreds of shards.
- Use `compression-level: 0` for the precompressed tar. Actions always wraps
  uploads in ZIP, so a second zlib pass is wasted CPU.
- Cap a shard at 256 MB raw and 32 MB compressed initially. Both are well above
  measured normal shards and make a new log flood fail explicitly.
- Keep one test's capture below 64 MB. Record oversize as an infrastructure
  error; do not silently truncate input used for parity.
- Do not include compiled guests, the Hermit binary, recordings, cores, caches,
  or hidden credentials in parity archives. Those have separate ownership.
- Artifact transport is not archival storage. Durable aggregate results belong
  in checked-in scorecard evidence; raw transport artifacts are intentionally
  ephemeral.

## Actions v4 constraints

The design follows the official
[`actions/upload-artifact@v4` contract](https://github.com/actions/upload-artifact/blob/v4/README.md):
artifacts are immutable, a name cannot be appended by multiple jobs, retention
is 1 to 90 days, compression is configurable from 0 to 9, hidden files are
excluded by default, and each job may create at most 500 artifacts. The action
returns artifact ID, URL, and SHA-256 digest.

The reducer pattern follows the official
[`actions/download-artifact@v4` contract](https://github.com/actions/download-artifact/blob/v4/README.md):
multiple immutable artifacts can be selected by `pattern` and either isolated
or merged. Isolation is required here so duplicate paths cannot overwrite one
another before validation.
