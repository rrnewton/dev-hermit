# `/proc` and `/sys` determinism scorecard

**Task:** `proc-sys-read-determinism`

**Measured source:** Hermit main `f89c69766371806d3c9b2c3003531df2d59d6118`

**Date:** 2026-08-06, local only

## Result

The source-derived sanitizer surface contains **67 `ProcfsKind` classes**. On this host:

- **67/67 classes had a readable representative path; 0 excluded.**
- **67/67 passed Hermit's internal double-run `--verify` record** under strict execution.
- **67/67 produced byte-identical stdout in two additional independent runs.**
- **67/67 returned non-empty content.** The `/proc/locks` cell plants an inert guest-owned POSIX
  lock so an empty host lock table cannot make that cell vacuous.
- **0 timeouts, 0 guest failures, 0 content divergences.** No host-state leak was observed in this
  complete sanitizer-class scorecard.

Each cell therefore represents four guest executions: two inside `--verify` plus two exact-output
runs, **268 guest executions total**. `full-scorecard-verification-ledger.jsonl` carries the complete
verification JSON, exact-output hash, host snapshot, source/binary identity, flags, CPU, timeout, and
libunwind path for every cell. `full-scorecard-results.csv` is the compact scorecard.

The current native host snapshot differed from the guest output in **60/67** cells. Seven cells were
equal at sampling time (`AioNr`, `ArchStatus`, `CpuidleCounter`, `FileMax`, `BtrfsBytesPinned`,
`ThpCounter`, `ModuleRefcnt`); equality is not treated as virtualization proof or as a leak because
these values did not demonstrate host evolution during the sample.

## What “full set” means

The denominator is not an unsafe recursive walk of every pseudo-file under `/proc` and `/sys`.
Hermit's actual load-bearing authority is `detcore/src/procfs.rs::ProcfsKind`: 67 semantic sanitizer
classes at the tested SHA, including dynamic path families for block, Btrfs, hwmon, CPU idle/frequency,
RTC, IRQ, module-refcount, NUMA, and process-specific files. `full-scorecard-cases.tsv` enumerates one
host-present representative for every class in enum order. This makes the denominator bounded,
source-derived, and auditable.

The earlier 27-path candidate set remains in `candidate-set.txt` for history, but it was not complete
and included paths outside this sanitizer authority. It is superseded by the 67-cell scorecard.

## Strictness boundary

The 67 positive records use:

```text
--backend ptrace --strict --verify --max-timeslice disabled
```

`--strict` is fail-closed execution. The internal verifier record is nevertheless the legacy
**stripped** comparison (`bitwise_parity=false`), so exact guest stdout was compared separately.

Canonical `--verify-strict` is currently red before this experiment can isolate a proc/sys cell:

- `/bin/true`: `verified=false`, 476 vs 476 compared messages.
- `/bin/cat /proc/stat`: `verified=false`, 1104 vs 1104 compared messages.

Both guests exit 0; the retained controls (`strict-baseline-true.json`, `strict-proc-stat.json`) use
the canonical policy with address canonicalization and exact remainder. Inspection showed baseline
trace noise such as nondeterministic vDSO patch ordering and real elapsed-time logging. Because the
same failure exists for `/bin/true`, it is a verifier-wide blocker, not evidence that `/proc/stat`
diverged. Accordingly this artifact claims verify-backed exact **content** parity, not canonical
internal-log parity.

## Continuous-evolution check (#140)

At the same exact main SHA, a syscall-heavy single guest run observed:

```text
cpu 0 0
uptime 126.00 0.00
cpu 0 0
uptime 142.00 0.00
```

`/proc/uptime` advanced by 16 virtual seconds, so it is deterministic without being frozen. The
`/proc/self/stat` user/system CPU fields remained `0 0`; that is the existing opposite-side gap:
over-freezing rather than host leakage. See `evolution-probe.txt` for the exact command.

## Provenance and reproduction

- Hermit source: `f89c69766371806d3c9b2c3003531df2d59d6118` (clean detached main checkout).
- Frozen binary SHA-256: `5f4aa9ab8e6b1cf3bf98f73cca35001211e28c2829215e97d9c2c927e5ae9047`.
- Embedded version: `hermit 0.2.0 (2026-08-06, gf89c69766371)`.
- Backend: ptrace; host CPU pinned to 0; per-command timeout 60 seconds.
- Libunwind: `/home/newton/.local/hermit-deps/lu/usr/lib64`.
- No CPUID relaxation. PMU preemption was disabled with `--max-timeslice disabled`.

Reproduce the scorecard with:

```bash
HERMIT_BIN=/path/to/the/exact/hermit \
OUTPUT_DIR=/tmp/proc-sys-scorecard \
LIBUNWIND_DIR=/home/newton/.local/hermit-deps/lu/usr/lib64 \
bash experiments/proc_sys_read_determinism_20260806/run-full-scorecard.sh
```

Raw per-run stdout/stderr stayed under `/tmp` because the combined text was 5.8 MiB; the durable
ledger retains the verification records, hashes, byte counts, and all conditions without committing
large generated output.
