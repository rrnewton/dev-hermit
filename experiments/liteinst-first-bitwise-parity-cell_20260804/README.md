# LiteInst 0→1: what it would take to get the first BITWISE-parity cell

- **Date:** 2026-08-04 (UTC)
- **Task:** `parity-definition-is-wrong-stdout-not-bitwise` (frontier follow-up: "pick the single
  cheapest cell that could compare full INFO logs with matching timestamps; name what blocks it —
  harness, backend, or the `strip_lines` default").
- **Scope:** MEASUREMENT + ENUMERATION only. No compat expansion, no fixes.
- **Parity definition (owner):** BITWISE determinism = the INFO/DETLOG streams match between
  backends, implying identical syscall inputs/outputs **with identical virtual-time timestamps**.
  Stdout equality (the current scorecard metric) is not parity. See
  `experiments/parity-bitwise-definition-audit_20260804/`.

## The cheapest candidate cell

`system-utils/record-getpid` (guest `hermit/tests/c/getpid.c`): a single `getpid()` then
`printf("My pid: %d\n", pid)`. It is the minimal value-emitting cell — one determinized syscall,
canonical PID — so its DETLOG is the shortest possible and any divergence is trivially localized.
It is already `parity=1` (stdout) and value-emitting on the honest-bracketed list.

## Method

Both backends were driven from the prebuilt debug binary with the DETLOG captured unstripped via
the global `--log-file` (the same file the `--verify` path writes; here compared **across**
backends instead of across two runs of one backend):

```
GUEST=hermit/target/tmp/hermit-wave1-workloads/getpid
hermit --log=info --log-file=ptrace.log   run --backend ptrace   --strict -- $GUEST
hermit --log=info --log-file=liteinst.log run --backend liteinst --strict -- $GUEST
# compare, stripping ONLY the leading host wall-clock log-emission timestamp,
# retaining every virtual timestamp + syscall input/result.
```

This is exactly the harness the owner's bitwise spec calls for (unstripped cross-backend INFO-log
diff). It took no new tooling — `--log-file` + a wall-clock-prefix strip.

## Results

Both runs succeed with **identical stdout** (`My pid: 3`, rc=0). The INFO/DETLOG streams do **not**
match — not remotely:

| metric | ptrace | liteinst |
|---|---:|---:|
| total INFO log lines | 129 | 981 |
| `DETLOG`-tagged lines | 81 | 819 |
| scheduler `COMMIT turn`s | **6** | **32** |
| `/proc/self/maps` reads | **0** | **72** |
| injected `fstat` (fd 3/4) | 2 | 36 |
| guest's own `libc.so.6` open = syscall # | #10 | #19 |

**Virtual time diverges at turn 2** (`results.csv`):

```
turn  ptrace                          liteinst
0     1_767_225_600.000_000_000s      1_767_225_600.000_000_000s   (equal)
1     1_767_225_600.000_500_000s      1_767_225_600.000_500_000s   (equal)
2     1_767_225_600.001_615_080s      1_767_225_600.001_616_330s   <-- DIVERGE (+1250 ns)
...   (ptrace ends at turn 5)         (liteinst runs through turn 31)
```

### Why (root cause, from the DETLOG)

The liteinst backend runs its instrumentation **inside the guest**, and that instrumentation
issues real, guest-observable syscalls that detcore commits to the deterministic timeline:

1. It loads `libreverie_liteinst.so` into the guest as the **first** `openat` — before
   `ld.so.cache` — and pulls in extra deps (`libgcc_s.so.1`) that ptrace never loads. This alone
   **renumbers the guest's own syscall stream** (libc opens at #10 under ptrace, #19 under
   liteinst).
2. Its runtime **re-scans `/proc/self/maps` 72 times** (turns 10–31 are almost all
   `Path("/proc/self/maps"): R`) to locate call sites and place trampolines. ptrace does this
   zero times.
3. detcore injects an `fstat` to determinize metadata for each instrumentation fd (36 vs 2).

Every one of these advances the logical clock (`syscall_nanos` + RCBs) and emits DETLOG lines, so
the two backends commit through a completely different event sequence and virtual-time trajectory.

## Interpretation — which of the three blockers is the real one

- **`strip_lines` default — NOT the blocker (shallow).** It is trivially bypassed: this experiment
  captured raw unstripped DETLOGs directly with `--log-file` (and `--verify-verbose` selects the
  same FullTrace/unnormalized mode). It hides divergence; it does not cause it.
- **Harness — NOT the blocker (modest).** The cross-backend unstripped INFO-log diff the owner
  specifies was reproduced by hand this session from existing primitives (`--log-file` + a
  wall-clock-prefix strip). Wiring it into `collect-envelope.rs` to replace `Sha256(stdout)` is
  routine.
- **Backend — THE blocker (deep).** Even for the cheapest possible cell (one guest syscall),
  liteinst's in-guest instrumentation injects guest-observable syscalls (`libreverie_liteinst.so`
  load, 72× `/proc/self/maps`, fd `fstat`s) that detcore commits to the timeline. Virtual time
  diverges at turn 2; the DETLOG cannot be byte-identical to ptrace's because the underlying event
  stream is not identical.

## What it would take to get liteinst 0→1

A **backend/detcore change**, not a harness or CLI change: liteinst's own bootstrap and
call-site-discovery syscalls must be made **invisible to detcore's determinism accounting** —
excluded from the DETLOG and from virtual-time advancement — so only the *guest program's*
syscalls tick the logical clock and appear in the compared stream. Concretely this requires event
**provenance tagging** (instrumentation-internal vs guest) at the interception boundary, plus
consistent guest-syscall renumbering once the instrumentation events are suppressed. That is the
real frontier for a credible "first bitwise-parity cell" story; until it exists, the honest
bitwise count stays 0 and the 17-bracketed cells remain stdout-only.

Caveat: `/proc/self/maps` is re-scanned 72× because instrumentation runs lazily/repeatedly; if that
scanning is itself nondeterministic in count across liteinst runs, within-backend repeatability
(a precondition of parity) is also at risk — worth a follow-up powered probe.

## Reproduction

```
# binary: hermit/target/debug/hermit (prebuilt debug, built 2026-08-03 21:42 -0700; rebuild:
#   cd hermit && cargo build --bin hermit)
GUEST=hermit/target/tmp/hermit-wave1-workloads/getpid   # rebuild via ci/test_harness.sh if absent
for b in ptrace liteinst; do
  hermit/target/debug/hermit --log=info --log-file=$b.log run --backend $b --strict -- $GUEST
done
grep 'COMMIT turn' ptrace.log liteinst.log     # 6 vs 32 turns; diverge at turn 2
grep -c 'proc/self/maps' ptrace.log liteinst.log  # 0 vs 72
```

Raw captured logs live in ignored `scratch/bitwise-probe/` on the producing host; `results.csv`
here carries the full COMMIT virtual-time sequence for both backends.
