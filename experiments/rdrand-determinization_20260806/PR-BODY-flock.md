[impl agent, claude-opus-5]

**P0.** Standalone off `main` so it can land independently of any fixture stack.

## Summary

`handle_flock` returned success unconditionally, so **two guest processes could hold the same `LOCK_EX` at once**.

Measured, minimal probe — parent takes `flock(LOCK_EX)` and *holds* it, forks, child opens a fresh fd and probes `LOCK_EX|LOCK_NB`:

| | before this change |
| --- | --- |
| native | `errno=11 EWOULDBLOCK` — exclusion **enforced** |
| hermit ptrace `--strict` | `rc=0` — **acquired. Two holders.** |
| hermit dbi `--strict` | `rc=0` — same |

**Not environmental:** the identical probe with fcntl POSIX record locks returned `EAGAIN` under Hermit exactly like native, and parent and child see the same `dev`/`ino`. The file was genuinely shared and the kernel would have enforced it. It was specific to `flock`.

## The in-code justification was wrong, and is corrected here

`syscall_classification.rs` claimed:

> *an advisory whole-file lock (flock) is never contended within the serialized container*

Serializing guest threads stops them **executing** simultaneously; it does not stop their lock **hold intervals** overlapping. A holder that is descheduled — because it blocked, forked, or used up its timeslice — keeps holding while another process runs and observes the lock. The comment is rewritten rather than left to mislead the next reader.

## Why this was worse than a typical unimplemented syscall

`flock` is *the* standard lockfile mutex, so Hermit silently removed mutual exclusion from every guest that used one — turning correct programs racy. And it did so **deterministically**, which means **double-run verification could never see it**: a consistently wrong answer passes every determinism check in the repo. It was also fail-open, replacing a fail-closed `--strict` abort with a silent wrong answer, which is the wrong default for a determinism tool.

## The fix

Forward to the kernel, as `fcntl` already does for POSIX record locks. The guest's descriptor is a real host descriptor, so the kernel supplies the contract self-consistently.

**One case cannot be served by forwarding.** A guest parked inside a blocking kernel `flock` is not visible to the deterministic scheduler as blocked, so nothing runs to release the lock — a four-way contention guest that completes natively **hung indefinitely** (200 s, reproduced twice) under plain forwarding. So the kernel is always asked non-blockingly:

- guest asked `LOCK_NB` → `EWOULDBLOCK`, exact;
- guest asked to wait and the lock is free → acquires, exact;
- guest asked to wait and the lock is held → **refuse loudly**: abort under `--strict`, `ENOLCK` otherwise.

Granting it would recreate the original bug; blocking would deadlock the container. Faithful waiting needs a wait queue owned by the scheduler, the way futexes are handled — a separate change, named in the error message rather than faked.

## Determinism

The acquisition outcome is a function of which guest holds the lock, and that is fixed by Detcore's deterministic schedule, so a given program and seed produce the same result every run — verified: `--strict --verify` returns `Success: deterministic` (3/3, 0.44 s / 0.42 s / 0.52 s wall). No host state enters the decision: every contending party is a guest, and the lock lives on a guest-visible file whose `dev`/`ino` the guests agree on. ptrace and DBI produce byte-identical output (`md5 18c8effaa7a6`).

The refusal path is deterministic too — it depends only on whether a guest holds the lock, not on timing — so it cannot turn a reproducible run into a flaky one.

## Validation

**Head:** `4aea3529cae84ea4cf1b41a130d4be454d9db838` · **Base:** `origin/main` `4c70658e785834737cbe1524f77330c781a6f5ea` (0 behind, 1 ahead) · **Relaxations:** none

**Verify by planting — the contract, hermit vs native, 10/10 identical:**

```
single-holder: acquired        ex-vs-ex(NB): denied      sh-vs-ex(NB): denied
downgrade: ok                  sh-vs-sh(NB): acquired    ex-vs-sh(NB): denied
upgrade: ok                    after-unlock: acquired    after-holder-exit: acquired
blocking-progress: acquired
```

That covers every point the fix claims: shared vs exclusive, `LOCK_NB`, atomic upgrade and downgrade, release on `LOCK_UN`, **release on process exit**, and the blocking path making progress when the lock is free. `diff` against the native run is empty.

| Check | Result |
| --- | --- |
| Second process denied (the original bug) | `EWOULDBLOCK` — was `rc=0` |
| Single holder still succeeds | acquired |
| Contended blocking flock | loud refusal + `--strict` abort — **was a 200 s hang** under plain forwarding |
| `--strict --verify` | `Success: deterministic`, 3/3 |
| ptrace vs DBI | byte-identical |
| `cargo test -p hermit-detcore --lib` | 386 passed, 0 failed |
| `cargo test -p hermit-detcore --test tests_misc` (on the stacked branch) | 29 passed, 0 failed |
| `cargo fmt --all -- --check` | clean |

**Not claimed.** SaBRe/KVM/LiteInst not exercised for this change. Deterministic *waiting* on a contended lock is not implemented — it is refused, and the message says so.

## Linux Semantics

Everything except contended waiting is now the kernel's own behaviour on the guest's own descriptor, so it is faithful by construction rather than by re-implementation — including the parts easy to get wrong separately: a shared lock does not conflict with another shared lock but does conflict with an exclusive one, upgrade/downgrade is atomic on the same descriptor, and the lock is released when the last descriptor for the open file description closes as well as on process exit. Contended waiting returns `ENOLCK` (or aborts under `--strict`) instead of blocking; that is a documented divergence, not silent.
