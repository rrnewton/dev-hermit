# Detcore deterministic scheduler resource model

Date: 2026-07-27

Source examined: `rrnewton/hermit` commit
`5f1f3f92e9587f7e7ad33ae829cf32cd2019916a`.

Scope: `detcore/src/` was read as the primary implementation, with supporting
identity and time types in `detcore-model/src/`. This is a source analysis, not
a performance experiment.

## Executive summary

Detcore does not currently use a general resource-locking scheduler. Its main
determinism mechanism is stronger and simpler: wait until every guest task is
parked, choose one runnable task deterministically, and release exactly one
guest turn. The declared `ResourceID`/`Permission` API is mostly a vocabulary
for a future dependency scheduler. Today:

- resource-release RPCs only log;
- the scheduler's resource-to-action table and background action pool are
  unused;
- requests containing more than one resource panic;
- most data resources (`FileContents`, `Path`, `MemAddrSpace`, and so on) are
  granted immediately;
- a small set of resource-like control tokens actually changes scheduling:
  sleeps, external blocking I/O, `vfork`, priority change points, and polling;
- futex waiters, timed events, external-I/O waiters, process topology, FD
  aliases, and mapping aliases are maintained in separate subsystem state,
  not in one unified resource graph.

The default scheduling policy is deterministic priority order followed by
round robin within a priority. Poll retries receive deterministic backoff.
Seeded random and sticky-random policies are chaos alternatives. Fork order,
futex wake selection, signal targeting, timers, and external-I/O re-entry add
special rules around that base queue.

Detcore has useful identity foundations for future process parallelism:
`MmId`, `FilesId`, `FdSlot`, `OpenFileId`, `SharedMemoryObjectId`, `FutexID`,
`DetPid`/`DetTid`, and a process/thread tree. They are not yet connected into a
complete conflict model. The safest expansion is not arbitrary concurrent
turns. It is conservative *serialization domains*: form connected components
from every known alias and causal edge, allow concurrency only between proven
disjoint components, and collapse unknown or unmodeled effects into a global
domain. Shared-memory components must remain serialized until Detcore gains
memory-access tracking or another sound isolation mechanism.

## 1. What the scheduler actually does

### 1.1 One turn

The operative path is `sched_loop` -> `do_a_turn_blocking` in
`detcore/src/scheduler.rs:601-780`:

1. Wait for **full quiescence**, meaning all guest tasks have parked and sent
   their next scheduler request. The source calls this an overapproximation and
   says a future implementation should wait only for dependent actions
   (`scheduler.rs:708-732`, `scheduler.rs:2043-2059`).
2. Process scheduler-owned blockers: `vfork` barriers, due timed events,
   completed external I/O, and the no-runnable-task case.
3. Peek a task from the deterministic run queue, with a `vfork` child able to
   override the ordinary choice.
4. Wait for the selected task's request if necessary.
5. Interpret its zero or one resource/control token.
6. Unblock that one guest and commit the turn.
7. Normally put it at the back of its priority band. `sched_yield` excludes it
   from the next productive turn.

This is global turn serialization, not resource-level parallel execution. A
guest can execute user instructions between interception/preemption points,
but Detcore admits only one such guest turn at a time.

### 1.2 Run-queue order

`detcore/src/scheduler/runqueue.rs` implements the ordinary choice:

- A queue key is `(priority, insertion turn)`, stored in a `BTreeMap`, so lower
  numerical priority wins and insertion order breaks ties.
- The default priority is 1000. Ordinary tasks are pushed to the back after a
  turn, producing FIFO/round-robin service within a priority band.
- Internal I/O pollers are progressively deprioritized with exponential
  backoff. Periodic deterministic upgrades prevent starvation.
- `sched_yield` excludes the yielding task for one committed guest turn rather
  than permanently changing its priority.
- `SchedHeuristic::None` is the default. `ConnectBind`, `Random`, and
  `StickyRandom` modify selection for targeted chaos exploration. Their random
  choices use a seeded PRNG and are reproducible for the same configuration.
- Child-first versus parent-first post-fork placement is an explicit policy.
  A `vfork` barrier forces the child because the parent cannot continue until
  the child exits or execs.

