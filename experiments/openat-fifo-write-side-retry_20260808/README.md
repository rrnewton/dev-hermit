# openat-on-FIFO, write side: the retry converts, but starves its own peer

**Question.** Defect-class instance 3 (`openat` on a FIFO blocks while holding the
scheduler turn) was recorded as *"specified, DECLINED, needs `BlockedPool`"*. The
design note argued the **write** side is separable and needs no new machinery:
`O_WRONLY|O_NONBLOCK` on a readerless FIFO returns `ENXIO`, an exact "no reader
yet", so a yield-and-retry loop should resume precisely when a blocking open
would. Is that true?

**Answer: the conversion works and both documented obstacles dissolve — but a
third obstacle, not identified in the design note, defeats it.**

## A 10-line reproducer, no nix stack required

`harness/fifo_writer_open.c`: parent `mkfifo`s, forks a reader that opens
`O_RDONLY`, and itself opens `O_WRONLY`. Native it completes instantly. Under
`hermit run --strict --no-virtualize-cpuid` it hangs until killed.

| arm | outcome |
|---|---|
| native | **PASS**, `FIFO-OK exit=0`, <1 s |
| `hermit --strict`, baseline | **DEADLOCK** (rc=143) |
| `hermit --strict`, write-side retry patch | **DEADLOCK** (rc=143) |

This replaces the previous reproducer (a nixpkgs `fixupPhase` build) with
something that builds in a second, which is the main reusable output here.

## The two documented obstacles both dissolve

1. *"The turn-yielding framework is fd-keyed and `open` has no fd yet"* —
   true of `ioaction_based_on_fd_status`, but `handle_openat` can call
   `retry_nonblocking_syscall_with_timeout` **directly** and bypass that
   dispatch entirely.
2. *"Every would-block predicate keys on `EAGAIN`/`EWOULDBLOCK`"* —
   `syscall_would_have_blocked` is an **overridable trait method**, not a
   hard-coded predicate. An `impl NonblockableSyscall for Openat` that returns
   `res == Err(Errno::ENXIO)` is the whole of it.

The patch (`openat-fifo-write-retry.patch`, 130 lines) does both, compiles
clean, and is confirmed live: the injected call carries `arg2: 2049`
(`O_WRONLY|O_NONBLOCK`) and the retry loop drives the scheduler forward.
A regular-file write-open control is unaffected.

## The third obstacle: the retrying writer starves the peer it is waiting for

From one debug run:

| task | turns committed |
|---|---|
| dettid 3 (writer, retrying) | **309,428** |
| dettid 5 (reader, the partner) | **3** |

The reader sits `going back into queue at position (p: 1000, t: 9)` while the
writer spins. Yield-and-retry is only correct if yielding actually lets the peer
run; here the scheduler returns the turn to the poller immediately, so the loop
is a busy-wait that guarantees the condition it waits for can never become true.
`rsrc.poll_attempt` increments but does not deprioritise enough to matter.

**So the original DECLINE was right, for a reason it did not state.** The blocker
is not `ENXIO` matching and not the fd-keyed dispatch — both are easy. It is that
a polling retry cannot make progress against a peer that needs a turn to satisfy
it. That is a scheduler-fairness property, and it is why this genuinely wants
`BlockedPool`-style modelling: the waiter must be *descheduled until the peer
acts*, not polled.

Note this reproducer is harsher than the nixpkgs case in one respect: **both**
ends open under Hermit, so the read side blocks too (`O_RDONLY` with no writer
hangs, and its `O_NONBLOCK` form yields a spurious EOF — the reason the read side
was never convertible).

## Status of the patch

Kept as an artifact, **not proposed for landing**. It is necessary-but-not-
sufficient: correct in what it does, and it would still need the fairness fix to
change any outcome. Landing it alone would add a busy-wait with no user-visible
benefit.

## Reproduction

```
gcc -O0 -o fifo_writer_open harness/fifo_writer_open.c
./fifo_writer_open                     # native: FIFO-OK
hermit run --strict --no-virtualize-cpuid -- ./fifo_writer_open   # hangs
```
Run it outside host `/tmp` (or pass `--tmp=/tmp`); Hermit replaces guest `/tmp`.
