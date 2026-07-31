# KVM frozen-example parity re-measurement on landed main 0ca0dec2 (2026-07-31)

## Question

The `kvm_example_parity_milestone` deliverable is "KVM 5/5 frozen examples
byte-identical to ptrace on landed main." That 5/5 was achieved on 2026-07-30 at
hermit `9cd955f9` / reverie pin `89388d7`
(`experiments/kvm_corpus_parity_postmerge_20260730/`). Main has since advanced to
`0ca0dec2` (reverie pin `adc14734`), landing the guest-clock-sharing evolution
(`3ac51e11 Share guest clock across process trees`, `cc3730fd Track committed
logical time in the guest clock`) plus `#1208 clock_getres`, `#1196` version
bump, and the cargo-feature backend gating (`ae2565be`; KVM stays in the default
build). **Does the 5/5 milestone still hold on current landed main?**

## Method

Same method as the 2026-07-30 baseline: single `hermit run --strict` runs
(no `--verify`, which suppresses ptrace guest stdout), byte-for-byte stdout
SHA-256 comparison of KVM vs ptrace on the frozen five. Plus internal-determinism
repeats (3x per backend) on the diverging cell, and a `--log=info` DETLOG trace
of the guest clock reads.

## Snapshot

- Hermit: `0ca0dec256fd484e238b475a031a5c2d482eeba8` (current `rrnewton/hermit:main`)
- Reverie pin: `adc147342f34754b449b9a24174aca3ac3a2e16b`
- Host: Linux `6.18.39`, x86-64, AMD EPYC 9D85; `/dev/kvm` present, mode `0666`
- Binary: `target/debug/hermit` (rebuilt at HEAD)
- Level: L0 execution + ptrace guest-stdout parity comparison. Logging default.

## Result: 4/5 (REGRESSED from 5/5)

| Example | ptrace stdout SHA-256 (12) | KVM stdout SHA-256 (12) | Parity | vs 2026-07-30 |
| --- | --- | --- | :---: | --- |
| `date.sh` | `5112c2c91f0d` | `ef5c29fdee2d` | **DIFF** | was MATCH `ded910b908b9` (both) |
| `devrand.sh` | `f5edcf77a864` | `f5edcf77a864` | MATCH | MATCH (unchanged) |
| `race.sh` | `44f4a9c58373` | `44f4a9c58373` | MATCH | MATCH (unchanged) |
| `rand.py` | `e1b8db378cfd` | `e1b8db378cfd` | MATCH | MATCH (unchanged) |
| `timed-progress-bar.py` | `d8778ce33675` | `d8778ce33675` | MATCH | MATCH (unchanged) |

`date.sh` is `exec /usr/bin/date +'%Y-%m-%d_%H:%M:%S_%N'` — it prints the
CLOCK_REALTIME wall clock down to nanoseconds. Actual outputs:

- ptrace: `2025-12-31_16:00:00_041405070`  (full nanosecond resolution)
- KVM:    `2025-12-31_16:00:00_036128000`  (microsecond-quantized: always `...000`)

Both backends are **internally deterministic** (3/3 identical repeats each); they
disagree only cross-backend. Exit status 0 on both.

## Root cause: guest clock now tracks per-backend scheduler committed time

DETLOG `--log=info` on `date.sh`, comparing the same guest syscall indices:

| guest syscall | ptrace virtual time | KVM virtual time | Δ |
| --- | --- | --- | --- |
| #126 `gettimeofday` | `tv_usec: 20104` | `tv_usec: 18504` | 1600 µs |
| #296 `clock_gettime(REALTIME)` | `tv_nsec: 41405070` | `tv_nsec: 36128000` | 5277 µs |

The divergence is present at the earliest time read and **grows** with guest
progress: KVM accumulates guest-visible virtual time *slower* than ptrace, and
its nanoseconds are quantized to microseconds. This is a rate + granularity
difference in the underlying clock, not a one-off offset.

Attribution (airtight): **ptrace's `date.sh` output ALSO changed** since
2026-07-30 (`ded910b908b9` -> `5112c2c91f0d`). A reverie-KVM-pin-only change
cannot alter the ptrace backend, so the cause is a hermit-side detcore change
shared by all backends — the guest-clock-sharing pair, specifically
`cc3730fd "Track committed logical time in the guest clock"`.

`cc3730fd` **removed** the per-backend `GuestClockCalibration`
(`detcore/src/tool_local.rs`). The deleted code's own doc comment stated its
purpose:

> "Detcore's raw logical clock includes backend-specific implementation work
> (for example, ptrace RCBs versus DBI's syscall-only fallback). Each task
> therefore calibrates its raw backend offset on first observation and after
> exec."

That calibration insulated the guest-visible clock from backend-specific raw
progress metrics. `cc3730fd` deliberately removed it so guest absolute deadlines
stay in the scheduler's own clock domain (the fix for the demo5 clock-skew /
past-deadline-poller wedge family). The side effect: the scheduler's committed
logical time — which genuinely differs between ptrace (PMU RCB counts) and KVM
(its own guest-progress metric) — now flows straight into guest-visible
CLOCK_REALTIME. Backends that measure guest progress differently therefore
diverge on any program that prints fine-grained wall-clock time. Only `date.sh`
in the frozen five does (`%N`); the other four never emit sub-microsecond time,
which is why they still match.

## Interpretation

This is a **design tension owned by the human maintainer**, not a freelance-able
KVM fix, and it is trigger #4 (core DetCore scheduling/time; `cc3730fd` is
authored by the maintainer). Two product requirements now conflict:

1. Guest time must track the scheduler's committed logical time so absolute
   deadlines are correct (cc3730fd; demo5 wedge fix).
2. The guest-visible clock must be byte-identical across backends (this
   milestone).

The principled reconciliation — per `continuous-virtual-time-is-sacred` — is NOT
to re-add a calibration layer that blunts/offsets time, but to make the KVM
backend's **raw logical clock advance at fine-grained parity with ptrace's**
(KVM guest-progress -> nanos conversion matching ptrace PMU RCB fidelity, with no
microsecond quantization). That fixes the rate + granularity gap at its source
and satisfies both requirements. It is a KVM-backend raw-clock change requiring
owner design review.

## Reproduction

```bash
cd worktrees/kvm/hermit && cargo build --bin hermit
bash ../../../experiments/kvm_corpus_parity_postmerge_20260730/run.sh   # 4/5 now
# clock trace:
target/debug/hermit --log=info run              --strict -- examples/date.sh 2>&1 | grep clock_gettime
target/debug/hermit --log=info run --backend kvm --strict -- examples/date.sh 2>&1 | grep clock_gettime
```
