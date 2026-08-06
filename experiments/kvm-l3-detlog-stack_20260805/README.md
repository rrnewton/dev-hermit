# KVM L3 detlog-stack: real variance, or measurement artifact?

**Task:** `kvm_l3_detlog_stack` · **Date:** 2026-08-05 · No egress, no validate run.

## Question

A prior observation claimed the KVM guest **stack content varies across runs** on the
`ld.so.cache` `openat` path — *3 runs → 3 distinct `--detlog-stack` hashes* — blocking
byte-identical L3 parity. The task's own acceptance criterion marks that claim
**UNVERIFIED**: it was never backed by retained per-run hashes, and it "MUST NOT be used as
a scorecard counterexample or parity claim" until it is.

## Verdict

> **The "3 runs → 3 distinct hashes" signature is a MEASUREMENT ARTIFACT of an unpinned
> environment. It reproduces on the PTRACE backend, which is not under suspicion, and it
> disappears entirely when the environment is pinned.**
>
> **Therefore the historical claim has no evidentiary basis as a KVM-specific property.**
> It does *not* follow that KVM is clean — KVM still cannot complete a guest (below), so
> the KVM-specific question remains **UNTESTABLE**, not answered.

## Evidence (all retained in this directory)

### 1. Unpinned environment — the claimed signature reproduces on ptrace

Three boxed ptrace runs, `--strict --detlog-stack`, guest `/bin/echo hello`:

| run | env | records | stack-record hash (sha256, first 32) |
| --- | --- | --- | --- |
| 1 | unpinned | 130 | `7553e9a41d194b6a141e43d8362b816d` |
| 2 | unpinned | 130 | `9fe062476fdc715e06715b37b30e1cce` |
| 3 | unpinned | 130 | `3b4b1f1ca02cbfd0e073b601156631a8` |

**3/3 distinct — the exact signature attributed to KVM, produced by ptrace.** Record count
is identical (130) and the mapped range is identical
(`0x7ffffffdc000-0x7ffffffff000 [stack]`); **every one of the 130 records differs.**

### 2. Pinned environment — the variance vanishes

Same binary, same guest, same flags, run under
`env -i PATH=… LD_LIBRARY_PATH=… HOME=/tmp TERM=dumb`:

| run | env | records | stack-record hash |
| --- | --- | --- | --- |
| 4 | pinned | 56 | `bdd39ed35218fbb8777a291b406a7dad` |
| 5 | pinned | 56 | `bdd39ed35218fbb8777a291b406a7dad` |

**2/2 identical, 0 of 56 records differing.** Record count drops 130 → 56 with the smaller
environment, consistent with environment size driving stack extent.

### 3. The cause, observed rather than inferred

Diffing the environment inside two otherwise-identical box invocations
(`runs/boxenv-1.txt` vs `runs/boxenv-2.txt`, 166 vars each) — exactly three vars differ:

```
INVOCATION_ID=12b4626560a643f1b7ffae23050fc249   ->  c48c52b0e9f445f2836d6ae8360fa55f
SAFE_CI_EXPECTED_OUTER_MEMORY_MAX_BYTES=555110741606 -> 554898688819
SAFE_CI_SCOPE_UNIT=safe-ci-4021952.scope         ->  safe-ci-4022324.scope
```

`INVOCATION_ID` is systemd's per-invocation UUID; `SAFE_CI_SCOPE_UNIT` embeds the launcher
PID; the memory figure is sampled from live free memory. All three land in the guest's
`envp`, which the kernel writes into the **initial process stack** — so the stack contents
legitimately differ, under any backend. Complete causal chain: differing env → differing
`envp` in the initial stack → differing stack hash.

### 4. KVM remains unrunnable — a 5th confirmation, at a new SHA

```
hermit --backend kvm run /bin/true
  -> CPU-TIMEOUT >30s cpu in 31s wall  (dCPU/dWall ~1.0 sustained = burned core)
```

Boxed via `hermit-box-run` (cgroup-scoped, so the measurement is contamination-proof and the
livelock is reaped rather than left spinning). Prior blocked notes measured this at hermit
`8f656b4d` (Aug 3); **this confirmation is at `b64d893a`**, so the livelock survived
everything that landed through Aug 5 21:03.

**Untested:** hermit `f89c69766` (current primary main, 2026-08-05T23:54) whose top commit
*"Reconstruct deterministic run-queue exec handoff linearly"* touches
`detcore/src/{scheduler,tool_global}.rs` — the precise area a prior note named as the real
fix ("core detcore-scheduler seeding of `next_turns[root]`"). No binary exists for it and I
could not build one (no worktree slot). **That is the single highest-value next check.**

## What this means for the scorecard

- The claimed KVM stack variance must **not** be cited as a parity counterexample. Its
  evidence is indistinguishable from an unpinned-environment artifact.
- **Any future L3 stack comparison must pin the environment**, or it measures the launcher.
  This applies to every backend, and to the heap domain work equally — a `--detlog-heap`
  comparison run under two different systemd scopes would show the same false variance.
- The measurement harness itself is **sound once the environment is pinned**: 2/2 identical
  is a real positive control, so a future KVM divergence measured this way would be credible.

## Files

- `runs/ptrace-{1,2,3}.log` — unpinned-env INFO logs (full, retained)
- `runs/ptrace-{4,5}.log` — pinned-env INFO logs
- `runs/ptrace-{1,2,3}.out|.err`, `runs/ptrace-{4,5}.boxout` — captured wrapper output
- `runs/boxenv-{1,2}.txt` — the two in-box environments that differ by three vars
- `results.csv` — per-run hash table (full sha256)
- `metadata.json` — binary provenance, SHAs, flags, host, KVM probe result

## Reproduction

```bash
cd ~/work/dev-hermit
B=worktrees/devscope/hermit/target/debug/hermit
# unpinned (reproduces the false signature)
scripts/hermit-box-run --cpu-budget 120 --wall 180 -- env LD_LIBRARY_PATH=/tmp/lu/usr/lib64 \
  $B --log=info --log-file=/tmp/a.log run --strict --detlog-stack -- /bin/echo hello
# pinned (stable)
scripts/hermit-box-run --cpu-budget 120 --wall 180 -- env -i PATH=/usr/bin:/bin \
  LD_LIBRARY_PATH=/tmp/lu/usr/lib64 HOME=/tmp TERM=dumb \
  $B --log=info --log-file=/tmp/b.log run --strict --detlog-stack -- /bin/echo hello
grep 'DETLOG \[memory\]' /tmp/b.log | sed 's/^.*DETLOG/DETLOG/' | sha256sum
```

## Limitations

- **KVM was never executed.** The verdict refutes the claim's *evidence*; it does not clear
  KVM. That requires the livelock fix.
- Guest is `/bin/echo hello`, which does exercise the `ld.so.cache` `openat` path (it is
  dynamically linked) but is not the exact historical workload — that workload was never
  recorded, which is part of why the claim was unverifiable.
- The binary is from slot `worktrees/devscope` at `b64d893a`; the mtime (21:03) and the
  slot HEAD are consistent but I did not rebuild to confirm the binary was produced from
  exactly that tree.
- N=3 unpinned / N=2 pinned. The pinned pair is a two-sample equality, sufficient to refute
  "always varies" but not to establish long-run stability.
- Runs 1–3 captured the *box wrapper's* stdout, not guest stdout, so their `.out` hashes
  differ for wrapper reasons and are not a guest signal. Runs 4–5 fixed this to `.boxout`.
