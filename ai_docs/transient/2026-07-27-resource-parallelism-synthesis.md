# Linux resource causation and Detcore process parallelism

Date: 2026-07-27

Inputs:

- `ai_docs/transient/2026-07-27-detcore-resource-model.md`, based on
  `rrnewton/hermit` commit `5f1f3f92e9587f7e7ad33ae829cf32cd2019916a`.
- `ai_docs/transient/2026-07-27-linux-process-causation.md`, a Linux/POSIX
  ground-truth survey of inter-process causal channels.

This document synthesizes those reports into one implementation roadmap. It
does not report a performance experiment and does not claim that process
parallelism exists today.

## Executive decision

Detcore is currently a deterministic **turn scheduler**, not a resource
scheduler. Its main safety property is global quiescence followed by release of
one guest turn. The declared `ResourceID` API does not currently provide
reader/writer exclusion: data-resource requests are granted immediately,
release RPCs only log, multi-resource requests panic, and the background action
table is unused.

Linux, meanwhile, lets processes affect each other through far more than
obvious IPC. Causation includes shared memory, inherited and transferred file
descriptors, filesystem names and metadata, signals and lifecycle, namespace
views, kernel identifier allocation, procfs/sysfs observation, clocks,
resource exhaustion, and external input. Some channels carry data, some only
change blocking or wakeup behavior, some change allocation or failure results,
and some are observable only through time.

The practical route to deterministic process parallelism is therefore:

1. Define a fail-closed effect contract for every admitted action.
2. Canonicalize object identity and maintain the complete alias/topology graph.
3. Start with coarse serialization domains and parallelize only proven-disjoint
   domains.
4. Add atomic multi-resource reader/writer acquisition.
5. Execute concurrently but publish results, wakeups, signals, and logical time
   in deterministic commit order.
6. Treat unknown effects and unresolved external state as an exclusive
   `GlobalKernel` or `ExternalWorld` resource.
7. Keep all components with shared-writable memory serialized until ordinary
   loads and stores can be tracked soundly.

Guaranteed process independence is a sufficient condition for overlap, not the
only eventual condition. A later dependency-aware scheduler can overlap actions
from related processes when their current effect sets commute. For example, a
parent/child wait relationship constrains exit and wait commits, but need not
forever serialize unrelated computation. The first implementation should still
use coarse connected components because it is easier to audit and fail closed.

## 1. Causation and the safety contract

### 1.1 What counts as causation

Process A causally affects process B when an action by A can change any later
architectural observation by B. The observation can be:

- returned bytes, metadata, identifiers, or credentials;
- success, failure, blocking, wakeup, or signal delivery;
- process existence, exit status, ancestry, or descriptor availability;
- resource exhaustion, throttling, eviction, or forced termination;
- elapsed time or event order, when a non-virtualized timing source is visible.

Three classes must be kept distinct:

1. **Architectural content and synchronization.** Shared bytes, queues,
   directory entries, locks, signals, and wait relations directly affect guest
   values or control flow.
2. **Allocation, metadata, and fate.** PID/port/inode allocation, quota,
   cgroup limits, and OOM selection affect identifiers, errors, or survival
   without carrying application-selected bytes.
3. **Timing and microarchitecture.** CPU contention, page-cache residency,
   KSM, cache/TLB state, and host completion order matter only if a guest can
   observe real time or if they cross into an architectural failure.

Hermit already neutralizes much of class 3 by virtualizing clocks, counters,
randomness, and scheduling. That does not neutralize OOM, `ENOSPC`, quota,
`pids.max`, external filesystem mutation, or host completion order that is
allowed to choose a guest-visible result. Those remain architectural channels.

### 1.2 Independence versus safe concurrent execution

Two processes are **guaranteed independent** when no action of either can
change an observation by the other. Independence permits arbitrary overlap and
arbitrary commit order.

Two actions are **safe to execute concurrently** when their complete effect
closures are known, their read/write sets do not conflict, neither can create
an alias during execution, and their results are published in a deterministic
order. The processes may still have a causal relationship outside those two
actions.

This distinction leads to two milestones:

- **Domain parallelism:** one active task per proven-disjoint serialization
  domain. This requires no claim that related processes commute.
- **Action parallelism:** multiple actions may overlap when canonical effect
  sets commute, even if their processes belong to a larger causal graph.

### 1.3 Required invariant

An action may run outside the global serial lane only if all of these hold:

1. Its resource closure is complete before execution.
2. All identities are canonical and generation-safe.
3. Its effect set is acquired atomically, with explicit read/write mode.
4. No unresolved topology mutation can add an alias during execution.
5. Blocking and wakeup targets are modeled.
6. External inputs are fenced or recorded.
7. Completion is private until deterministic commit.
8. Unknown behavior falls back to exclusive global serialization.

