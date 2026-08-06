# Strict compat green-cell drop (2026-08-06)

This experiment measures how Hermit's compatibility matrix changes when a
green cell requires raw repeat equality instead of the legacy lossy log
comparison. The frozen denominator is **231 unique semantic workloads × 6
execution paths = 1,386 cells**, with two executions per cell (**2,772
executions**).

There is deliberately **no metric result yet**. The non-metric input
preflight passes at Hermit PR #1727 head
`49321e67e6356d3f1f26d897ef8193a21c7e28ef`, but no real calibration,
spot, or full compatibility execution has run. A blank numerator is not zero
and is not green. Metric execution remains disabled until two independent
reviews of this harness pass.

## Strict observation contract

A strict-green cell requires both executions to:

- be attempted and exit successfully;
- emit at least one raw INFO-log event;
- have byte-identical stdout and stderr;
- have the same exact exit code or terminating signal; and
- have byte-identical complete ordered `--log=info --log-file` streams.

No prefix, number, address, line, or event class is stripped, rewritten,
canonicalized, or filtered. The legacy verdict is recomputed from the same two
executions with `hermit log-diff --unsafe-strip-lines`; historical labels or
cached aggregate counts are not evidence.

## Frozen denominator and input evidence

- Hermit: `49321e67e6356d3f1f26d897ef8193a21c7e28ef`
- Reverie checkout and dependency:
  `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6`
- LiteInst2 checkout:
  `8bf704feb06a62e7a05bee3b237d70793e4e2689`
- LiteInst2 dependency:
  `95ee5e6917fa33191eb41c3f1606ea8b03c1b78c`
- C corpus: 214 rows
- non-C corpus: 17 rows
- requested manifest:
  `46b48a6c6761e223ed08e58cae3df9eec1433eb2adcf19b4d0aa59c5f3432e0d`
- input audit:
  `cbeff9765d187d9a07a79d4802f9a34c6b625fae2e91ad50833266c19fcbf67a`
- denominator decision bytes:
  `1b4707148413c583d26602b2c7586445a851dcff9043f1dc0bc755bca323fc42`
- denominator decision semantics:
  `23c94ec1762cc686d6299299f5d7580c8a03a5a1686d105edde3198fbe1f5a4f`

`input-audit.json` independently records 231 observed rows, 231 unique IDs,
231 executable workloads, zero missing sources, zero duplicate IDs, and zero
duplicate workload identities. `requested-manifest.json` binds every row to
its exact recursive source/launcher chain, canonical argv, preparation or
compile command, preparation environment, prepared artifacts, workload
identity, repository SHAs, dependency SHAs, corpus hashes, and Hermit binary
hash.

Four trivial-exec wrapper aliases are retired rather than replaced with
filler: the formerly deleted `applications/timed-progress-bar`, plus
`determinism-stress/thread-output`, `language-runtimes/python-random`, and
`system-utils/random-device`. Their run arms only exec the retained example
workloads. `denominator-decision.json` records each wrapper and retained
payload hash. Producer and verifier independently dereference every declared
wrapper and retained payload at its historical Git commit, verify its exact
path and bytes, reject alias cycles or duplicate targets, and permit only the
declared side-effect-free wrapper syntax. The direct shell examples use the
same `bash -c` launcher as the production portable-CI command builder;
workload identity canonicalizes that launcher and the trivial wrappers to the
underlying payload. The prior 235 named rows were therefore 231 semantic
workloads.

The six execution paths are ptrace, KVM, DBI, SaBRe, e9patch preprocessing
with ptrace lifecycle, and LiteInst. The e9patch column is an execution-path
measurement, not a claim that e9patch is an independent Detcore backend.

## Authority and resume rules