The scheduler also records/replays preemption histories when configured.
Timeslice endpoints are expressed in logical time and ultimately use
Reverie's deterministic-preemption mechanism.

### 1.3 Blocked work and wake ordering

Blocked tasks are absent from the run queue and live in specialized pools:

- **Futex waiters** are keyed by `FutexID`. Normal wake selection follows the
  stored waiter order; chaos mode may make a seeded alternative choice.
- **Timed waiters and timers** are ordered by logical deadline, with ordered
  event identities providing deterministic tie-breaking.
- **External blocking I/O** is allowed to run in the background and rejoin
  later. Readiness is explicitly described as a nondeterministic snapshot in
  `scheduler.rs:1533-1564`. Record/replay modes and deterministic eager
  harvesting reduce the observable variation, but the source still relies on
  an independence-or-recording assumption.
- **Signals** have explicit task- and process-directed selection. Eligible
  process targets are sorted in normal mode; chaos mode can choose a seeded
  target. An inbound signal is turned into a scheduler request before handler
  execution.
- **No runnable task** advances global logical time to the next timed event. A
  system containing only futex waiters is diagnosed as deadlocked.

## 2. The four meanings of "tracked resource"

The implementation uses the word "resource" for distinct things. Treating
them as one complete model gives a misleading answer.

### 2.1 Declared `ResourceID` vocabulary

`detcore/src/resources.rs:152-295` declares read, write, and read/write
permissions for the following IDs:

| `ResourceID` | Intended meaning | Operational status today |
| --- | --- | --- |
| `FileContents(DetInode)` | File bytes | Requested by mediated file I/O; immediately granted |
| `FileMetadata(DetInode)` | Inode metadata | Declared; no scheduler exclusion |
| `DirectoryContents(DetInode)` | Directory entries | Declared; no scheduler exclusion |
| `MemAddrSpace(DetPid)` | A process address space | New-task token; keyed too coarsely and immediately granted |
| `Path(PathBuf)` | One namespace path | Used around some opens; raw path identity and immediately granted |
| `PathsTransitive(PathBuf)` | Path subtree | Marked unstable; no scheduler exclusion |
| `Device` | Container stdin/stdout/stderr | Declared, but stdio currently tends to use `/proc/<pid>/fd/N` paths |
| `Exit { group, process, mm }` | Exit/exit-group permission and clear-child-tid identity | Scheduler control token, not a held lock |
| `ParentContinue` | Parent/child post-clone ordering | Scheduler control token |
| `SleepUntil` | Timed suspension | Fully interpreted by the scheduler |
| `InternalIOPolling` | Nonblocking retry turn | Fully interpreted by polling/backoff machinery |
| `TraceReplay` | Internal trace-replay turn | Scheduler control token |
| `FutexWait` | Remove a futex waiter from run queue | Control token; the actual key is held in the futex pool |
| `BlockingExternalIO` | Begin an external blocking operation | Backgrounds/deschedules the task |
| `BlockingVfork` | Begin a kernel-blocked `vfork` | Installs a parent barrier |
| `BlockedExternalContinue` | Rejoin after external operation | Re-entry control token |
| `PriorityChangePoint` | Chaos priority change | Scheduler control token |
| `InboundSignal` | Deliver a physical signal next | Scheduler control token |
| `SchedYield` | Yield one scheduler opportunity | Queue-control token |

`Resources` can syntactically hold a set of IDs and union permissions. The
scheduler, however, accepts zero or one ID and panics on a larger set
(`scheduler.rs:1801-1837`). This prevents atomic modeling of naturally
multi-resource operations such as rename, descriptor transfer, or
input-to-output copies.

The strongest evidence that this is not a live lock manager is:

- `Scheduler::resources: HashMap<ResourceID, ActionID>` is dead code
  (`scheduler.rs:283-286`);
- `Scheduler::bg_action_pool` is dead code (`scheduler.rs:264-266`);
- release-one and release-all handlers are TODO log statements
  (`tool_global.rs:781-788`);
- every data-oriented `ResourceID` match arm returns `Ok(())`
  (`scheduler.rs:1941-1956`).

### 2.2 Concrete task and alias identities

These types do real work even though most are not scheduler locks:

- **Task identity:** `DetPid` and `DetTid` are currently the same integer
  wrapper (`detcore-model/src/pid.rs`). The scheduler has a raw parent/child
  `ThreadTree`, thread-group leader membership, and TID-to-TGID mapping.
- **Ancestry:** `Pedigree` describes a deterministic fork-tree path and can be
  converted to a virtual PID, but its own documentation says it does not encode
  joins, dependencies, or happens-before edges
  (`detcore-model/src/pedigree.rs:47-56`).
- **Address-space identity:** `MmId { creator, generation }` is shared under
  `CLONE_VM`, fresh for a copied address space, and renewed on exec.
- **Shared mapping identity:** `SharedMemoryObjectId` distinguishes anonymous
  allocations, file `(device, inode)` objects, and an `OpenFileId` fallback.
  `MemoryMetadata` tracks intervals only for resolving process-shared futex
  keys across aliases; it is not a memory dependency tracker.
- **Futex identity:** private futexes use `(MmId, virtual address)`; shared
  futexes use `(SharedMemoryObjectId, byte offset)`. This is a materially better
  alias key than `ResourceID::MemAddrSpace(DetPid)`.
- **Descriptor-table identity:** `FilesId` identifies a Linux `files_struct`;
  `FdSlot` identifies one numeric slot within it.
- **Open-file identity:** `OpenFileId` identifies a Linux open file description
  (OFD), surviving `dup` and fork aliases through shared `Arc` state.

Thread creation preserves important Linux sharing relationships in
`ThreadState`: `CLONE_VM` shares `MmId` and mapping metadata; `CLONE_FILES`
shares descriptor metadata; otherwise a fork copies the descriptor table but
preserves aliased OFDs. POSIX timers and CPU accounting have their own
thread-group/process sharing rules. This is useful substrate, but it is not a
complete model of all `clone(2)` sharing flags.

### 2.3 Modeled subsystem state

Detcore maintains additional guest-visible state outside `ResourceID`:

- descriptor maps and per-OFD flags, type, path/inode metadata, procfs cursor,
  and selected socket metadata;
- FD type tags for regular files, pipes, sockets, epoll, eventfd, timerfd,
  signalfd, inotify, memfd, pidfd, userfaultfd, and RNG devices;
- futex waiter queues and clear-child-tid bookkeeping;
- timed events, alarms, POSIX timers, logical sleeps, and per-task virtual time;
- thread groups, ancestry, lifecycle, `vfork`, and wait/exit state;
- deterministic port allocation and OFD-to-port association;
- deterministic inode identities and logical metadata timestamps;
- signal delivery scheduling and some disposition/mask behavior;
- deterministic PRNG state, CPUID/RDTSC handling, procfs snapshots, and
  record/replay event streams.

Several of these are virtualization state, not conflict resources. For
example, `GlobalTime` stores each task's accumulated progress and a cached sum
plus scheduler time. It is called a vector clock in comments, but it does not
encode a vector-clock partial order or causal joins; the exposed global time is
the sum of work, modeling one-core cumulative progress.

### 2.4 Kernel state used through serialization, polling, or record/replay

Many operations are still performed by Linux. Detcore makes them repeatable by
serializing guest turns, converting potentially blocking calls to nonblocking
poll loops, virtualizing their outputs, or recording/replaying results. That
does not mean Detcore has an explicit model of the corresponding kernel
object. Pipe buffers, socket receive queues, epoll interest/readiness state,
and file offsets are important examples.

The repository explicitly excludes a changing external filesystem and external
network from its determinism guarantee. Several unmodeled, host-global
interfaces are rejected with stable errors rather than modeled: `io_uring`,
native AIO, POSIX message queues, System V IPC, namespace/mount mutation,
kernel keyrings, cross-process memory operations, and other host-observation
interfaces (`detcore/src/syscall_classification.rs`). Refusal is a valid
deterministic policy boundary, but it is not resource modeling.

## 3. Resource coverage by category

### 3.1 File descriptors and open files

**Tracked well enough for current serialization:** numeric FD slots, descriptor
table identity, `dup`/fork OFD aliasing, close-on-exec, status flags, coarse FD
type, path/stat metadata, selected socket identity, deterministic port
allocation, and optional file-content resource attachment.

**Not explicitly modeled as conflicts:**