Linux syscalls generally cannot be rolled back. Optimistic execution followed
by post-hoc conflict detection is therefore not a safe default.

## 2. Complete Linux inter-process resource taxonomy

The table below is organized by the shared object or ambient scope that carries
causation. "Partition condition" states what would make the resource safe to
place in separate execution domains.

| Resource family | Causal state or effect | Canonical identity needed | Partition condition |
| --- | --- | --- | --- |
| Private address space | Process-private mappings, protections, COW state | `MmId`, mapping generation, virtual range | Different `MmId`s and no common shared backing object |
| Shared memory | `MAP_SHARED`, shared anonymous pages, POSIX/SysV shm | backing object plus byte/page range | Disjoint backing objects/ranges, or serialize the whole shared-writable component |
| Futexes and robust lists | waiter queue, futex word, owner death, clear-child-tid | private `(MmId, addr)` or shared `(object, offset)` | Distinct futex keys and no untracked shared-memory access |
| FD table slots | lowest-free allocation, close/dup/exec changes | `(FilesId, fd)` | Different `FilesId`s, or disjoint slots with no topology mutation |
| Open file descriptions | shared offset, flags, lock ownership, object reference | `OpenFileId` plus aspect | Different OFDs, or read-only aspects proven independent |
| Pipes and FIFOs | byte buffer, capacity, readers/writers, EOF/wakeup | `PipeId` plus endpoint/queue aspect | Different pipe objects |
| UNIX sockets | stream/datagram queues, peer state, credentials, fd transfer | `SocketId`, connection/peer, queue | Different socket graphs; no `SCM_RIGHTS` edge |
| INET sockets | queues, ports, backlog, connection and shutdown state | network namespace, endpoint/connection ID | Different network namespaces/endpoints and isolated external input |
| epoll and pollable FDs | interest graph, readiness, edge/level state | epoll object plus watched object/aspect | Disjoint interest graphs and watched objects |
| eventfd/timerfd/signalfd | counter, expirations, pending event/signal queue | kernel object/OFD ID | Different objects; timer commits use logical order |
| inotify/fanotify | watches and filesystem event queues | watch object plus canonical FS object | Disjoint watches and mutation sets |
| userfaultfd/pidfd | event queue or target lifecycle/FD access | object plus target task/process | Different targets; target operations serialized with lifecycle |
| SysV/POSIX IPC | shm, msg queues, semaphores, mqueues | IPC namespace plus object generation/key | Different IPC namespaces/objects |
| Cross-process control | ptrace, `process_vm_*`, `pidfd_getfd` | source capability plus target `MmId`/process | Refuse, isolate permissions, or serialize against complete target state |
| Filesystem data | file bytes, page-cache-backed content | filesystem/mount object plus inode generation and byte range | Immutable shared reads or disjoint writable objects/ranges |
| Directory namespace | names, links, rename, create/unlink, mount topology | FS namespace, parent object, basename | Disjoint directory-entry sets and no shared writable ancestor mutation |
| Filesystem metadata | size, times, mode, links, xattrs, quota | scoped inode/object plus aspect | Disjoint objects/aspects, or read/read only |
| File locks and leases | POSIX locks, OFD locks, `flock`, leases | inode/OFD plus range and lock class | Disjoint lock domains/ranges |
| Filesystem capacity | free blocks/inodes, quota, writeback | filesystem and quota domain | Separate filesystems/quotas with enforced capacity |
| Process/task lifecycle | fork/clone/exec/exit, zombie/reap, wait, reparent | stable task/process/thread-group generation | No active lifecycle edge, or serialize only lifecycle effects |
| Signal state | dispositions, masks, process/thread pending queues, targeting | `SighandId`, task mask, queue, target generation | Separate state/targets; delivery committed deterministically |
| Sessions, process groups, TTY | foreground group, hangup, job-control broadcast | session, process group, terminal object | Different sessions/TTYs, or serialize terminal control |
| Credentials/security | uid/gid, capabilities, namespaces, LSM-visible state | credential/security object generation | Immutable/private credentials or modeled shared changes |
| Filesystem context | cwd, root, umask, `CLONE_FS` | `FsId`/`fs_struct` generation | Different contexts, or read-only use with no mutation |
| Namespace membership | PID, mount, net, IPC, user, UTS, cgroup, time | namespace type plus stable object generation | Different relevant namespaces and no shared object crossing |
| PID/TID allocation | identifier selection, reuse, existence | PID namespace allocator plus virtual ID | Separate PID namespaces or deterministic virtualization |
| Other allocators | inode, SysV ID, timer ID, watch descriptor, ephemeral port | owning namespace/object allocator | Separate allocator scopes or deterministic virtualization |
| procfs per-process view | status, maps, fd, io, sched, existence | PID namespace plus target task/process | Target invisible, or reads served from deterministic modeled state |
| procfs/sysfs global view | CPU, memory, interrupts, net, devices, kernel knobs | virtual machine/container observation domain | Virtualize/snapshot; otherwise global or external domain |
| Clocks and timers | time values and expiry order | clock domain, timer generation | Deterministic virtual time and commit-ordered expiry |
| Randomness and ASLR | random values, seed/initialization state | deterministic PRNG stream/domain | Separate deterministic streams or explicit shared stream ordering |
| CPU scheduler resources | runqueue, priority, affinity, throttling | virtual scheduler plus CPU/cgroup domain | Replaced by Detcore scheduling; host effects must not affect commit |
| Memory pressure/OOM | allocation success, reclaim, swap, kill selection | memory cgroup/host pressure domain | Enforced separate memory domains or exclusive external fallback |
| CPU/IO/PID cgroup limits | quota, bandwidth, `pids.max`, throttling | controller and cgroup generation | Separate enforced controller domains |
| Page cache and KSM | residency, merging, reclaim, timing | backing object plus host memory domain | Timing virtualized and no architectural pressure effect |
| Microarchitecture | cache, TLB, predictors, SMT, memory bandwidth | host placement domain | Timing fully unobservable, or physical partitioning |
| Container devices | stdin/out/err, PTY, `/dev` state | device/terminal instance | Different devices or deterministic output commit |
| External filesystem/network | state changed by actors outside Detcore | `ExternalWorld` plus recorded input stream | Snapshot/isolate, or fence and record every observable result |
| Kernel-global policy | sysctls, keyrings, mount/ns mutation, host services | `GlobalKernel` | Refuse or execute exclusively under explicit policy |

