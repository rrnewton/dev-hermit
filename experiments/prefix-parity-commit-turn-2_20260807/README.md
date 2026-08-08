# COMMIT turn 2: two real causes, only one of them a bug

**Task:** `attack-the-shared-divergence-at-commit-turn-2` · agent `hermit-w6` · 2026-08-07
**Measured on:** `scratch/p4/bin/hermit` = `hermit 0.2.0 (2026-08-07, g590fcc9eeb03)`, **clean, exactly
live main `590fcc9`**.

## Question

The recorded measurement found 6/6 non-ptrace pairs first diverging at zero-based record 2 /
`COMMIT turn 2` — LiteInst 4/4 on injected-DSO `Path`, KVM 2/2 on virtual time — and concluded
**one fix should move all six depths at once**.

## Answer

**Both named causes are real and both were diagnosed correctly. But there is no single fix, because
they are not the same kind of thing.**

| | LiteInst (4 pairs) | KVM (3 pairs measured) |
|---|---|---|
| record count vs ptrace | **31 vs 5** (4.7–6.2×) | **5 vs 5, 6 vs 6, 7 vs 7** — equal |
| resources on every record | **differ** | **identical** |
| what actually differs | the injected DSO, then 24 map scans | **virtual time only** |
| self-deterministic | — | **yes**, 3/3 runs byte-identical |
| is depth 2 a bug? | **No — it is the ceiling** | **Yes — and it is fixable** |

Fixing KVM's virtual-time accounting should move every KVM pair to `Z/Z`. Nothing moves LiteInst.

## Re-baselining was load-bearing, and it reversed a conclusion

My first pass used `hermit/target/release/hermit` at `f89c69766` — **18 commits behind** live main,
Reverie pin `9470712a` vs live `6144323c`, and built from a dirty tree. On that build **KVM hung**:
`rc=124`, zero COMMIT records, at both 75 s and 120 s. I recorded KVM as NO-RUN and reported the KVM
half of the premise as refuted.

**That was wrong, and only the re-measure caught it.** On a clean `590fcc9` binary KVM runs cleanly
(`rc=0`) and produces a full record set. The stale-baseline warning in the task was not a formality.

The LiteInst numbers were unaffected by the 18-commit delta — identical on both builds.

## Results (`results.csv`)

| guest | ptrace Z | liteinst Y | kvm Y | kvm emitted |
|---|---:|---:|---:|---:|
| `/bin/true` | 5 | **2** (31 emitted) | **2** | 5 |
| `/bin/echo hello` | 6 | **2** (32) | **2** | 6 |
| `/usr/bin/wc -c /etc/hostname` | 7 | **2** (33) | **2** | 7 |
| `/bin/sh -c 'echo a \| wc -c'` | 26 | **2** (37, **rc=1**) | — | — |

`true 5/5`, `echo 6/6`, `wc 7/7` reproduce the recorded ptrace baseline exactly. The 4th rung reads
Z=26 here against a recorded 30 **and its LiteInst run exits rc=1** — that row was never a clean
measurement on either side, so `2/30` should not be quoted as a depth.

## KVM — a real bug, precisely located

Resources are byte-identical on **every** record for all three guests. The only difference is the
committed virtual timestamp:

```
rec0  ParentContinue      delta      +0 ns     <- synthetic, fixed
rec1  MemAddrSpace        delta      +0 ns     <- synthetic, fixed (exactly 500 µs apart)
rec2  Path(ld.so.cache)   delta  -34170 ns     <- first REAL timed operation: divergence starts here
rec3  Path(libc.so.6)     delta  -37250 ns
rec4  Exit                delta -216095 ns
```

The first two commits carry hardcoded timestamps, so they cannot diverge. The deficit appears at the
**first genuinely timed syscall** and grows. It is:

- **stable to the nanosecond across runs** (`-34170` on run 1 and run 3), and
- **consistent across guests** (`-34170 / -34170 / -34270` at rec2).

So KVM's virtual clock systematically charges *less* time per operation than ptrace's. This is a
deterministic accounting difference, not nondeterminism — which is what makes it fixable, and what
makes "KVM diverges on virtual time" exactly the right diagnosis.

KVM self-determinism control: three `/bin/true` runs byte-identical; ptrace control likewise.

## LiteInst — not a bug, and "fixing" it would plant one

```
ptrace   turn 2: Path("/etc/ld.so.cache")
liteinst turn 2: Path(".../scratch/p4/bin/libreverie_liteinst.so")
```

Turns 0–1 are backend-independent process setup. Turn 2 is **the first file the guest loader opens**,
and under LiteInst that file is the injected runtime. There is no earlier place for the injection to
appear, so **2 is the ceiling**.

The two ways to raise it are both worse than the problem:

- **Don't inject** — then it is not LiteInst.
- **Hide the record** — then the log stops showing the backend engaged. That is precisely the
  documented e9patch failure: `mapped_sites=0`, patches nothing, falls through to the ptrace runtime,
  scores `400940/400940`. Suppressing instrumentation records to raise a parity score manufactures
  that bug deliberately.

And the runs are not the same program. **Exactly 24 records are `/proc/self/maps` on every guest**
(24/31, 24/32, 24/33) — a fixed runtime self-scan, constant regardless of workload. Roughly 25 of
~31 records are instrumentation; the guest's own work is a *minority* of its own log.

## The metric is sound — controls both directions

| control | expected | measured |
|---|---|---|
| golden vs itself | Z | **5/5** |
| perturb record 4 | drops to 3 | **3/5** |
| perturb record 1 | drops to 0 | **0/5** |
| truncate golden to 3 | drops to 3 | **3/5** |

Satisfies the task's "a deliberately perturbed log still LOWERS depth" requirement: the metric keeps
detecting regression.

## Recommended next action

1. **Attack KVM virtual time — this is the real one.** Resources already match; only the clock
   differs, deterministically, from the first timed syscall. Expected payoff: KVM goes 2→5, 2→6, 2→7
   in a single fix. Start at whatever charges virtual time per syscall in the KVM backend and compare
   against the ptrace path; the constant ~34 µs step at the first `Path` open is the entry point.
2. **Stop counting LiteInst on this metric.** Cross-backend prefix parity against an uninstrumented
   golden cannot measure a DSO-injection backend. Either compare LiteInst against a *LiteInst*
   golden (self-determinism, the property it can control), or filter instrumentation-owned records
   behind a **fail-closed engagement witness** so a zero-engagement run scores NOT-EXERCISED rather
   than perfect. Without that witness, option two *is* the e9patch bug.

## Caveat on provenance

This reconstructs the metric: `ci-hub/parity/prefix_depth.sh` iterates `for be in dbi sabre e9patch`
and has **no liteinst and no kvm arm**, so the recorded numbers cannot have come from it. The
measurement here is on a clean binary at exactly `590fcc9`.

## Reproduction

```
export LD_LIBRARY_PATH=$PWD/ignored/lu-parity/usr/lib64
H=$PWD/scratch/p4/bin/hermit          # hermit 0.2.0 (2026-08-07, g590fcc9eeb03)
for be in ptrace kvm liteinst; do
  $H --log=info --log-file=OUT.$be.log run --base-env minimal --backend $be -- /bin/true
  grep -o 'COMMIT turn .*' OUT.$be.log | sed -E 's/0x[0-9a-f]+/HEX/g'
done
```
