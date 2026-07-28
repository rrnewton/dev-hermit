# Process-shared locking and crash recovery

Shared memory removes the kernel message boundary, but it does not remove
failure boundaries. A process can stop between any two instructions while it
has exclusive access to shared bytes. Lock acquisition, bounded waiting,
owner-death detection, and data recovery are therefore separate mechanisms.

This guide states which guarantees `shmem-pod` provides today and which
protocols are required before an application may recover after a crash.

## Choose the primitive by failure model

| Primitive | Contention behavior | Owner death | Appropriate use |
| --- | --- | --- | --- |
| Atomics | No software lock | No owner to abandon | Counters, flags, and carefully designed lock-free state |
| `ProcessSpinMutex` | Busy-spins | Remains locked | Very short critical sections in a supervised process set |
| `ProcessFutexMutex` | Spins briefly, then sleeps | Remains locked | General Linux process-shared critical sections where restart is acceptable |
| POSIX robust process-shared mutex | Sleeps; libc and kernel cooperate | Reports `EOWNERDEAD` | Future recoverable backend with type-specific state repair |
| Fenced lease | Application-specific | Rejects stale generations | External or versioned resources which check every fencing token |

Neither mutex in this release is robust. A process that exits while holding a
guard leaves the lock permanently locked. That fail-closed behavior preserves
exclusive access; it is not a data-recovery feature.

## Bounded waiting is not a lease

On Linux, `ProcessFutexMutex::try_lock_for` bounds how long the caller waits:

```rust
use core::time::Duration;
use shmem_pod::sync::ProcessFutexMutex;

let counter = ProcessFutexMutex::new(0_u64);
match counter.try_lock_for(Duration::from_millis(20)) {
    Ok(Some(mut guard)) => *guard += 1,
    Ok(None) => { /* still owned: cancel, retry, or report overload */ }
    Err(error) => { /* clock_gettime/futex was unavailable; fail closed */ }
}
```

The method first attempts immediate acquisition. It then uses
`FUTEX_WAIT_BITSET` with one absolute `CLOCK_MONOTONIC` deadline. `EINTR`,
spurious wakeups, and contention retries do not restart the duration. A zero
duration still succeeds when the mutex is immediately available.

Timeout has exactly one meaning: this caller stopped waiting. It does not
unlock the mutex, infer that the owner died, poison the protected value, or
grant a second guard.

This distinction is required for Rust soundness. The owner may merely be
descheduled, stopped by `SIGSTOP`, paused in a debugger, blocked on a page
fault, or delayed by overload. It still has a live guard and may resume with a
valid `&mut T`. Giving another process a guard after a deadline would create
simultaneous mutable references and a data race.

The timeout syscall can fail under seccomp or another sandbox policy.
`FutexLockError::raw_os_error` exposes the positive Linux error number. An
injector should probe the `futex` and `clock_gettime` policy before admitting
work; treating a denied wait as owner death would be unsafe.

## Recovery for the current non-robust mutex

Do not reset a stuck lock word in place. Even an apparently dead numeric PID
does not prove that every thread and every copied guard is gone, and PID values
can be reused.

The safe recovery unit today is a complete mapping generation:

1. Mark the pod instance unhealthy in supervisor-owned state so no new process
   can attach.
2. Stop every admitted process which could hold a reference into the mapping.
3. Confirm that all of them have exited. Prefer pidfds obtained at process
   creation; a bare PID plus `kill(pid, 0)` is not a stable identity.
4. Discard the old mapping without reusing or rewriting its mutexes.
5. Create and initialize a new backing object with a new instance generation.
6. Validate the new layout and publish readiness before admitting callers.

This is a restart protocol, not mutex recovery. Application state which must
survive the restart needs its own journal, immutable snapshot, or external
source of truth.

A pidfd is process-local kernel state, so its integer descriptor must not be
stored as a cross-process identifier in shared bytes. A supervisor can retain
pidfds, create children with `CLONE_PIDFD`, or transfer pidfds over a Unix
socket. Linux 6.9 added `PIDFD_THREAD` for thread-specific pidfds, but robust
futexes remain the direct kernel mechanism for arbitrary thread-owner death.

## Why robust futexes are a separate backend

The established Linux interface is a POSIX mutex initialized with both
`PTHREAD_PROCESS_SHARED` and `PTHREAD_MUTEX_ROBUST`. When an owner terminates,
the next lock operation acquires the mutex and returns `EOWNERDEAD`. The new
owner must repair the protected state and call `pthread_mutex_consistent`.
Unlocking without doing so makes the mutex permanently `ENOTRECOVERABLE`.

That notification proves that the old thread no longer executes. It does not
prove that the bytes it was modifying form a valid application transaction.
A sound Rust API should therefore distinguish these outcomes:

```text
Consistent(Guard<T>)
OwnerDied(RecoveryGuard<T>)
NotRecoverable
TimedOut
```

`RecoveryGuard<T>` must not implement ordinary `Deref` or `DerefMut` before a
type-specific recovery procedure establishes the invariant. Explicitly marking
it consistent may then produce an ordinary guard. Dropping or abandoning it
without repair must leave the mutex not recoverable.

Implementing this by registering another raw robust-futex list in pod code is
not compatible with arbitrary host threads:

- Linux records one robust-list head per thread.
- glibc normally registers that head when it creates a thread.
- Every entry on one kernel list uses the same node-to-futex offset.
- `list_op_pending` and exact insertion/removal ordering cover death at each
  instruction boundary.