### 2.1 Namespace isolation is selective

Namespaces cut only the channels keyed to them:

- PID namespaces limit PID visibility, signals by PID, pidfd lookup, and
  per-process procfs visibility.
- Mount namespaces separate path resolution, `/tmp`, path-based UNIX sockets,
  and POSIX shm only when no shared bind mount crosses the boundary.
- Network namespaces separate ports, INET state, abstract UNIX sockets, and
  `/proc/net`.
- IPC namespaces separate SysV IPC and POSIX message queues, not pipes, file
  mappings, or ordinary UNIX sockets.
- User namespaces change authorization and capability interpretation; they do
  not remove an otherwise reachable shared object.
- UTS and time namespaces isolate narrow identity/time views.
- A cgroup namespace changes the view of cgroup paths, not resource limits.

CPU, cache, memory bandwidth, OOM fate, filesystem capacity, global procfs
aggregates, and much of `CLOCK_REALTIME` remain shared unless cgroup,
filesystem, physical-placement, or virtualization policy handles them.

### 2.2 Dynamic topology is part of the resource model

The graph is not fixed at fork. Causal edges are created and removed by:

- `clone` sharing flags, fork, exec, exit, wait, and reparenting;
- `mmap`, `mremap`, `mprotect`, `munmap`, and file-backed shared mappings;
- `open`, `dup`, `close`, `close_range`, and `SCM_RIGHTS`;
- bind/connect/accept, epoll registration, and shutdown;
- link/rename/unlink and mount-namespace operations;
- signal-disposition, session, process-group, TTY, and credential changes;
- entering namespaces or moving among cgroups.

An admission decision is unsound if one action can create a new alias while a
supposedly independent action is already executing.

## 3. Current Detcore coverage map

### 3.1 Status vocabulary

- **M - modeled:** Detcore owns meaningful state and deterministic ordering.
- **V - virtualized:** guest observations are replaced by deterministic state.
- **R - refused:** strict policy rejects the operation with a stable error.
- **S - serialization-dependent:** Linux owns the state; current correctness
  relies mainly on the one-turn scheduler or polling.
- **I - isolation assumption:** correctness requires stable external state or
  resource isolation outside Detcore.
- **G - gap:** identity or causal state is not complete enough for parallel
  admission.

More than one status can apply to one family. "Modeled" does not automatically
mean "usable as a scheduler conflict key."

### 3.2 Coverage table

