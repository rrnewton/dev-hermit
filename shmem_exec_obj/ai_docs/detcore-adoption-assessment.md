# Detcore adoption assessment

[impl agent, gpt-5.6-sol]

## Conclusion

Moving Detcore's entire `GlobalState` into a shared executable object is not a
representation-only change. It would require a major scheduler, async runtime,
file-ownership, and lifecycle redesign. A useful first deployment is feasible:
keep scheduler and OS authority in a host-local control plane, and move bounded
clocks, counters, and selected tables into a relocatable shared data plane whose
pure synchronous methods can execute from the authenticated pod image.

Assessed revisions:

- Hermit: `e3067d69f972a57247a72c3cfa2624691e8439a7`
- Reverie: `f237446733dd646e0a0c582e922b1b914971bb2e`
- Published shmem-pod connector baseline: `9b9d6325ce29e8db85cd58f8d2eb7dddba8cd757`
- Published shmem-pod validation baseline: `f8e44647f2f0cdd7412a125c02be3c80f4616c53`

The shmem-pod baselines above were still undergoing adversarial remediation at
the time of this refresh. They identify the concrete API and implementation
reviewed here, not a release-acceptance claim.

## Current Detcore graph

Detcore's `GlobalState` is the Reverie `GlobalTool`. It owns the scheduler,
inode and device pools, port allocation state, used-port and open-file maps,
unsupported-syscall reporting state and a `File`, an async task handle, global
time, full configuration, preemption input, and host `SystemTime`
(`hermit/detcore/src/tool_global.rs`). The inode/device pools contain ordinary
heap `HashMap`s.

The scheduler is an interconnected graph of `BTreeMap`, `HashMap`, `HashSet`,
`Vec`, run queues, blocked pools, thread ancestry, record/replay state, PRNGs,
and `Ivar` endpoints (`hermit/detcore/src/scheduler.rs`). Each `Ivar` is an
`Arc<Mutex<...>>` containing an optional value and async `Waker`
(`hermit/detcore/src/ivar.rs`). These objects are allocator-, executor-, and
process-local.

Thread-local Detcore state also encodes sharing through `Arc<Mutex<_>>`, owns
dynamic sets/vectors and PRNGs, and carries generic subtool state
(`hermit/detcore/src/tool_local.rs`). File aliases use
`HashMap<RawFd, DetFd>` and `Arc<Mutex<OpenFileDescription>>`; final-alias
behavior depends on `Arc::strong_count` (`hermit/detcore/src/fd.rs`). A raw FD
and a Tokio waker cannot become cross-process merely because their containing
bytes are mapped shared.

All scheduling, resource, lifecycle, futex, time, timer, signal, port,
reporting, and shutdown operations currently funnel through `GlobalRPC`
(`hermit/detcore/src/tool_global.rs`). Every request also updates a vector
clock backed by a `HashMap` (`hermit/detcore-model/src/time.rs`).

The latest SaBRe lifecycle work makes the scheduler boundary more explicit,
not easier to map wholesale. `receive_rpc` now holds scheduler admission while
accounting clock state, permanently tombstones logical threads, rejects raw TID
reuse, rechecks tombstones after awaited operations, and makes asynchronous
deregistration accounting idempotent. These invariants span the scheduler,
global clock, run queue, `Ivar` responses, and backend RPC lifetime. A shared
fast path therefore needs generation-tagged thread slots and cancellation
epochs; a plain shared request queue keyed by Linux TID would be incorrect.

## Representation classification

| Class | Detcore state |
| --- | --- |
| Direct POD after audited encoding | Port/exec atomics; scalar IDs, logical times, flags, counters, limits, and immutable scalar configuration subsets |
| Relocatable shared storage | Bounded inode/device/port tables, timer records, CPU accounting, preemption records, thread-tree snapshots, and queue descriptors |
| Process-shared synchronization plus recovery | Allocator metadata, OFD alias counts, global-time aggregation, table transactions, and every scheduler invariant currently behind an ordinary mutex |
| Must remain host-local | Tokio tasks/wakers, `File` and raw FDs, paths and filesystem I/O, signals, backtraces, guest memory/register access, sockets, and ptrace/KVM/DBI handles |
| Major redesign | Scheduler authority, `Ivar`, generic record/replay state, `Arc`-encoded clone semantics, and async generic `Tool`/`Guest` calls |

Rust enum and `Option` layouts should not be treated as a stable wire ABI even
when they happen to contain only scalars. Shared forms need explicit integer
tags, range validation, and schema fingerprints.

## Fixed VA versus relocatable

A reserved high virtual address can make pointers allocated from one mapped
generation valid in every participant. It reduces conversion work for custom
allocator-aware collections, but it does not make `Arc`, standard collections,
Tokio wakers, FDs, or `std::sync::Mutex` process-shared. It also requires early
`MAP_FIXED_NOREPLACE` reservation across every exec image and makes collision
handling part of process admission.

