# e9patch DETLOG parity against the ptrace golden

**Task:** `e9patch-is-clean-on-tsc-and-detlog-score-its-parity` · agent `hermit-w6` · 2026-08-07
**Binary:** `hermit 0.2.0 (2026-08-07, g590fcc9eeb03)` — clean, equals `origin/main` `590fcc9`.

## Result

| | count | denominator |
|---|---:|---:|
| guests built | **20** | 20 |
| **engaged** (`mapped_sites` ≥ 1) | **20** | 20 |
| ptrace self-deterministic | **20** | 20 |
| e9patch self-deterministic | **20** | 20 |
| **COMPARABLE** | **20** | 20 |
| NOT-COMPARABLE | **0** | 20 |
| **DETLOG parity TRUE** | **0** | 20 |
| DETLOG parity FALSE | **20** | 20 |

Every cell is accounted for: 20 built = 20 engaged = 20 comparable = 0 parity + 20 non-parity. Nothing
vacuous, nothing dropped, nothing unmeasured.

**Tier:** DETLOG + COMMIT record stream, wall-clock prefix stripped, `0x…` hex normalised. **Not** stdout-only;
**not** four-signal (INFO+stack+heap). Recorded on every row of `results.csv`.

## The divergence is a single prologue point, not 20 guest failures

**Prefix depth is exactly 6 on all 20 guests**, while `ptrace_records` ranges 8 → 37. A constant depth across a
4.6× spread in guest size means the divergence precedes the guest's own work.

At record 6, immediately after a shared `init auxv AT_RANDOM`:

```
[5] SHARED  DETLOG [post_exec, dtid 3] init auxv AT_RANDOM value to [162, 205, ...]
[6] ptrace  DETLOG [syscall] inbound syscall: exit_group(0) = ?          <- the guest's own first syscall
[6] e9patch DETLOG [syscall] inbound syscall: readlink(HEX -> "/proc/self/exe", ...)
```

**e9patch's runtime locating its own patched binary is the first divergence.** The delta is a constant
**+17 records** on 19 of 20 guests (+21 on `multi_site`, which has 3 mapped sites rather than 1).

This is the same shape as the LiteInst injected-DSO result — the instrumentation announcing itself at a fixed
prologue position — by a different mechanism (self-exe `readlink` rather than a preloaded DSO). Under a
cross-backend comparison against an *uninstrumented* ptrace golden, **6 is the ceiling**, not a symptom.

## Falsifiability: a planted divergence is detected

| | baseline | planted | detected | depth |
|---|---:|---:|---|---:|
| ptrace | 8 records | 10 | **yes** | 6 / 8 |
| e9patch | 25 records | 27 | **yes** | 7 / 25 |

Planted guest = `minimal_exit` plus one extra `getpid` syscall. First differing record is exactly the plant:

```
[6] base    inbound syscall: exit_group(0) = ?
[6] planted inbound syscall: getpid() = ?
```

**Positive control:** `minimal_exit` ptrace run 1 vs run 2 is byte-identical, so the comparator is not simply
reporting everything as different.

## Old vs new — the comparison did tighten, twice

**1. Engagement witness.** The committed `compat-envelope/e9patch-scorecard.csv` records **227/227 e9patch
cells as `outcome=pass`** with `candidate_sites`, `mapped_sites` and `reach_state` **blank on all 227**. Those
rows are `run_id=e9patch-20260801` at hermit `b1fdeaf6` — they predate the columns. The collector *does* have
the machinery (`collect-e9patch-compat.rs:186 apply_reach_gate`, banner parsing at :161-171); the data is
simply stale.

| | OLD (committed 2026-08-01) | NEW (this run) |
|---|---|---|
| cells | 227 | 20 |
| engagement recorded | **0 / 227** | **20 / 20** |
| parity claim | `pass` on 227 | **0 / 20** |

The two are **not** a subtraction — different populations (227 shared-corpus cells vs 20 dedicated-corpus
guests) and different comparators. What transfers is the *property*: an e9patch pass with no engagement
witness cannot be distinguished from e9patch doing nothing.

**2. My own comparator was wrong first.** My initial pass reported 0/20 parity with divergence at index 0 —
which was the **wall-clock timestamp**, not content. The definition says only the wall-clock prefix is
stripped, and I had normalised hex but not the timestamp. Re-scored from the same logs with the prefix
stripped, the depth becomes a meaningful 6. The 0/20 headline survived; the *reason* changed completely, and
the first version would have hidden the constant-6 finding behind noise.

## Caveats that bound this result

- **e9patch's TSC-cleanliness is inherited, not earned.** `reverie-e9patch/README.md:34`: *"Ptrace remains
  attached for process lifecycle, signals, timers, CPUID/RDTSC."* The TSC audit's CLEAN verdict for e9patch
  is ptrace's behaviour showing through. A parity match on TSC-derived state would be partly tautological.
- **ARCHITECTURE GATE.** DETLOG routes via the ptrace host rather than in-guest. Measuring parity is in scope;
  **performance work is not**, until the ptracer leaves the syscall path.
- **Population is the dedicated corpus** (`-nostdlib -static -ffreestanding`, 20/20 reach). The shared
  full-corpus is compiled dynamically and has 4/137 reach — never quote a ratio without saying which.
- Backends other than ptrace/e9patch are untouched here.

## Reproduction

```
export LD_LIBRARY_PATH=$PWD/ignored/lu-parity/usr/lib64
H=$PWD/scratch/p4/bin/hermit
cc -nostdlib -static -ffreestanding -O0 -fno-pie -no-pie -o /tmp/g hermit/tests/backend-parity/e9patch_corpus/minimal_exit.c
for be in ptrace e9patch; do
  $H --log=info --log-file=/tmp/$be.log run --base-env minimal --backend $be -- /tmp/g
done
# strip the wall-clock prefix before comparing, or you measure timestamps:
sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z[[:space:]]+//' ...
```