| Resource family | Current Detcore coverage | Status | Parallelism consequence |
| --- | --- | --- | --- |
| Global guest scheduling | Full quiescence, deterministic priority/round-robin, one released turn | M | Safe today, but globally serial |
| `ResourceID` reader/writer API | Vocabulary exists; data arms grant immediately; releases log; multi-key requests panic | G | Cannot protect concurrent actions |
| Task/thread identity | `DetPid`/`DetTid`, thread groups, parent/child tree, pedigree | M/G | Useful base; physical/virtual identity and generation are not uniform |
| Address-space identity | `MmId` shared for `CLONE_VM`, renewed on exec | M | Can form coarse memory domains |
| Mapping aliases | `SharedMemoryObjectId` and intervals resolve shared futex keys | M/G | No ordinary load/store or byte-range conflict tracking |
| Futexes | Canonical private/shared keys, waiter queues, deterministic wake selection | M | Strong subsystem model; still tied to globally serial turns |
| FD table slots | `FilesId`, `FdSlot`, clone/fork/exec metadata | M | Good alias substrate, not a live lock resource |
| Open file descriptions | `OpenFileId`, shared metadata across dup/fork | M/G | Cursor, flags, lock, endpoint aspects lack scheduler conflicts |
| Regular file I/O | selected file-content/path requests and metadata normalization | S/M/G | Safe mainly because turns serialize; keys are incomplete |
| Pipe/FIFO state | FD type known; Linux owns buffer and wake state | S/G | Same pipe component must remain serial |
| Socket state | type/path/selected port metadata; deterministic port allocation | M/S/G | Queue, peer, backlog, shutdown, ancillary data not modeled |
| `SCM_RIGHTS` | Not integrated into a complete FD alias graph | G | Dynamic cross-domain alias can be missed |
| epoll/poll | polling/backoff machinery and FD tags | M/S/G | Interest/readiness graph is not a conflict resource |
| eventfd/timerfd/signalfd/inotify | object types recognized; selected behavior modeled | M/S/G | Counters and queues need canonical object state |
| Filesystem objects | raw inode-like identities, paths, logical metadata time | M/G/I | Raw inode/path keys are not namespace/alias canonical |
| Directory entries/rename | declared resource vocabulary, incomplete construction, single-key limit | G | Namespace mutations require atomic multi-key effects |
| procfs | deterministic snapshots/normalization for selected files | V/M/G | Per-process and global coverage remains incomplete |
| PIDs and ancestry | task tree, some virtual identity, fork policy, wait/exit state | M/V/G | Not a complete lifecycle dependency DAG |
| Fork/clone/exec/exit | many transitions handled, `vfork` barrier, clear-child-tid | M/G | Changes several graphs atomically; current effect set cannot express that |
| Signals | deterministic target choice, inbound-signal turn, selected masks/dispositions | M/G | Complete queues, coalescing, realtime order, and lifecycle atomicity missing |
| Timers and logical time | per-task progress, summed global time, ordered timed events | M/V | Parallel execution needs a commit-time frontier |
| Randomness/CPUID/RDTSC | deterministic PRNG and interception/virtualization | V | Partitionable if streams and observations remain deterministic |
| External blocking I/O | background call, poll/rejoin, some record/replay | S/I/G | Host completion can race; must become an explicit input fence |
| SysV IPC, POSIX mqueues, native AIO, `io_uring` | source report identifies stable strict-mode refusals for unmodeled forms | R | Deterministic boundary, but no parallel resource model |
| Cross-process memory, keyrings, namespace/mount mutation | generally refused or constrained by fixed policy | R/I | Keep refused until effects are modeled |
| Filesystem mutation by outside actors | explicitly outside determinism guarantee | I | Snapshot/isolate or use `ExternalWorld` exclusively |
| Network outside container | explicitly outside determinism guarantee | I | Record/isolate; never infer independence from FD disjointness alone |
| Credentials, `CLONE_FS`, `CLONE_SIGHAND`, namespaces | partial per-subsystem handling, no uniform alias IDs | G/R | Sharing can connect supposedly separate process domains |
| Process groups/sessions/TTY | selected signal/lifecycle behavior | S/G | Job-control and terminal broadcast require shared identities |
| Cgroup limits, quota, OOM, host pressure | not a Detcore resource model | I/G | Must isolate or conservatively globalize |
| CPU/cache timing | rendered largely unobservable by deterministic virtual time | V/I | Safe only while no host timing/fate leaks into architectural results |
| Container stdio/devices | declared `Device`; often reached through procfs FD paths | S/G | Shared output/device state requires ordered commit |

### 3.3 What current serialization buys

One admitted turn imposes a total order over almost every in-container kernel
operation. That is why incomplete data resources are not immediately unsafe.
It also converts many blocking calls to deterministic poll/retry sequences so
an internal peer can make progress.