`prepare` refuses dirty source repositories, removes any existing Hermit
binary, rebuilds it from the exact clean Hermit source/tree and Cargo.lock, and
records the exact build command, toolchain receipt, log, and snapshotted output
hash. Every compiled C guest has an equivalent source/command/toolchain/log/
output receipt. Each retained non-C e2e wrapper runs its real `--prepare`
protocol in an isolated HOME/XDG/E2E fixture directory; direct scripts receive
an executable probe. Receipts bind the command, environment, recursive source
chain, preparation log, and every prepared artifact. Every preparation and
measured process starts with `env_clear` and an exact recorded environment,
including a fixed executable `PATH`; ambient Python, Rust, logging, HOME, XDG,
and temporary-directory state cannot leak into the run.

Each ordinal receives four path- and inode-disjoint input trees: HOME, XDG
configuration, temporary storage, and fixtures. A preserved seed copy is
content-matched to preparation, then copied to a separate writable execution
tree. Canonical recursive manifests bind every directory and regular file by
relative path, type, mode, byte count, content hash, and aggregate tree hash;
symlinks and special files are refused. Producer cache validation and the
independent verifier both rewalk and rehash the trees, check ordinal/cell
identity and preparation ancestry, and reject missing, added, changed,
mode-changed, swapped, shared, or aliased roots. The frozen manifest also binds
the committed parent harness bytes and the unique guest-set digest.

Each run requires an explicit lowercase run-instance slug. Denominator,
run-binding, state, and artifact paths include the run kind, instance, and
binding hash. Both ordinals and every stream have distinct canonical paths.
A partial cache, changed binding, changed command or environment, changed
preparation receipt, changed binary, changed log path, artifact alias, or
mismatched artifact hash is refused rather than resumed.

The verifier independently rederives corpus rows, workload identities,
source and compile bindings, all coverage counts, commands, artifact paths,
environments, preparation receipts, termination records, raw INFO counts, and
cell verdicts. INFO counts come only from the exact bound Hermit binary running
`log-diff LOG LOG --no-color --limit=1`; the parser requires exactly one
equal-sided INFO count line and rejects malformed or junk evidence. A zero
INFO count—including a WARN/ERROR-only log—is recorded as zero and cannot
satisfy strict green or authorize the spot gate. A full run must bind a
digest-valid, exact healthy spot completion: all 72 executions attempted,
successful, and nonzero-INFO, and all
36 cells raw-equal and strict-green (2 tests × 6 paths × 3 observation modes ×
2 executions). Before launching full work, the producer invokes the exact
committed verifier to dereference the spot JSONL and recreate that completion;
the receipt alone is not trusted. The producer self-test brackets its real
compiler, hermetic environment, isolated-tree, cache, parser, and spot
authorities with 20 planted refusals. The normal-CLI verifier self-test
brackets qualifying selftest, exact 72-execution spot, and spot-gated synthetic
2,772-execution full profiles with 31 planted refusals. Synthetic fixtures do
not execute the real 231-workload metric.

## Reproduction after review authorization

From this directory:

```bash
./run.rs audit-inputs
./run.rs self-test
./verify.rs self-test
./run.rs prepare --jobs 32

./run.rs run --kind calibration --run-instance reviewed-calibration \
  --jobs 6 --timeout-seconds 120
./verify.rs verify --kind calibration --run-instance reviewed-calibration

./run.rs run --kind spot --run-instance reviewed-spot \
  --jobs 6 --timeout-seconds 120
./verify.rs verify --kind spot --run-instance reviewed-spot

./run.rs run --kind full --run-instance reviewed-full \
  --spot-run-instance reviewed-spot --jobs 16 --timeout-seconds 120
./verify.rs verify --kind full --run-instance reviewed-full
```

These real-run commands are documented for the reviewed harness revision; they
must not be launched until the coordinator records two independent harness
review passes. Use the current coordinator/CI admission mechanism for detached
execution rather than the obsolete direct-systemd recipe.

## Current result

**NO RESULT.** Input authority is complete, but preparation and real metric
execution have not occurred. `manifest.json`, real run JSONL, and
`REPORT.md` are intentionally absent. `REPORT.md` is generated only after
all 1,386 cells and 2,772 executions exist exactly once, the spot-completion
authority re-verifies, and the full verifier succeeds.
