# Randomness-source determinism sweep, with a planted violation

**Question.** Is *every* randomness source determinized under Hermit — not just
`getrandom` — and does the check actually **fail** when one is not?

**Answer.** 8 of 9 enumerated sources are determinized. **RDRAND is not.** It is
*hidden* behind a cleared CPUID feature bit rather than determinized, so code
that issues the instruction without consulting CPUID receives raw hardware
entropy on every run. The planted violation was caught and the positive control
was not flagged, so the check discriminates.

---

## Method

One C guest (`probe.c`) exercises every source in a single process, so a single
double-run covers the whole set and no source can be silently skipped. Sources
are read at the lowest available layer (raw `syscall()`, inline `rdrand`, direct
`getauxval`) so a libc abstraction cannot hide the underlying mechanism.

* **Native ×3** — establishes which sources vary at all. A probe whose sources
  are naturally constant would prove nothing under Hermit.
* **`hermit run --strict` ×2, same seed** — the determinism assertion.
* **PLANT: `hermit run --strict --no-virtualize-cpuid` ×2** — un-masks the CPUID
  RDRAND bit so that one source stops being controlled.
* **Positive control** — the same command *without* the plant flag must not be
  flagged.
* **Production check** — `hermit --log INFO run --strict --verify`, clean and
  planted.

Backend `ptrace`, relaxations none except the deliberate plant. Binary:
`worktrees/strictcorpus/hermit/target/debug/hermit`.

## Results

Native ×3: **7 sources vary** (`getrandom`, `/dev/urandom`, `/dev/random`,
`AT_RANDOM`, `RDRAND`, `getentropy`, `/proc/…/uuid`, plus the stack ASLR offset).
The probe is not trivially constant.

`hermit run --strict` ×2: **all 9 lines identical.** See `results.csv` for the
per-source mechanism.

Two details worth recording rather than glossing:

* `/dev/random` and `/dev/urandom` return the **same** bytes
  (`2972bb044d96df2871ba034c95de2770`) — they share one canonical stream, per
  `fill_random_device_bytes`.
* Under Hermit, `RDRAND` reports **`<CPUID feature bit CLEAR>`** — the
  instruction never executes, because the feature is masked. That is the whole
  finding below.

### The plant

`--no-virtualize-cpuid` un-masks the bit, so RDRAND executes:

```
RDRAND A: 8f219cf9c0a5d246
RDRAND B: f304dc18ac4ca8d4      *** DIVERGES ***
```

and the other **8 sources stay identical**. The plant is surgical: it breaks
exactly one source, and the check flags exactly that one.

### Positive control

Same command without the flag → all 9 identical; `--verify` → **PASS, rc=0, not
flagged**. The check does not cry wolf.

### The production check

| run | result |
| --- | --- |
| `--verify`, clean | PASS rc=0 — not flagged |
| `--verify` + plant | **FLAGGED**, rc=1 |
| `--verify`, CPUID-ignoring RDRAND | **FLAGGED**, rc=1 |

Note *how* the plant is caught: the DETLOG comparison reported "no substantive
differences found (318 | 318 DETLOG messages compared)" and the failure came from
**stdout**. So `--verify` catches an un-determinized source when its value reaches
observable output — and, by the same token, would **not** catch one that only
influenced internal behaviour (a hash seed affecting iteration order that never
prints, a retry count, a timing branch).

## The gap: RDRAND is hidden, not determinized

`rdrand_forced.c` issues `rdrand` unconditionally, without consulting CPUID.
Under **default** `hermit run --strict`:

```
forced-rdrand cf=1 74f0dcf6bb345c2d
forced-rdrand cf=1 7a512ce4008bba6d
forced-rdrand cf=1 20ddabcfdb76904f
```

Three runs, three different values, carry flag set — **raw hardware entropy
reaching the guest under strict mode**.

Why it has never shown up: well-behaved software checks the CPUID feature bit
first and falls back to `getrandom`, which *is* determinized. The masking is
therefore effective for compliant code and invisible for the rest. Code that can
issue RDRAND unconditionally includes hand-written assembly, binaries compiled
with `-mrdrnd` for a known target, JITs emitting RDRAND, and anything
deliberately avoiding CPUID.

This is exactly the failure shape the task names: *an undetermined source produces
a run that diverges while every syscall succeeds*. There is no syscall here at
all — which is also why a syscall-interception determinism strategy misses it.

**Not claimed:** that this is easy to fix. Trapping RDRAND requires either
`CR4.TSD`-style instruction faulting (RDRAND has no such control), binary
rewriting, or running under a backend that already rewrites the instruction
stream (DBI/e9patch). The right first step is to decide whether "hidden via
CPUID" is an accepted limitation to document, or a gap to close on the rewriting
backends.

## Reproduction

```bash
cd experiments/randomness-source-sweep_20260806
gcc -O1 -o probe probe.c && gcc -O1 -o rdrand_forced rdrand_forced.c
H=../../worktrees/strictcorpus/hermit/target/debug/hermit
export LD_LIBRARY_PATH=/home/newton/.local/libunwind/usr/lib64

for i in 1 2 3; do ./probe; done                                    # native: sources vary
for r in A B; do $H run --strict --base-env=minimal -- $PWD/probe > hermit-$r.txt; done
diff hermit-A.txt hermit-B.txt                                      # expect: no output

for r in A B; do $H run --strict --no-virtualize-cpuid --base-env=minimal -- $PWD/probe > plant-$r.txt; done
diff plant-A.txt plant-B.txt                                        # expect: RDRAND line only

for i in 1 2 3; do $H run --strict --base-env=minimal -- $PWD/rdrand_forced; done   # expect: 3 distinct
```

`--base-env=minimal` keeps the environment out of the comparison. The libunwind
path is this host's workaround for a missing system libunwind.