- shared OFD file cursor and status mutations as an `OpenFileId` resource;
- pipe buffer capacity, byte sequence, read/write endpoints, and writer count;
- socket endpoint/connection identity, packet/byte queues, accept backlog,
  shutdown state, ancillary data, and peer relationships;
- `SCM_RIGHTS` transfer, which creates aliases across descriptor tables;
- epoll interest graph and edge/level-triggered readiness;
- eventfd counter, timerfd expirations, signalfd queue, inotify watch/event
  queues, userfaultfd events, and pidfd target lifecycle;
- POSIX record locks, OFD locks, leases, and meaningful `flock` contention.

The FD type enum proves Detcore knows *what kind* of object an FD names. It does
not prove the object's state participates in scheduler dependency analysis.
Current global turn serialization makes many of these races unobservable. They
become required resource keys before independent processes can run together.

### 3.2 Filesystem and namespace

**Tracked:** inode-like identities, logical metadata time, paths for selected
operations, procfs normalization/snapshots, and a fixed mount namespace policy.

**Gaps:**

- `DetInode` is presently an alias of a raw inode number. An inode alone is not
  globally unique without device/mount/generation context.
- Raw `PathBuf` keys do not canonicalize relative paths against per-process
  cwd/root, symlinks, bind mounts, hard links, mount namespaces, or rename.
- Directory-entry identity should be `(filesystem namespace, parent object,
  basename)`, distinct from the target inode and directory's contents version.
- Namespace-changing operations need multiple atomic resources. Rename, for
  example, touches source entry, destination entry, one or two parent
  directories, target link counts/metadata, and possibly mount constraints.
- The current source constructs only a subset of the declared
  `FileMetadata`, `DirectoryContents`, `PathsTransitive`, and `Device` IDs.
- External processes mutating the same filesystem invalidate any in-container
  lock model unless the filesystem is snapshotted or such mutations are in the
  recorded input boundary.

### 3.3 Memory regions and futexes

**Tracked:** address-space identity across clone/exec, shared-mapping intervals,
backing objects and offsets, private/shared futex identities, futex waiters,
and clear-child-tid interactions.

**Gaps:**

- `MemoryMetadata` tracks mappings for futex-key resolution, not reads, writes,
  dirty pages, protection changes, or overlapping byte/range conflicts.
- `ResourceID::MemAddrSpace` uses `DetPid`, not `MmId`, and does not represent
  `CLONE_VM` or `MAP_SHARED` entanglement correctly.
- Ordinary user-space loads/stores do not cross a syscall hook. Concurrent
  turns in address spaces sharing pages would introduce data races that a
  syscall-only lock manager cannot see.
- File-backed `MAP_SHARED` connects memory and file-content domains; anonymous
  shared mappings connect otherwise separate processes. Those edges must be
  transitive.
- `mprotect`, `mremap`, `munmap`, fork copy-on-write, and exec change the alias
  graph and need atomic topology updates.
- Robust futex lists and `rseq` create additional lifecycle/scheduler coupling;
  strict mode currently refuses some unmodeled forms rather than modeling them.

### 3.4 Processes, PIDs, and lifecycle

**Tracked:** raw task IDs, thread groups, parent/child tree, clone ordering,
`vfork` barriers, exit/exit-group, waits, child-tid clearing, process-directed
signal target selection, and some pidfd creation state.

**Gaps:**

- `DetPid`/`DetTid` still commonly wrap physical IDs; deterministic identity is
  not uniformly virtualized.
- An ancestry tree is not a process dependency DAG. It lacks join/wait edges,
  signal edges, inherited-object edges, descriptor passing, shared mappings,
  and other happens-before relationships.
- Process groups, sessions, reparenting/subreapers, zombie/reaping state, PID
  namespaces, and pidfd target operations need stable identities and atomic
  transitions.
- Cross-process memory and several pidfd operations are refused today. They
  would require explicit target-address-space and lifecycle dependencies.
- `fork`, `clone`, `exec`, and `exit` alter many resource graphs at once and
  cannot be represented by the current single-resource request.

### 3.5 Process-global sharing not represented uniformly

Linux clone flags can share more than memory and FDs. A process-parallel model
needs stable identities and alias edges for at least:

- filesystem context (`fs_struct`: cwd, root, umask; `CLONE_FS`);
- signal dispositions (`sighand_struct`; `CLONE_SIGHAND`) and per-task masks;
- pending thread and process signal queues;
- credentials/capabilities and security state, even if the configured policy
  virtualizes most changes;
- namespaces (mount, PID, user, network, IPC, UTS, cgroup, time);
- System V semaphore undo state (`CLONE_SYSVSEM`) if that IPC family is ever
  enabled;
- resource limits, process-group/session identity, controlling terminal, and
  selected `prctl` state.

The current fixed/refused policies can remain a deliberate simplification, but
the policy must be encoded in the dependency contract so new syscall support
cannot silently bypass it.

### 3.6 Signals, time, and randomness

**Tracked/virtualized:** deterministic local progress, one-core global time,
ordered timers, sleeps, alarms, POSIX timers, signal targeting at delivery,
seeded PRNGs, CPUID/RDTSC, and recorded preemptions.

**Gaps under parallel execution:**

- complete pending-signal queue state and ordering must be a process/thread
  resource, including coalescing versus realtime queue semantics;
- signal disposition, mask changes, and delivery must have atomic relationships
  with thread creation, exec, exit, waits, and blocking syscall interruption;
- the current global clock is updated safely because turns are serialized. A
  parallel implementation needs a deterministic commit-time frontier rather
  than exposing completion wall time;
- timer expiration order relative to concurrently completed work must be based
  on logical commit order, never host completion order;
- external-I/O completion and inbound physical signals remain nondeterministic
  inputs that must be recorded or deterministically fenced.

## 4. Why current serialization works, and where it still leaks

Full quiescence plus one admitted guest turn makes almost every in-container
kernel operation appear in a deterministic total order. It also means the
current no-op data-resource arms are not immediately unsafe: global turn order
already provides exclusion. Blocking-via-polling keeps a task from holding the
only virtual CPU while an internal peer must make progress.

The main exceptions are operations intentionally allowed outside that total
order:

- external blocking calls backgrounded by `BlockingExternalIO`;
- physical signal arrival;
- state changed by actors outside the container;
- host properties that have not been virtualized or refused;
- any execution mode that disables thread sequentialization.

The external-I/O code acknowledges this directly: background execution races
with later turns and assumes non-interference, or interference only in results
that record/replay captures (`scheduler.rs:1893-1925`). This is a boundary to
tighten before increasing parallelism, not a precedent that makes arbitrary
background syscalls safe.

## 5. A sound model for process-level parallelism

### 5.1 Safety target

Permit two guest actions to overlap only if all of the following hold:

1. Their complete resource closures are known before execution.
2. The closures have no read/write conflict and no topology mutation that can
   create an alias during execution.
3. Neither action can affect the other's blocking/wakeup outcome, signal state,
   lifecycle, time observation, or externally recorded result.
4. Results become guest-visible in a deterministic commit order independent of
   host completion order.
5. Any unknown syscall or identity falls back to the global serialization
   domain.

Because Linux syscalls generally cannot be rolled back, optimistic execution
with post-hoc conflict detection is unsafe for arbitrary operations. Start
with conservative predeclared effects and a serial fallback.

### 5.2 Serialization domains

Build an alias graph whose nodes are tasks and resource identities. An edge
means two nodes can observe or mutate shared state. Connected components are
serialization domains. Known edges include:

- tasks sharing an `MmId`;
- address spaces mapping the same `SharedMemoryObjectId` range;
- tasks sharing a `FilesId`, `OpenFileId`, pipe, socket, epoll graph, terminal,
  or transferred FD;
- tasks sharing filesystem context, signal disposition, namespace, or process
  signal queue;
- parent/child wait, `vfork`, pidfd, signal, and clear-child-tid relationships;
- a file-backed mapping joined to its filesystem object;
- stdout/stderr and other container-global devices;
- external or insufficiently identified resources joined to a global node.

This gives an important conservative option: if Detcore cannot observe ordinary
memory accesses, all tasks connected by any shared-writable mapping remain in
one serialized domain. It can still parallelize truly disjoint process groups
without claiming deterministic execution of shared-memory races.

### 5.3 Resource key expansion

Replace coarse IDs with canonical, versionable identities:

