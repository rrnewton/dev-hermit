# L3 stack-content divergence: the multithreading hypothesis is refuted

**Task:** `l3_stack_content_divergence` · **Date:** 2026-08-06 · Local, no egress.

## What was asked

The task recorded, explicitly as **unconfirmed**, that multithreading causes the L3
stack-content divergence (`zstd-multithread` passes L2, fails `--detlog-stack`), and named
the first step: *"build a RELEASE hermit, then run the ST/MT discriminator; do not try this
at debug."* A prior attempt exceeded 420 s at debug.

## Verdict

> **REFUTED. `zstd -T1` — single-threaded — fails L3 exactly as `-T4` does, at the *same*
> DETLOG message indices (1133 and 1135). Multithreading is not the cause.**

## Results (release hermit `5562161a4`, boxed, pinned env, 2 MiB deterministic input)

| guest | threading | L3 | note |
| --- | --- | --- | --- |
| `wc -c` | single | **PASS** | **control** — proves L3 discriminates on this binary |
| `sha256sum` | single | **PASS** | real work, still passes |
| `gzip -1` | single | **PASS** | a compressor that passes |
| `xz -T1` | single | **FAIL** | |
| `zstd -T1` | single | **FAIL** | **refutes the hypothesis** |
| `zstd -T4` | multi | **FAIL** | same indices 1133/1135 as T1 |

The control matters: without a passing case on *this* binary, "zstd -T1 fails" would be
uninterpretable — it could have meant L3 was blanket-broken in my setup. It is not.

The divergence signature matches the task's description exactly — same address range, same
permissions, different `[stack]` content digest:

```
INFO detcore: DETLOG [memory][dtid <NUM>] <ADDR>-<ADDR> MMPermissions(READ | WRITE | PRIVATE) ... [stack]-><DIGEST>
```

Two independent reasons multithreading is out: `-T1` fails at all, and `-T1`/`-T4` fail at
*identical, early* message indices — a worker-thread cause would diverge later and at
thread-dependent positions.

## What the class actually is

Not "all compressors" (gzip passes) and not "any nontrivial program" (sha256sum passes).
The failing set is `{xz, zstd}`; the passing set is `{wc, sha256sum, gzip}`.

**Hypothesis, explicitly UNCONFIRMED:** xz and zstd both size internal buffers from a
*host-derived* quantity — CPU count and/or available memory — at startup, even at `-T1`,
and that value lands in stack memory. gzip and sha256sum do not. That would produce exactly
this signature: early, thread-count-independent, same address range, differing stack bytes.

**Next step to confirm or kill it:** run `zstd -T1` once with `--log-file` and inspect the
syscall DETLOG before message 1133 for a host-derived query
(`sched_getaffinity`, `sysconf(_SC_NPROCESSORS_ONLN)`, `/proc/meminfo`, `sysinfo`). If one
is present and its result is not determinized, that is the root cause and it is a
determinization gap, not a stack-hashing gap. ~5 minutes on the release binary.

## Why this matters for the north star

L3 is a **live, discriminating ratchet** — 3 of 6 guests pass. But the corpus gates at L2
only, so this class is invisible to CI. The refutation narrows the fix: it is not
"determinize worker-thread stacks" (a large job) but plausibly "determinize one more
host-derived source" (a small one) — pending confirmation.

## Limitations

- Six guests, one input size, one binary (`5562161a4` release), single runs per cell.
- The `{xz, zstd}` vs `{gzip, wc, sha256sum}` split is a 6-point sample; the CPU-count /
  memory-sizing explanation is **inference from program behaviour, not measured**.
- I did not inspect the syscall stream — that is the named next step.
- `zstd -T1` may still create threads internally; I did not verify thread counts. The
  refutation does not depend on it (the identical divergence indices do), but a strict
  reading of "single-threaded" was not confirmed.
