# `--detlog-stack` is a function of the environment block; `--detlog-heap` is not

## Question

Blocking prerequisite work for cross-backend heap/stack parity assumed the obstacle was
**backend-injected bytes** (trampolines, probes, code caches). Before building an exclusion
mechanism, I checked the control the task demanded: does an identical double-run on the
**golden ptrace reference** even reproduce its own memory hashes?

## Method

`hermit -l info run --backend ptrace --strict --detlog-heap --detlog-stack -- <guest>`, DETLOG
lines extracted and compared pairwise. Three conditions. Reproduce with `./repro.sh`
(`GUEST` must point outside `/tmp` — hermit isolates guest `/tmp`).

## Results — 172 DETLOG lines per run

| case | env condition | differing | stack | heap | other |
|---|---|---:|---:|---:|---:|
| ambient | same shell, same env | 0 | 0 | 0 | 0 |
| pinned | `env -i`, identical | 0 | 0 | 0 | 0 |
| **perturbed** | **one var, different length** | **54** | **54** | **0** | **0** |

Every one of the 54 `[stack]` hashes changes; all 3 `[heap]` hashes and all 115 other DETLOG
lines are untouched. Observed directly in an earlier ad-hoc pair: stack addresses shifted by
16 bytes (`0x7fffffffb840` → `0x7fffffffb850`), which also moved every stack-address-bearing
syscall argument.

## Interpretation

The kernel builds the initial stack from `argv`/`envp`/`auxv`, so **the environment block's
size determines where the stack sits**, and `detlog_memory_maps` hashes the *whole* `[stack]`
VMA. Change one variable's length and every stack hash changes with zero behavioural
divergence. `AT_RANDOM` is *not* the cause — it is determinized from the thread PRNG at
`handle_post_exec` (`detcore/src/lib.rs:1344`) and was byte-identical across all runs.

**This is not a hermit determinism defect.** With the environment pinned, `--detlog-stack` is
fully reproducible, so L3 memory determinism is attainable — it just has an unstated
precondition.

**Consequence for cross-backend parity, and it is the important one.** The `e9patch` and
`liteinst` backends run through `reverie-preload`, i.e. they set **`LD_PRELOAD`**. Adding that
variable changes the environment block, which shifts the guest stack, which changes **every**
`[stack]` hash relative to the ptrace arm — a pure artifact with no behavioural content. So
heap/stack parity really is unachievable by construction for those backends, but the mechanism
is the **environment**, not trampoline bytes. An exclusion list of injected code regions would
not have fixed it and would have masked nothing.

The correct fixes are, in order: (1) normalize the environment block across both arms (pad to a
common size, or launch both arms under an identical env), and (2) for the stack specifically,
exclude the `argv`/`envp`/`auxv` area at the top of the VMA from the hash, or hash only the live
frame region. Neither is an injected-region registry.

## Limitations

One guest, single-threaded, ptrace only. Does not measure whether trampoline/code-cache bytes
*additionally* contaminate `[heap]` — that remains open, and is the part of the original
premise that survives. It does not establish what a patching backend's stack hash looks like
once the env is normalized; that is the next measurement.
