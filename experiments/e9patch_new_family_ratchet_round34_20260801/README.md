# e9patch corpus ratchet — round 34 (inert timer/futex/wait/membarrier probes)

## Question

Round 34 of the standing e9patch corpus ratchet. Can freestanding raw-syscall
x86-64 guests for six inert query/no-op syscalls on previously uncovered
boundaries — an unarmed POSIX timer (`timer_create` + `timer_gettime`), an
unarmed `timerfd_gettime`, a non-blocking `futex(FUTEX_WAIT)` value mismatch,
`wait4(WNOHANG)` with no children, `membarrier(CMD_QUERY)`, and
`getrusage(RUSAGE_THREAD)` — reach L2 parity across the golden ptrace backend and
the e9patch-rewritten ptrace path?

Rounds 32–33 established that the *inert query/no-op* vein is clean (round 33:
4/4 kept) while the *data-movement/zero-copy* vein is a dead vein (round 32: 5/8
dropped for golden `-ENOSYS`/`-EPERM`). Round 34 stays on the inert vein and
probes new families: POSIX/timerfd timer *queries* (not arming), the
non-blocking error boundaries of `futex` and `wait4`, a `membarrier` query, and a
distinct `getrusage` who-target.

## Method

Each candidate is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values (a fixed constant return, a fixed errno, or a boolean).
Each was native-tested, then golden-hermit-ptrace L2-tested (`--strict
--verify`, "Determinism verified"), then e9patch L2-tested (candidate_sites>0,
mapped==candidate, no SIGILL fallback `b0==0`, DETLOG tail-match with the
deterministic 8-syscall e9loader prologue removed). A candidate is KEPT only if
native, golden, and e9 all pass and agree; any guest failing native OR golden is
DROPPED (no false parity, hermit issue #152).

**Environment note.** The fleet PMU was moderately contended during vetting
(loadavg ~680, ~43 concurrent `--verify` processes — down from round 33's ~365).
These single-syscall probes have minimal retired-conditional-branch counts, so
they hit L2 on the first verify attempt for both golden and e9; the verify legs
were run through a `killpg`-on-wedge retry harness as a precaution. Native and
`--strict` (non-verify) runs are unaffected by PMU load.

## Kept (6)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| timer_create_gettime | timer_create(222)+timer_gettime(224) | unarmed POSIX timer query → 0 | `timergettime=0` |
| timerfd_gettime_unarmed | timerfd_gettime(287) | unarmed timerfd query → 0 | `timerfdgettime=0` |
| futex_wait_mismatch | futex(202) FUTEX_WAIT | value mismatch → -EAGAIN, non-blocking | `futexwait=-11` |
| wait4_nochild | wait4(61) WNOHANG | no children → -ECHILD | `wait4=-10` |
| membarrier_query | membarrier(324) CMD_QUERY | success as boolean | `membarrierquery=1` |
| getrusage_thread | getrusage(98) RUSAGE_THREAD | thread rusage query → 0 | `getrusagethread=0` |

`timer_create_gettime` creates a per-process CLOCK_MONOTONIC timer and queries it
before arming, so `timer_gettime` reports a zeroed `itimerspec` and returns 0
(the spec is not printed). `timerfd_gettime_unarmed` does the same on a
`timerfd`, distinct from `timerfd_create_check` (creation only).
`futex_wait_mismatch` supplies an expected value that does not match the futex
word, so the kernel returns `-EAGAIN` from the pre-sleep recheck without ever
blocking or registering a timed waiter. `wait4_nochild` reaps with `WNOHANG` and
no children, returning `-ECHILD` immediately. `membarrier_query` prints only a
boolean because the supported-command bitmask is host/kernel-dependent.
`getrusage_thread` uses `RUSAGE_THREAD` — a distinct who from `getrusage_self`'s
`RUSAGE_SELF` — and prints only the return, never the timing-dependent fields.

## Dropped (0)

The inert-query vein remains clean: all six candidates were kept, extending the
round-33 result (4/4) and reconfirming the round-32 lesson — prefer inert
probe/query syscalls and non-blocking error boundaries over zero-copy /
data-movement syscalls, which golden hermit does not support.

## Results

- native: 6/6 exit 0 with expected stdout.
- golden ptrace: 6/6 L2 "Determinism verified"; native==golden and expected
  stdout matched.
- e9patch: 6/6 PASS_L2 exit=0, sites c/1 m/1 b0/0, prologue=8, tail_match=yes.
- audit-inventory: exit 0 (605 files).
- corpus size: 221 → 227.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