The main existing exceptions are already the same places a parallel scheduler
would be vulnerable:

- background external I/O;
- physical signal arrival;
- state changed by actors outside the container;
- unvirtualized host properties;
- modes that disable thread sequentialization.

Those boundaries must be tightened before broadening concurrency.

### 3.4 Reconciliation rule for contradictory coverage claims

The Linux causation report occasionally describes a Hermit disposition based
on broader project history. The Detcore report is the source-based snapshot for
this synthesis. When the reports differ, treat an operation as **refused or
unverified at the analyzed commit** until a current source audit and strict
test prove otherwise. A newly supported syscall does not become parallel-safe
until it also has a complete effect descriptor and canonical resource keys.

## 4. Target resource and causation model

### 4.1 Canonical resource keys

Replace coarse or ambiguous keys with stable, scoped identities. Initial keys
should be deliberately coarse:

```text
MemoryPrivate(MmId, Range)
MemoryShared(SharedMemoryObjectId, OffsetRange)
FdSlot(FilesId, Fd)
OpenFile(OpenFileId, Aspect)
Pipe(PipeId, Aspect)
Socket(SocketId, Aspect)
PollGraph(PollObjectId)
FsContext(FsId)
FsObject(FsNamespaceId, MountId, InodeGeneration, Aspect)
DirEntry(FsNamespaceId, ParentObjectId, Name)
TaskLifecycle(DetTidGeneration)
ProcessLifecycle(DetPidGeneration)
ThreadGroup(ThreadGroupId)
WaitRelation(ParentId, ChildId)
SignalDisposition(SighandId)
SignalQueue(ProcessOrTaskId)
SignalMask(DetTidGeneration)
Namespace(NamespaceType, NamespaceId)
Terminal(TerminalId)
Timer(TimerIdGeneration)
VirtualClock(ClockDomainId)
ResourceBudget(ControllerType, CgroupId)
ContainerDevice(DeviceId)
ExternalWorld(InputClass)
GlobalKernel
```

Each identity needs a generation. Reuse of an FD, PID, inode, timer ID, or
kernel address must not alias stale scheduler state.

### 4.2 Alias and causality graph

Maintain separate edge classes rather than one undifferentiated graph:

- **Alias edges:** two handles name the same mutable object.
- **Ownership edges:** a table or namespace owns a slot/object.
- **Topology edges:** operations can add/remove aliases.
- **Directed dependency edges:** wait, signal, pidfd target, timer wakeup.
- **Pressure-domain edges:** tasks can affect the same quota/OOM/controller.
- **External edges:** an observation depends on uncontrolled host state.

Coarse Phase 1 serialization may union all relevant edges into connected
components. Later phases should avoid turning directed lifecycle relations into
permanent exclusion: the edge constrains only actions whose effects touch it.

### 4.3 Syscall effect descriptors

Every intercepted syscall or scheduler-visible event needs an auditable
descriptor containing:

```text
reads:       sorted canonical resource keys
writes:      sorted canonical resource keys
creates:     identities and alias/topology edges installed on success
removes:     identities and edges removed on success
blocks_on:   resource and wake predicate
wakes:       eligible task set and deterministic selection rule
external:    none | isolated | recorded input class
commit:      return value, output memory, wakeups, signals, time effects
fallback:    domain used when identity/effects cannot be resolved
```

Strict parallel mode must reject non-global admission for any action without a
complete descriptor. An empty effect set is reserved for proven pure
virtualization, not the default for missing work.

### 4.4 Atomic multi-resource acquisition

Actions declare a sorted read/write set before admission. The scheduler either
acquires the whole set atomically or queues the whole request. It never holds a
partial subset while waiting for the rest.

This is required for:

- rename/link/unlink across directory entries and inode metadata;
- sendfile/splice across input/output OFDs, cursors, and content;
- fork/clone/exec/exit graph transitions;
- descriptor transfer across socket queue, receiver slot, and OFD;
- futex wake across futex queue and task run state;
- signal delivery across queue, mask/disposition, target lifecycle, and wakeup.

Canonical ordering must not depend on `HashMap` iteration.

### 4.5 Deterministic execution and commit frontier

The scheduler should separate execution from visibility:

1. Collect parked actions and complete effect sets.
2. Order candidates with the existing deterministic priority/round-robin key.
3. Select the earliest maximal nonconflicting set.
4. Execute that set in worker slots.
5. Buffer return values, output writes, wakeups, signals, and completion state.
6. Commit in deterministic scheduler order after dependencies complete.
7. Apply topology changes atomically at commit.
8. Advance logical time from committed work, never host finish order.
9. Release resources and admit the next frontier.

