# Strict compat green-cell drop (2026-08-06)

This experiment measures how Hermit's compatibility matrix changes when a
green cell requires raw repeat equality instead of the legacy lossy log
comparison. The frozen denominator is **234 unique semantic workloads × 6
execution paths = 1,404 cells**, with two executions per cell (**2,808
executions**).

There is deliberately **no metric result yet**. The non-executing input
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
- non-C corpus: 20 rows
- requested manifest:
  `183e04156294d1d63b26b5e48e1d72772b5a91c663732080ce04702a1055bd9d`
- input audit:
  `60d50b0c048ccb51ccdaf44f60c4b27b0638af8e5c124dec91ce1908982190c3`

`input-audit.json` independently records 234 observed rows, 234 unique IDs,
234 executable workloads, zero missing sources, zero duplicate IDs, and zero
duplicate workload identities. `requested-manifest.json` binds every row to
its exact source bytes, argv, compile command, workload identity, repository
SHAs, dependency SHAs, corpus hashes, and Hermit binary hash.

The former `applications/timed-progress-bar` row was retired rather than
replaced with filler. Its deleted wrapper merely executed the already-retained
`applications/example-timed-progress-bar` Python workload.
`denominator-decision.json` records the historical wrapper commit and hashes.
The prior 235 denominator was therefore two names for one semantic workload;
234 is the corrected unique-workload authority.

The six execution paths are ptrace, KVM, DBI, SaBRe, e9patch preprocessing
with ptrace lifecycle, and LiteInst. The e9patch column is an execution-path
measurement, not a claim that e9patch is an independent Detcore backend.

## Authority and resume rules

`prepare` refuses dirty source repositories, removes any existing Hermit
binary, rebuilds it from the exact clean Hermit source/tree and Cargo.lock, and
records the exact build command, toolchain receipt, log, and snapshotted output
hash. Every compiled C guest has an equivalent source/command/toolchain/log/
output receipt. The frozen manifest also binds the committed parent harness
bytes and the unique guest-set digest.

Each run requires an explicit lowercase run-instance slug. Denominator,
run-binding, state, and artifact paths include the run kind, instance, and
binding hash. Both ordinals and every stream have distinct canonical paths.
A partial cache, changed binding, changed command, changed binary, changed log
path, artifact alias, or mismatched artifact hash is refused rather than
resumed.

The verifier independently rederives corpus rows, workload identities,
source and compile bindings, all coverage counts, commands, artifact paths,
termination records, raw event counts, and cell verdicts. A full run must bind
an exact verified spot completion: 2 tests × 6 paths × 3 observation modes ×
2 executions = 72 executions. The normal-CLI self-test brackets qualifying
selftest, spot, and synthetic full profiles plus 18 refusal cases. Synthetic
fixtures do not execute the real 234-workload metric.

## Reproduction after review authorization

From this directory:

```bash
./run.rs audit-inputs
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
all 1,404 cells and 2,808 executions exist exactly once, the spot-completion
authority re-verifies, and the full verifier succeeds.
