# Closeable admission and quiescence

`CloseableSnzi` is a one-generation admission barrier for shared-memory state.
It combines a one-word close gate with the scalable presence tracking of SNZI.
The barrier answers a narrow question: after permanently rejecting new work,
have all successfully admitted participants departed?

## Protocol and linearization

The gate word stores `CLOSED`, `POISONED`, `CHECKING`, `DRAINED`, and a 60-bit
count of transient operations. Entry is a three-step protocol:

1. Compare-exchange the gate from `n` to `n + 1`, failing if `CLOSED` or
   `POISONED` is set. This is the entry/close ordering point.
2. Publish an arrival in the SNZI tree.
3. Decrement the gate reservation and return a linear `AdmissionToken`.

Departure takes another transient reservation before touching the SNZI and
holds it until the complete departure method has returned. `close` atomically
sets `CLOSED` in the same word. An entry whose reservation won first may finish;
an entry whose compare-exchange observes close is rejected.

`is_drained` first rejects an obviously active SNZI, then changes exactly
`CLOSED | count(0)` to `CHECKING`. Departure starts wait while that state is
present, making the full SNZI scan stable. A healthy, quiescent scan changes
`CHECKING` to terminal `DRAINED`; a non-quiescent scan reopens departure. The
early active check ensures repeated scanners cannot continually seize
`CHECKING` and starve a token which needs to depart. The scan requires all of
the following:

- close is visible;
- the gate is not poisoned;
- no entry publication or departure reservation remains; and
- the complete SNZI diagnostic scan is healthy and quiescent.

The reservation bridges the otherwise unsafe interval during which an entrant
has passed the gate but is not yet represented at the SNZI root. Once close is
set and reservations reach zero, no new SNZI arrival can begin. Counting the
complete departure method is necessary because SNZI can zero its root shortly
before its final poison load; reclaiming on root zero alone could unmap payload
state beneath that method tail. A true drain result is stable for the protected
payload in that generation.

`DRAINED` does not prove that the barrier's own pages may be unmapped. A caller
can pause inside `is_drained`, `query`, or `debug_snapshot` while holding `&self`;
those diagnostic calls are intentionally not recursively counted. An outer
mapping lifecycle must stop and drain all callers/references before unmapping
the page which contains `CloseableSnzi`. Use this barrier to authorize payload
reclamation within a still-live control mapping, or nest it under that outer
lifecycle.

## Crash matrix

| Failure point | Persistent evidence | Drain result |
| --- | --- | --- |
| Before reserving | none | unaffected |
| After reserving, before arrival completes | leaked reservation | false |
| After arrival, before returning the token | leaked reservation and/or arrival | false |
| While holding a returned token | leaked SNZI arrival | false |
| During departure before its linearization point | reservation and arrival remain | false |
| After departure linearizes but before it returns | departure reservation | false |
| During terminal quiescence scan | `CHECKING` | false unless that caller seals `DRAINED` |
| After terminal `DRAINED` is sealed | no participation remains | permanently true |

Tokens deliberately do not depart in `Drop`. `fork` duplicates Rust values
without duplicating their logical ownership, and cancellation or unwinding
cannot decide that a cross-process operation is complete. Explicit consumption
makes ownership visible. Losing a token leaks presence and is safe for memory
reclamation because it fails closed.

Fork only after operations and tokens are quiescent. If a live token is
inherited, exactly one process may consume it; the other must leave it untouched
and proceed directly to `exec` or `_exit` without unwinding. Re-entering pod
methods from a signal handler which interrupts an entry or departure is not
supported. A copied Rust value is not another logical reservation.

The current implementation does not identify or remove a dead participant. A
drain-checking process which dies leaves `CHECKING` set and departures waiting;
this is also intentionally fail closed. The scan is bounded by the configured
SNZI tree size during normal operation. Recovery requires a supervisor to fence
admission, prove that every process which could retain a reference has stopped
(prefer pidfds over reusable numeric PIDs), and discard or repair the complete
mapping generation. Do not clear a reservation or synthesize a departure in
place.

## Why timeout and leases do not repair it

Elapsed time is not proof of owner death. A process can be descheduled, stopped
by a debugger or `SIGSTOP`, delayed by a page fault, or paused during host
suspend. It may later resume with live Rust references. Expiring its token and
reusing protected memory would create use-after-free even if every clock agrees.

Leases are appropriate only when every protected effect validates a monotonic
fencing token at its commit point. Chubby calls such a token a *sequencer*.
Generic shared references and in-place writes have no recipient at which to
reject a stale generation, so `CloseableSnzi` never converts a timeout into
departure. See [Process-shared locking and crash recovery](locking.md) for the
full robust-mutex, lease, clock-domain, and fencing analysis.

## Precedents and boundary

- Ellen, Lev, Luchangco, and Moir's SNZI algorithm supplies scalable arrivals,
  departures, and a constant-time nonzero query. The close gate is an external
  lifecycle layer, not part of the original algorithm.
- Linux `percpu_ref_kill()` prevents new references and transitions a scalable
  per-CPU reference to an atomic mode so a release callback can observe zero.
  Its surrounding subsystem controls object lifetime; it is not a mechanism for
  recovering references leaked by crashed user processes.
- Linux RCU and SRCU divide removal from reclamation: first stop publishing new
  references, then wait for every pre-existing read-side critical section to
  pass through a quiescent state. They rely on kernel-managed participants and
  progress assumptions that arbitrary user processes do not automatically meet.
- POSIX robust process-shared mutexes report owner death (`EOWNERDEAD`) because
  the kernel knows the owning thread terminated. They still require
  type-specific data repair before `pthread_mutex_consistent`.
- Gray and Cheriton leases, and Chubby sequencers, support bounded authority only
  when stale operations are fenced. They do not justify revoking `&T` or `&mut T`.

## References

- Ellen et al., "SNZI: Scalable NonZero Indicators", PODC 2007:
  <https://doi.org/10.1145/1281100.1281106>
- Lev, Luchangco, and Olszewski, "Scalable Reader-Writer Locks", SPAA 2009
  (introduces the closeable C-SNZI variant):
  <https://doi.org/10.1145/1583991.1584020>
- Linux `percpu_ref` API: <https://docs.kernel.org/core-api/percpu-refcount.html>
- Linux RCU concepts: <https://docs.kernel.org/RCU/whatisRCU.html>
- Linux SRCU API: <https://docs.kernel.org/RCU/Design/Requirements/Requirements.html>
- POSIX robust mutex recovery:
  <https://man7.org/linux/man-pages/man3/pthread_mutex_lock.3p.html>
- Gray and Cheriton, "Leases", SOSP 1989: <https://doi.org/10.1145/74850.74870>
- Burrows, "The Chubby Lock Service", OSDI 2006:
  <https://www.usenix.org/legacy/event/osdi06/tech/full_papers/burrows/burrows.pdf>
