# e9patch corpus ratchet — round 33 (inert futex/time/scheduler query probes)

## Question

Round 33 of the standing e9patch corpus ratchet. Can freestanding raw-syscall
x86-64 guests for four inert query/no-op syscalls — `futex(FUTEX_WAKE)` with no
waiters, `gettimeofday`, `sched_getattr`, and a `sched_setaffinity` self
round-trip — reach L2 parity across the golden ptrace backend and the
e9patch-rewritten ptrace path?

After round 32's high drop rate (5/8, all zero-copy/data-movement syscalls that
golden hermit answers with `-ENOSYS`/`-EPERM`), this round deliberately targets
the *inert query/no-op* vein instead of data movement.

## Method

Each candidate is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values (a fixed constant syscall return). Each was
native-tested, then golden-hermit-ptrace L2-tested (`--strict --verify`,
"Determinism verified"), then e9patch L2-tested (candidate_sites>0,
mapped==candidate, no SIGILL fallback `b0==0`, DETLOG tail-match with the
deterministic 8-syscall e9loader prologue removed). A candidate is KEPT only if
native, golden, and e9 all pass and agree; any guest failing native OR golden is
DROPPED (no false parity, hermit issue #152).

**Environment note.** The fleet PMU was heavily contended during vetting by ~365
concurrent `--verify` processes (loadavg ~790). These are single-syscall probes
with minimal retired-conditional-branch counts, so they hit L2 on the first
verify attempt for both golden and e9 despite the load; the verify legs were run
with a short timeout + process-group kill (`killpg`, SIGKILL) on a wedge as a
precaution. Native and `--strict` (non-verify) runs are unaffected by PMU load.

## Kept (4)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| futex_wake_empty | futex(202) FUTEX_WAKE | no waiters → 0 woken | `futexwake=0` |
| gettimeofday_check | gettimeofday(96) | success, timeval not printed | `gettimeofday=0` |
| sched_getattr_self | sched_getattr(315) | own sched_attr query → 0 | `schedgetattr=0` |
| sched_setaffinity_self | sched_setaffinity(203) | get+set identical mask → 0 | `setaffinity=0` |

`futex_wake_empty` wakes zero threads because the guest is single-threaded and
nothing is blocked on the futex word — a deterministic 0 exercising the futex
FUTEX_WAKE fast path (not a blocking wait). `gettimeofday_check` uses syscall 96,
distinct from `clock_gettime(228)`, and prints only the return (0); the
virtualized `timeval` is host-dependent and never emitted. `sched_getattr_self`
reads its own scheduling attributes via the unified query and prints the return
(0), not the policy/nice fields. `sched_setaffinity_self` reads the current CPU
affinity mask and writes the identical mask back — a no-op returning 0 — and
never prints the host-dependent mask.

## Dropped (0)

The inert-query vein is clean: all four candidates were kept, in contrast to
round 32's data-movement batch (5/8 dropped for `-ENOSYS`/`-EPERM`). This
confirms the round-32 lesson — prefer inert probe/query syscalls over zero-copy /
cross-address-space data-movement syscalls, which golden hermit does not support.

## Results

- native: 4/4 exit 0 with expected stdout.
- golden ptrace: 4/4 L2verified=1; native==golden and expected stdout matched.
- e9patch: 4/4 PASS_L2 exit=0, sites c/1 m/1 b0/0, prologue=8, tail_match=yes.
- audit-inventory: exit 0 (599 files, 254 guest fixtures).
- corpus size: 217 → 221.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