The unused `Action`, `ActionID`, resource table, and background pool are useful
structural seeds, but need reader/writer ownership, wait queues, completion
state, cancellation, failure cleanup, and deterministic sequencing.

## 5. Parallelism strategy by resource class

### 5.1 Partitionable immediately after coarse alias tracking

These are the best first candidates:

- separate `MmId`s with no common shared backing object;
- separate descriptor tables with no common OFD or kernel endpoint;
- private immutable inputs or filesystem snapshots with disjoint output roots;
- separate PID/IPC/network/mount namespaces where no object crosses them;
- per-task deterministic computation with virtualized clocks and RNG;
- pure syscalls whose outputs depend only on private modeled state;
- independent top-level process trees with no signals, waits, shared devices,
  external I/O, or common pressure domain.

Initial actions should be allowlisted. CPU-only user execution can overlap only
when no shared-writable mapping connects the tasks and preemption/commit remains
deterministic.

### 5.2 Partitionable after explicit object modeling

These can eventually overlap at object or aspect granularity:

- read/read access to immutable file content;
- disjoint files, directory entries, or file byte ranges;
- disjoint OFDs and endpoint objects;
- different pipes, sockets, eventfds, timers, and poll graphs;
- separate signal queues and lifecycle targets;
- separate cgroup/resource-budget domains whose limits are enforced;
- different external input streams when each is independently recorded.

### 5.3 Serialization required within a domain

Keep these serialized until their precise conflicts are modeled:

- tasks sharing any writable memory mapping;
- aliases to one OFD cursor/status/lock state;
- producers and consumers of one pipe/socket/message queue;
- one epoll graph and its watched readiness sources;
- file operations touching the same inode, directory entry, lock, or quota;
- signal queue/disposition/mask transitions for the same target group;
- lifecycle operations on the same process/thread group;
- shared stdout/stderr, terminal, or device state;
- timers and wakeups whose order crosses the same logical clock frontier.

Serialization can be per resource/action, not necessarily permanent per
process, once effect descriptors and deterministic commit are reliable.

### 5.4 Global serialization or isolation required

Use an exclusive fallback for:

- unknown syscalls or unresolved object identity;
- mutable external filesystem/network state without recording;
- global procfs/sysfs reads that are not virtualized;
- sysctls, namespace/mount topology, keyrings, and host services;
- OOM, quota, disk capacity, and cgroup exhaustion when domains are not
  independently enforced;
- physical signals and external I/O before deterministic input fencing;
- any backend that cannot provide the same interception/effect contract.

### 5.5 Shared memory boundary

Futex modeling is not shared-memory modeling. Ordinary loads and stores never
cross a syscall hook. Therefore:

- shared-read-only mappings may be placed in separate readers after protection
  and alias identity are proven stable;
- any shared-writable mapping joins its tasks into one serialized execution
  domain;
- file-backed `MAP_SHARED` also joins the memory and filesystem objects;
- `mprotect`, `mremap`, `munmap`, fork, and exec are topology barriers.

Fine-grained concurrent shared-memory execution is a separate project. It
would require DBI/compiler memory instrumentation, page-protection faulting,
hardware support, or a comparably sound access detector.

## 6. Roadmap

### Phase 0: make the existing contract honest

Work:

- Document current resource requests as turn/control requests, not locks.
- Inventory every constructed `ResourceID` and every syscall with zero, one,
  or multiple real effects.
- Add a fail-closed `GlobalKernel` descriptor for all unclassified actions.
- Remove the multi-resource panic by accepting canonical effect sets, while
  still executing globally serially.
- Add stable generations and scoped identities for PIDs, FDs, OFDs, inodes,
  namespaces, and timers.
- Test alias equivalence across dup, fork, clone sharing, exec, `MAP_SHARED`,
  hard links, rename, and descriptor passing.

Exit criteria:

- Every admitted action has an audited descriptor or explicit global fallback.
- The serial scheduler produces unchanged deterministic traces.
- No missing identity silently becomes an empty effect set.

### Phase 1: coarse serialization-domain parallelism

Work:

- Build live alias components from `MmId`, shared mapping, `FilesId`,
  `OpenFileId`, endpoint, namespace, device, lifecycle, and external edges.
- Union every shared-writable-memory component.
- Place unknown/external resources in a global component.
- Admit at most one allowlisted action per component concurrently.
- Buffer outcomes and commit in the existing deterministic task order.

Initial workload:

