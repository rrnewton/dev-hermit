# Self-determinizing guests: native as the golden reference

## The technique (this is the point; the syscall count is only guest #1)

**Construct a guest whose observable behaviour is fixed BY CONSTRUCTION, prove
the invariant holds under native execution including parallel stress, and only
then assert that every backend reproduces it.**

Two properties make a guest a yardstick, and both are required:

1. **The invariant is structural, not statistical.** "Exactly 10,000 getpid" is
   a property of the instruction stream. "About 2ms of work" is a property of
   the machine. Never accept a tolerance where a construction is available.
2. **Native is the reference, not a backend.** Comparing ptrace against KVM
   tells you they disagree, not which is wrong; nominating a backend as
   reference enshrines its bugs as correct behaviour. Native has no stake in
   the answer.

### How to invent guest #4

Pick a property, then ask what would make it vary, and remove that cause rather
than tolerating it:

| Invariant | Guest shape | Removed cause of variance |
| --- | --- | --- |
| exactly K syscalls | freestanding loop, raw `syscall` | libc startup, stdio buffering |
| exact byte sequence on stdout | write a fixed buffer with one `write` | buffering mode, locale |
| exact file content after run | `open`/`write`/`close` fixed bytes | umask, tmp path, ordering |
| exact memory layout | fixed `mmap` at a fixed address | ASLR |
| exact exit code from computed state | self-check, exit non-zero on mismatch | needs no external observer |

The last row is the strongest pattern and worth defaulting to: **make the guest
check itself and encode the verdict in its exit code.** Then validating it
natively and validating it under a backend are the same one-line check, and the
result does not depend on an observer that might have its own bugs.

## Guest #1 — `guest_k_syscalls.c`

**Invariant, stated precisely:** exactly **10,000** `getpid` syscalls, then
exactly one `exit_group(0)`. Total 10,001. Nothing else, in any environment.
Exit code is 0 if and only if the loop ran 10,000 times and every `getpid`
returned the same value; 2 or 3 otherwise.

Built `-static -nostdlib -nostartfiles` with its own `_start`. That is not
fastidiousness: the invariant is destroyed by libc, not by the loop. The
DBT-vs-ptrace comparison already in `reverie/experimental/` found precisely
this, a glibc `prlimit64` probe on one side and an `ioctl` probe on the other,
neither a tool defect.

`getpid` is chosen because it cannot block, cannot be interrupted (so no EINTR
restart adds a syscall under load), takes no arguments, and returns a value the
guest can check itself.

## Validating the instrument — `validate_guest.sh`

| Gate | Checks |
| --- | --- |
| G1 | exit code 0 |
| G2 | exactly EXPECT syscalls of the counted kind |
| G3 | same count with stdout a pipe and a file |
| G4 | G1 and G2 hold across N=20 concurrent runs |

A failure **rejects the guest**, and says nothing about any backend.

### Result: guest #1 ACCEPTED

    G1 exit code                    0 OK
    G2 getpid count                 10000 OK
    G3 pipe=10000 file=10000        OK
    G4 exit codes (20 runs)         20/20 zero OK
    G4 getpid counts (20 runs)      all 10000 OK
    VERDICT: ACCEPTED as a yardstick

### Result: the gate DISCRIMINATES — and only the stress gate caught it

`guest_rejected_nondet.c` loops until 2ms has elapsed. It reads like a bounded,
reasonable workload. Same gate, same command:

    G2 sched_yield count            270 OK          <-- PASSED the single run
    G3 pipe=277 file=260            REJECT
    G4 sched_yield counts (20 runs) 14 distinct values, 64..148   REJECT
    VERDICT: REJECTED

**It passed the single-run check and was caught only under 20x parallel load,
where the count more than halved.** That is why the stress condition is the
crux rather than a nicety: a gate that ran only G1/G2 would have accepted this
guest, and every backend comparison against it afterwards would have scored
machine load and reported it as backend disagreement.

## Two traps found while building this

- **vDSO calls are invisible to syscall counting.** The first version of the
  rejected guest timed itself with `clock_gettime`, and the counted total was
  **0** — glibc routes it through the vDSO and it never traps. A guest whose
  "syscalls" are vDSO calls will read as zero on every backend and look like
  perfect agreement. Choose counted syscalls that provably trap.
- **The only external counter available here is ptrace-based**, and ptrace is
  itself a backend we intend to compare. `perf` tracepoints are blocked at
  `perf_event_paranoid=1` on this host. So `strace -c` is used as
  **corroboration only, never as the golden reference** — the golden signal is
  the guest's own exit code, which needs no observer. A yardstick that can only
  be read through one of the things it is meant to judge is not golden.

## Scope

Backends were deliberately **not** compared. That direction is an open owner
decision; this artifact establishes only that the instrument is trustworthy.
