# JVM power-to-weight under `hermit --strict --verify` (2026-07-30)

**Question.** Which *small* JVM programs maximize syscall + JIT coverage **per
wall-second** under `hermit run --strict --verify`, so JVM validation can run on
every commit without paying javac's cost?

See `../../ai_docs/2026-07-30-jvm-power-to-weight-analysis.md` for the ranked
table, interpretation, and the recommended every-commit CI set. This directory
holds the reproducible harness and raw data.

## Layout

- `src/*.java` — candidate programs (Hello, JitHotLoop, GcStress, ThreadCounter,
  HashMapString, NioFile, NioSocket). `java -version` needs no source.
- `classes/` — host-`javac` output (regenerate with `javac -d classes src/*.java`).
- `measure_native.sh` — native pass: strace syscall counts + `-XX:+PrintCompilation`
  JIT tiers + native wall (JIT ON). Writes `results/native.csv`.
- `measure_hermit.sh` — hermit `--strict --verify` pass (JIT ON, RCB preemption
  default): two-run wall + L2 verdict. Writes `results/hermit.csv`.
- `results/merged.csv` — joined table with derived coverage/second metrics.
- `results/strace/*.txt`, `results/jit/*.txt`, `results/hermit/*.log` — raw logs.

## Reproduce

```bash
javac -d classes src/*.java
./measure_native.sh          # fast, ~1 min
./measure_hermit.sh          # heavy; run detached, ~5 min on an idle host
```

## Method

- **Syscalls:** `strace -f -c java <args>` (threads followed). "Unique" = distinct
  syscall names; "total" = sum of the calls column.
- **JIT:** `java -XX:+PrintCompilation <args>`; tier 4 = C2, tiers 1-3 = C1.
- **Hermit:** `hermit --log=info run --strict --verify --no-virtualize-cpuid -- java <args>`.
  RCB preemption is left **on** (no `--max-timeslice=disabled`) — a compute-bound
  JVM livelocks with preemption disabled because Hermit never interrupts a
  CPU-bound guest thread, starving the JVM's GC/JIT/dispatcher threads.

## Caveat

The hermit pass ran on a 316-core host saturated with other agents' Hermit
workloads. Absolute wall times are inflated upper bounds; the **relative**
ranking is the reliable signal.