- independent CPU-bound processes with private memory, private FD tables,
  isolated output, virtual time/RNG, and no external I/O.

Exit criteria:

- Measurable simultaneous execution for at least two disjoint processes.
- Bitwise output, schedule trace, and logical-time equivalence to serial mode.
- Negative tests prove no overlap for every known shared object class.

### Phase 2: real resource acquisition and deterministic frontier

Work:

- Implement atomic multi-resource reader/writer acquisition.
- Add per-resource wait queues and deterministic maximal-set admission.
- Implement worker completion buffering, cancellation, exit cleanup, and
  deterministic commit.
- Define logical-time advancement from the committed frontier.
- Make topology-changing actions exclusive against affected graph regions.

Exit criteria:

- Reversed host completion order never changes guest-visible commit order.
- No deadlock from multi-key acquisition.
- Unknown actions demonstrably fall back to global serialization.

### Phase 3: FD endpoint and filesystem model

Work:

- Split OFD aspects: cursor, flags, locks, content endpoint, readiness.
- Model pipe/socket queues, endpoint relationships, shutdown, backlog, and
  ancillary-data transfer.
- Model epoll interest/readiness and eventfd/timerfd/signalfd/inotify queues.
- Canonicalize filesystem identities by namespace, mount, inode generation,
  directory entry, and aspect.
- Add atomic effects for rename/link/unlink and file-lock ranges.

Exit criteria:

- Disjoint endpoints/files overlap; aliased endpoints serialize.
- `SCM_RIGHTS` immediately merges the receiver with the transferred object.
- Hard-link, symlink, bind-mount, cwd/root, and rename alias tests pass.

### Phase 4: lifecycle, signals, namespaces, and observation

Work:

- Add stable process/thread-group generations, wait relations, pidfd targets,
  sessions, process groups, and terminal identities.
- Complete signal disposition, mask, pending queue, realtime ordering, and
  interruption effects.
- Model `CLONE_FS`, `CLONE_SIGHAND`, namespace, credential, and selected
  `prctl` sharing.
- Serve relevant procfs/sysfs observations from deterministic modeled state or
  classify them as global/external.

Exit criteria:

- Parent/child computation may overlap where action effects commute, while
  wait/exit/signal commits remain deterministic.
- Namespace and credential transitions cannot create an untracked channel.

### Phase 5: external input and resource-fate policy

Work:

- Turn external I/O into an explicit recorded-input resource and commit fence.
- Define supported isolation contracts for filesystem, network, memory, CPU,
  IO, PID limits, quota, and OOM domains.
- Detect or require cgroup/namespace/snapshot configuration at startup.
- Refuse parallel mode when an architectural pressure/fate channel is shared
  and not modeled.

Exit criteria:

- Varying host readiness does not alter committed results.
- Resource exhaustion produces either an isolated deterministic result or a
  clear unsupported-policy error.

### Phase 6: optional fine-grained shared-memory concurrency

Work only if workloads justify it:

- track ordinary memory reads/writes at range or page granularity;
- integrate access information with futex and mapping topology;
- define deterministic treatment for races rather than inheriting host order.

Until then, keep shared-writable-memory components serial.

## 7. Priority ordering of gaps

| Rank | Gap | Why it comes here | Parallelism unlocked |
| --- | --- | --- | --- |
| 1 | Complete fail-closed effect descriptors | Prevents unknown effects from being mistaken for independence | Safe allowlist and auditable global fallback |
| 2 | Canonical alias identity and generations | Every conflict decision depends on object equality | Coarse disjoint process domains |
| 3 | Deterministic execution/commit separation | Host finish order otherwise becomes guest order | Any real worker parallelism |
| 4 | Atomic multi-resource reader/writer acquisition | Linux operations naturally touch several objects | Concurrent disjoint syscalls without deadlock |
| 5 | Shared-memory component detection | Untracked loads/stores are the largest correctness hazard | Private-memory CPU process parallelism |
| 6 | FD/OFD/pipe/socket/SCM_RIGHTS graph | Inheritance and transfer create common hidden aliases | Parallel workers with private I/O |
| 7 | Filesystem canonicalization and directory-entry effects | Raw paths/inodes miss hard links, rename, mounts, and cwd/root | Parallel disjoint filesystem work |
| 8 | Fork/exec/exit, wait, signals, pidfd topology | Process relations change wakeups and visibility | Parallel related processes and process trees |
| 9 | Logical-time/timer commit frontier | Parallel host completion must not choose timer order | Timed workloads across domains |
| 10 | procfs/sysfs observation model | Ambient reads can reconnect otherwise disjoint tasks | Utility-heavy and introspective workloads |
| 11 | External-I/O recording fence | Existing background calls already escape the total order | Parallel isolated network/device input |
| 12 | Cgroup/quota/OOM/isolation contract | Resource failure remains architectural despite virtual clocks | Robust parallelism under load |
| 13 | Fine-grained shared-memory access tracking | High complexity and not needed for share-nothing tasks | Concurrent shared-memory programs |
| 14 | Microarchitectural physical isolation | Mostly neutralized by virtual time; lower architectural value | Side-channel-resistant parallel deployment |