A fully relocatable representation is the stronger long-term design. Every
link is a checked offset from a validated mapping base and every owning header
is stored in shared pages. This requires replacing standard collection headers
and `Arc` ownership with generation-tagged handles. The current relocatable
allocator and fixed-capacity collections prove the model but are too bounded
for an unmodified Detcore scheduler; production tables need deterministic
capacity planning or a segmented allocator.

## Recommended split

Introduce two explicit Detcore layers:

```text
GlobalHost
  scheduler authority, async tasks, Config, FDs/files, signals,
  backend handles, cleanup, recovery supervisor

SharedGlobalCore
  schema/generation header, clock and counter slots, bounded numeric tables,
  sequence mailboxes, process-shared synchronization, pod method state
```

`GlobalHost` owns and authenticates the mappings. It is the only component
allowed to perform OS effects or repair/reclaim state. `SharedGlobalCore` is
pointer-free (or fixed-VA allocator-only in the alternative mode), contains no
destructors or process-local resources, and exposes only bounded synchronous
operations.

Reverie can preserve its public tool API initially by making `GlobalState` a
host wrapper around this mapping. General backend support later needs additive
hooks for shared-global export/attach, fork/exec and abnormal-exit lifecycle,
and a backend-local attachment context that is not serialized into
`ThreadState`.

On the Detcore side, the first credible internal interface is narrower than
`GlobalRequest`:

```text
ThreadSlot = { logical_tid, generation, lifecycle_epoch, clock, seq }
FastRequest = { slot_handle, expected_epoch, opcode, bounded payload }
FastResponse = { seq, status, bounded payload }
```

The host must allocate and tombstone slots, validate every generation/epoch,
and retain the authoritative fallback RPC. Scheduler grants, thread creation
and teardown, futex queues, signal targeting, and any operation that can await
an `Ivar` remain host operations. This avoids creating a second scheduler whose
linearization rules merely resemble Detcore's current one.

The pod ABI also needs to grow beyond the current scalar demonstration
signatures. Production Detcore calls need stable request/response records,
status codes, size/version checks, and an authenticated allowlisted host-import
table. Rust references, futures, trait objects, unwinding, and host allocator
ownership cannot cross that ABI.

## Staged migration

1. Measure current `GlobalRPC` categories separately for ptrace, KVM, DBI,
   LiteInst, and SaBRe. Define stable encodings and a generation handshake.
2. Add shared fixed thread-clock/counter slots behind the existing RPC API,
   retaining a complete host fallback.
3. Move bounded inode/device/port metadata after deterministic capacity,
   transaction, and owner-death semantics exist.
4. Add generation-tagged request/response sequence slots for selected
   non-scheduler operations. Keep process-local futures which wait on shared
   futex words, and make tombstone/cancellation epochs part of every completion.
   Do not replace scheduler `Ivar`s until equivalent teardown and wakeup
   linearization has been specified and model-checked.
5. Compile only pure synchronous fast-path methods into the RX pod image.
6. Reconsider scheduler storage only after measurement. Moving run-queue
   authority and complete `ThreadState` is a separate project, not phase-one
   cleanup.

## Failure and isolation boundary

The v2 security model trusts every writable participant. Mapping scheduler RW
pages into a DBI/LiteInst/SaBRe guest address space therefore lets arbitrary
guest code corrupt Detcore. Isolation-preserving deployments should map RW
state only into trusted injected/runtime components, or keep mutation in the
coordinator and expose a narrower call gate. Direct guest mappings are a
cooperative-mode feature.

Fork duplicates uncounted capabilities; exec needs fresh authentication and
attachment. Detcore's clone/vfork/exec model therefore needs backend-controlled
quiescence and generation reattachment rather than relying only on libc
`pthread_atfork`.

The initial owner-death policy should abort the Hermit run, fence and reap every
participant with supervisor-owned pidfds, discard the complete mapping
generation, and restart. In-run recovery would need journaling and type-specific
repair. A timeout must never steal a mutex or allocator lock.

## Effort and expected payoff

| Scope | Estimate | Risk |
| --- | --- | --- |
| Backend attachment, schema handshake, and scalar clock/counter fast path | 3-5 engineer-months | Medium-high |
| Shared bounded tables and scheduler mailboxes | Additional 4-8 months | High |
| Whole global/thread-state and scheduler conversion | 12-24 months | Very high |

The 3-5 month first stage includes Detcore work, backend lifecycle integration,
fallback/rollback, and correctness/performance evaluation; it is not remaining
work in the shmem-pod crate alone. A focused prototype behind the existing RPC
surface could be demonstrated in roughly 4-8 weeks, but would not yet be a
production Detcore state model.

The credible win is removing bincode plus Unix-socket request/response overhead
for LiteInst, SaBRe, and the newer generic e9patch coordinator path, and making
clock/counter operations lock-free. DBI socket-coordinator mode may benefit as
well; in-process DBI, ptrace, and current KVM already call the global tool
directly, so shared mapping alone offers little there. Scheduler grants remain
serialized and often require a wakeup. Large end-to-end gains must be measured
per backend rather than inferred from atomic-counter microbenchmarks.