- `MemoryPrivate(MmId, range)` and
  `MemoryShared(SharedMemoryObjectId, offset_range)`;
- `FdSlot(FilesId, fd)` and `OpenFile(OpenFileId, aspect)` where aspects include
  cursor, status, contents endpoint, and readiness;
- `Pipe(PipeId, aspect)`, `Socket(SocketId, aspect)`, and explicit epoll/eventfd/
  timerfd/signalfd/inotify state;
- `FsObject(FsId)` for cwd/root/umask, `Inode(FsObjectId, aspect)`, and
  `DirEntry(FsNamespaceId, parent, name)`;
- `TaskLifecycle(DetTid)`, `ProcessLifecycle(DetPid)`, `ThreadGroup(DetPid)`,
  `WaitRelation(parent, child)`, and `PidFd(OpenFileId, target)`;
- `SignalDisposition(SighandId)`, `SignalQueue(ProcessOrTaskId)`, and
  `SignalMask(DetTid)`;
- namespace, credential, process-group/session, terminal, timer, and virtual
  clock identities;
- a `GlobalKernel`/`ExternalWorld` fallback for unknown effects.

Ranges and aspects matter. Treating an entire inode or address space as one
resource is sound but leaves substantial safe parallelism unused. Begin coarse
and refine only after tests prove alias canonicalization.

### 5.4 Multi-resource acquisition

Every action should declare a sorted effect set with read/write mode before it
is admitted. Acquire all keys atomically in canonical order, or enqueue the
whole request without holding a partial subset. This avoids deadlock and
supports operations such as:

- `rename`: two directory entries, parent directories, and inode metadata;
- `sendfile`: input OFD/cursor and contents plus output OFD/cursor/contents;
- `fork`: parent state plus creation of child identities and inherited edges;
- `exec`: lifecycle, address space, descriptor table, signal state, and image;
- descriptor passing: sender socket queue, receiver table slot, and shared OFD;
- futex wake: futex key, waiter queue, and selected task run-state.

The existing `Resources` set and `Permission` enum are a reasonable API seed,
but `HashMap` iteration must never determine lock or commit order. Canonical
resource ordering and explicit version/generation checks are required.

### 5.5 Scheduler changes

The scheduler would need to evolve from a single turn into a deterministic
frontier:

1. Park tasks with complete effect sets.
2. Order candidates by the existing deterministic priority/round-robin key.
3. Admit the earliest maximal set whose resources do not conflict.
4. Execute admitted actions in a real background action pool.
5. Record completion privately. Host completion order must not choose the next
   visible event.
6. Commit in deterministic scheduler order once predecessors and dependencies
   are complete; only then expose return values, wakeups, signals, or timers.
7. Recompute alias components atomically after topology-changing commits.

The scheduler's currently unused `Action`, `ActionID`, `bg_action_pool`, and
resource table point in this direction, but they need reader/writer ownership,
multi-resource wait queues, completion state, cancellation, and deterministic
commit sequencing.

Logical time also needs a frontier. Per-task progress can continue to accrue,
but timers and global time observations should advance from committed work,
not whichever host worker finishes first. A conservative global virtual time
is the minimum safe committed frontier plus explicitly ordered scheduler time.

### 5.6 Syscall effect descriptors

Put effect calculation next to each syscall handler and make coverage
auditable. A descriptor should include:

- canonical read/write keys;
- alias/topology edges created or removed on success;
- possible blockers and exact wake targets;
- whether the call reads external state;
- whether output must be recorded;
- the fallback domain when arguments or kernel identity cannot be resolved.

An automated strict-mode assertion should reject parallel admission for any
intercepted syscall without a complete descriptor. Empty effect sets should be
reserved for proven pure virtualization, not used as an implicit default.

## 6. Recommended rollout

### Phase 0: make the current contract accurate

- Rename or document resource requests as turn/control requests until locks are
  real.
- Add diagnostics that inventory constructed `ResourceID`s and flag all
  multi-object syscalls still represented by one or zero IDs.
- Replace raw-path and raw-inode identity with canonical scoped identities.
- Add tests proving alias equivalence for `dup`, fork, `CLONE_FILES`,
  `CLONE_VM`, `MAP_SHARED`, hard links, rename, and descriptor passing.
- Keep all execution serialized; this phase should not change behavior.