Ranks 1-5 are correctness prerequisites. Ranks 6-10 determine how quickly real
Linux applications escape the global fallback. Ranks 11-12 make the model
robust outside a controlled test container. Rank 13 should not block the
share-nothing milestone.

## 8. Admission decision procedure

For each parked action:

1. Resolve stable task, process, namespace, FD/OFD, mapping, and filesystem
   identities.
2. Calculate the syscall/event effect descriptor.
3. Expand keys through the current alias graph and pressure/external domains.
4. If any effect or identity is unknown, replace the closure with exclusive
   `GlobalKernel` or `ExternalWorld`.
5. If the task shares writable memory with another runnable task, constrain the
   whole memory component to one executing guest turn.
6. Sort candidates by deterministic scheduler order.
7. Select the earliest maximal set with no write/write or read/write conflict
   and no conflicting topology transition.
8. Acquire each complete set atomically.
9. Execute concurrently and retain results privately.
10. Commit in deterministic dependency order.
11. Apply alias/topology changes, wakeups, signals, timers, and logical time at
    commit.
12. Release resources and audit the observed effect against the descriptor.

This procedure is conservative. A false conflict costs parallelism. A missed
alias costs correctness. Early phases should prefer false conflicts.

## 9. Validation plan

Correctness must compare parallel mode to the serial scheduler, not merely run
parallel mode twice.

Required suites:

- bitwise output, deterministic log, schedule, and virtual-time equivalence;
- adversarial alias tests for every identity and generation type;
- actual-overlap tests for independent CPU-bound processes;
- no-overlap tests for shared memory, OFDs, endpoints, filesystem names,
  signal/lifecycle state, devices, and global fallback;
- topology races covering fork/clone/exec/exit, mmap/munmap, dup/close,
  rename/link/unlink, and `SCM_RIGHTS`;
- reversed worker-completion tests for return values, timers, signals, and
  wakeups;
- external readiness perturbation under record/replay;
- unknown-syscall and failed-identity fallback tests;
- deadlock, starvation, cancellation, and process-exit cleanup tests;
- backend-specific execution checks demonstrating the same effect contract;
- stress across scheduler seeds and host worker counts.

Operational counters should expose:

- admitted parallel width and worker utilization;
- conflict and global-fallback rates by syscall/resource class;
- serialization-domain size and merge/split causes;
- deterministic-commit wait time versus execution time;
- unresolved identity/effect descriptor counts;
- external-input fences and replay usage.

## 10. Recommended first deliverable

Build a feature-gated **private-process parallel lane** with this deliberately
narrow contract:

- each eligible task has a distinct `MmId` and no shared backing object;
- no common `OpenFileId`, pipe, socket, poll object, terminal, or device;
- private/snapshotted read-only inputs and disjoint output roots;
- no signal, wait, pidfd, session, or process-group action during overlap;
- no external blocking I/O;
- virtual time and RNG only;
- unknown syscalls acquire `GlobalKernel`;
- results commit in the existing deterministic scheduler order.

This milestone proves the scheduler frontier and commit machinery without
pretending to solve shared-memory races or every Linux kernel object. After it
matches serial execution, expand one resource family at a time and require a
positive overlap test plus adversarial alias/no-overlap tests for each
expansion.

## 11. Bottom line

Linux process independence is not equivalent to separate address spaces. It is
the absence, isolation, refusal, or deterministic virtualization of every
shared architectural, allocation/fate, and observable timing channel.

Detcore already has valuable identity and subsystem foundations, especially
for address spaces, shared futex keys, FD tables/OFDs, task topology, timers,
signals, ports, inodes, time, and randomness. Those pieces are not yet one
complete conflict graph, and the declared resource API is not yet a lock
manager. Global one-turn serialization currently supplies the missing
exclusion.

The safest growth path is coarse first: fail closed, build canonical alias
components, parallelize only private process domains, and publish through a
deterministic commit frontier. Refine object/aspect granularity only after the
corresponding identity and topology tests exist. Shared-writable memory and
uncontrolled external state remain serialization or isolation boundaries until
Detcore can observe them soundly.