- Kernel documentation warns that pure userspace robust unlock has an
  unlock/unmap race; newer kernels add a kernel/vDSO-assisted unlock protocol.

Calling `set_robust_list` from a pod would replace the host libc's registration
and could strand unrelated robust pthread mutexes. A future implementation
should instead use a host import backed by the process's pthread runtime. It
must initialize the opaque mutex in its final address before publication and
include the libc synchronization ABI in the attach handshake. The executable
pod must not assume that glibc and musl mutex representations are interchangeable.

## Leases require fencing

Leases solve a different problem. Once a lease expires, an old holder may
still run or may deliver a delayed operation. Correct lease systems attach a
monotonically increasing fencing token to every protected effect, and the
recipient rejects tokens older than the greatest token it has accepted.

Chubby calls this token a *sequencer*: it includes the lock generation, and
servers validate it before performing a protected operation. Chubby also has a
lock-delay for systems that cannot check sequencers, but describes lock-delay
as imperfect. Delay reduces the probability of stale work; it is not a proof
of exclusion.

A fenced shared-memory API cannot safely return `&mut T` and later revoke it.
It must instead expose operations whose commit point checks the token. One
possible fixed-capacity design is:

1. Increment an atomic epoch when granting a new lease.
2. Give each epoch a distinct staging slot which an older writer cannot share.
3. Write a complete candidate value and checksum into that slot.
4. Publish `(epoch, slot, version)` with one compare-exchange which fails after
   a newer epoch has been granted.
5. Reclaim old staging slots only after confirmed owner death or another
   quiescence proof.

Merely checking the epoch before an in-place write has a check-then-use race.
Every externally visible commit must be fenced.

Clock choice is part of a lease protocol. `CLOCK_REALTIME` can jump.
`CLOCK_MONOTONIC` does not jump with wall-clock changes but does not include
system suspend. `CLOCK_BOOTTIME` includes suspend. Linux time namespaces can
give processes different monotonic offsets, so processes which exchange an
absolute timestamp need an explicit common clock-domain contract. The timed
mutex API does not exchange deadlines: each caller computes and consumes its
own deadline in one time namespace.

## Data recovery patterns

Owner-death notification serializes a recovery attempt; it does not make an
arbitrary `T` crash-consistent. Use a representation with a documented repair
rule:

- Atomic counters and flags often need no lock recovery.
- A two-slot value can write and checksum the inactive slot, then atomically
  publish its slot and generation. An interrupted unpublished slot is ignored.
- An append-only or write-ahead journal records intent and commit state before
  changing reusable allocator metadata.
- Poisoning is appropriate when the invariant cannot be reconstructed. Future
  accesses fail closed instead of guessing.

A sequence counter alone is not recovery. If its writer dies after making the
sequence odd, readers retry forever. Linux also requires sequence-counter
writers to be serialized, and warns against pointer-bearing protected data.

## Validation checklist

A recoverable backend should not ship until tests cover all of these cases:

- a waiter sleeps and wakes through different virtual mappings of one memfd;
- an absolute timeout survives repeated signal interruption without extending;
- timeout racing with normal unlock either acquires once or returns timeout;
- `SIGSTOP` past a timeout does not transfer ownership;
- `SIGKILL`, thread exit, process exit, and `execve` while holding report owner
  death only for the robust backend;
- clean repair, abandoned repair, and death of the recovery owner produce the
  specified `EOWNERDEAD`/`ENOTRECOVERABLE` transitions;
- failure at every robust-list insertion and removal step cannot lose cleanup;
- a host pthread robust mutex and a pod robust mutex coexist in one thread;
- mapping destruction is impossible while a guard, waiter, or robust-list node
  can still refer to it;
- seccomp-denied clock/futex calls fail closed rather than busy-looping;
- layout and backend ABI mismatches are rejected before typed access.

The current test suite includes normal cross-process wakeup, exact contention
totals, different-address exec attachment, zero-duration acquisition, timed
normal wakeup, a stopped owner which is not robbed, and a killed owner which
remains locked.

## References

- Linux `FUTEX_WAIT`: <https://man7.org/linux/man-pages/man2/FUTEX_WAIT.2const.html>
- Linux `FUTEX_WAIT_BITSET`: <https://man7.org/linux/man-pages/man2/FUTEX_WAIT_BITSET.2const.html>
- Linux robust-futex ABI: <https://docs.kernel.org/locking/robust-futex-ABI.html>
- Linux robust-futex design: <https://docs.kernel.org/locking/robust-futexes.html>
- `get_robust_list`/`set_robust_list`: <https://man7.org/linux/man-pages/man2/set_robust_list.2.html>
- POSIX process-shared mutex attributes: <https://man7.org/linux/man-pages/man3/pthread_mutexattr_setpshared.3.html>
- POSIX robust mutex attributes: <https://man7.org/linux/man-pages/man3/pthread_mutexattr_setrobust.3.html>
- POSIX robust lock recovery: <https://man7.org/linux/man-pages/man3/pthread_mutex_lock.3p.html>
- `pidfd_open`: <https://man7.org/linux/man-pages/man2/pidfd_open.2.html>
- Linux time namespaces: <https://man7.org/linux/man-pages/man7/time_namespaces.7.html>
- Linux sequence counters: <https://docs.kernel.org/locking/seqlock.html>
- Gray and Cheriton, "Leases", SOSP 1989: <https://doi.org/10.1145/74850.74870>
- Burrows, "The Chubby Lock Service", OSDI 2006:
  <http://usenix.org/event/osdi06/tech/full_papers/burrows/burrows_html/index.html>