### Phase 1: parallelize only disconnected process domains

- Build coarse alias components at fork/clone/mmap/FD-transfer boundaries.
- Merge any component with shared-writable memory, shared OFDs, process causal
  edges, container devices, or unknown external state.
- Admit at most one task from each component concurrently.
- Limit initial eligible actions to an allowlist with no host-side mutation or
  rollback requirement. Everything else takes `GlobalKernel` exclusively.
- Validate output, schedule, and virtual time against the serial scheduler.

This phase can produce real process-level parallelism for independent worker
processes while remaining conservative about shared state.

### Phase 2: enable disjoint modeled syscalls

- Implement atomic reader/writer acquisition and deterministic commit.
- Model canonical filesystem entries/inodes and OFD state.
- Model pipe/socket/event objects sufficiently to identify internal wakeups.
- Allow known disjoint syscalls from separate domains to overlap.
- Treat external I/O as an explicit recorded input fence.

### Phase 3: refine sharing domains

- Split coarse objects into ranges/aspects where valuable.
- Maintain dynamic alias edges for mappings, descriptor passing, exec, and
  namespace changes.
- Consider page protection, DBI instrumentation, hardware watch mechanisms, or
  compiler support only if concurrent shared-memory execution is a goal.
  Without one of these, preserve serialization for shared-writable mappings.

## 7. Required validation

A parallel scheduler needs tests that compare it with the serial reference,
not only repeated parallel runs:

- deterministic trace and bitwise output equivalence over many seeds;
- adversarial alias tests for every identity type;
- two independent CPU-bound child processes to demonstrate actual overlap;
- negative tests showing no overlap for shared memory, shared OFDs, pipe/socket
  peers, parent/wait child, signal relations, and shared stdout;
- deterministic timer and signal order despite reversed host completion order;
- external-I/O record/replay with intentionally varied readiness order;
- topology mutation during fork/exec/exit/mmap/munmap/dup/close/SCM_RIGHTS;
- fallback tests proving unknown syscalls acquire the global domain;
- deadlock, starvation, cancellation, and process-exit cleanup tests;
- performance counters for admitted width, conflict rate, component size, and
  time spent behind global fallback.

## 8. Bottom line

Detcore currently has a deterministic **turn scheduler** plus several concrete
subsystem models, not a deterministic **resource scheduler**. The single-turn
design is why incomplete data-resource handling remains workable. Turning on
parallel guest execution before building alias closure, multi-resource atomic
acquisition, and deterministic result publication would remove the property
that currently supplies most of Detcore's determinism.

The practical path is coarse and conservative: preserve serial execution
inside each connected resource/causality domain, run only proven-disjoint
domains together, and progressively split domains as explicit models become
complete. That design can exploit process-level parallelism while retaining a
clear fail-closed answer for every unmodeled Linux resource.

## Source index

- `detcore/src/resources.rs`: resource vocabulary, permissions, and polling
  request metadata.
- `detcore/src/scheduler.rs`: scheduler state, full-quiescence turn loop,
  blocker pools, futex/signal/lifecycle ordering, and current resource handling.
- `detcore/src/scheduler/runqueue.rs`: priority, round-robin, polling backoff,
  yield, and seeded heuristic selection.
- `detcore/src/tool_global.rs`: global scheduler RPCs, global time, inode/port
  state, thread registration, and no-op resource releases.
- `detcore/src/tool_local.rs`: per-task descriptor metadata and record/replay
  boundary.
- `detcore/src/fd.rs`: descriptor/OFD representation and FD type tags.
- `detcore/src/memory.rs`: shared mapping intervals used for futex identity.
- `detcore/src/syscalls/`: the concrete resource requests, polling paths, and
  syscall-specific virtualized/recorded behavior.
- `detcore/src/syscall_classification.rs`: deterministic, native, and refused
  syscall policy boundaries.
- `detcore-model/src/fd.rs`: `FilesId`, `FdSlot`, and `OpenFileId`.
- `detcore-model/src/futex.rs`: `MmId`, shared-memory identities, and `FutexID`.
- `detcore-model/src/pid.rs` and `pedigree.rs`: task IDs and ancestry identity.
- `detcore-model/src/time.rs`: per-task progress and summed global logical time.
