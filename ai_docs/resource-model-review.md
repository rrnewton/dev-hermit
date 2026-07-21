# Hermit Resource Model Review Against Linux Semantics

Date: 2026-07-21

## Scope and source revisions

This review examines the resource model in Hermit's Detcore scheduler and
compares its identities, sharing rules, and blocking protocols with Linux
kernel objects.

- Hermit: `74324fff107ed720a5c1d26d52962e9fff2400d0`
- Reverie pin inspected for context: `075d1eff799eb619282cedd303afe9fdacea02a5`
- Linux: `06a7415cf24774baf1945fc28ea152e888bd72bb`, a local
  `6.18.0-rc2`-based sched_ext tree
- Parent workspace before this document: `1917803a30f2926457dd1e2a9c9c50066d9130c5`

The Hermit primary checkout had concurrent uncommitted work in files unrelated
to this document. Findings are bound to the committed Hermit revision above,
not those mutable edits. Linux references below are paths and line numbers in
the named local revision.

The review covers:

- every `ResourceID` and `Device` variant;
- `Resources`, `Permission`, scheduler acquisition, and release behavior;
- file descriptors, open file descriptions, files, directories, mappings,
  pipes, sockets, futexes, signals, timers, epoll, inotify, and special fds;
- false sharing, missed aliases, missed races, and external-state boundaries.

This is a semantic review, not a claim that Linux exposes a formal syscall
linearizability specification. Kernel locking is evidence about object
identity and atomicity, while user-visible syscall behavior remains the
contract Hermit must preserve.

## Executive conclusion

Hermit does not currently have an enforcing resource-lock model. It has a
deterministic single-guest-thread turn scheduler with several effective
blocking and control protocols, plus a mostly dormant enum describing a
future resource model.

That distinction is foundational:

1. `Scheduler::resources`, the supposed lock table, is dead code.
2. `Action` and `bg_action_pool`, which were intended to describe concurrent
   effects, are dead code.
3. `Permission::{R,W,RW}` is never used to decide compatibility.
4. `recv_release_resources` and `recv_release_all_resources` are no-ops.
5. Requests containing more than one resource panic.
6. Ordinary data resources are immediately granted without availability
   checks.
7. `FileContents`, `FileMetadata`, `DirectoryContents`, `PathsTransitive`, and
   `Device` are never constructed in the repository.
8. `MemAddrSpace` is requested only once as a child starts and is neither
   retained nor compared with another address space.
9. `Path` is used for `openat` and the three initial stdio descriptors, but it
   is a turn marker rather than a lock.

The active model consists of:

- one scheduled guest thread at a time;
- logical timed waits through `SleepUntil`;
- an out-of-band precise futex waiter map;
- nonblocking retry and poller backoff;
- an explicitly nondeterministic external-blocking protocol;
- clone/exit/signal/trace-replay control requests.

This architecture can determinize many interactions between guest threads
because it serializes them globally. It does not establish that operations
which run outside that serialized turn, or kernel objects shared through
aliases, are correctly represented. It is especially weak for background
external I/O, separate processes sharing kernel objects, raw memory mappings,
and descriptor-based readiness.

The immediate engineering recommendation is to stop presenting
`ResourceID` as an implemented lock model. Split scheduler control requests
from kernel-object identities, then build an explicit object/effect graph only
for concurrency that actually escapes the one-thread-at-a-time baseline.

## What the scheduler actually enforces

### Serialized turns, not resource compatibility

`Resources` is submitted when a guest thread parks. The scheduler chooses one
thread and runs `step4_resource_block`. That function supports zero or one
resource; multiple resources panic. Except for a few control variants, a
singleton resource is immediately granted
(`detcore/src/scheduler.rs:1490-1650`).

The supposed lock table is declared at `scheduler.rs:246-249` with
`#[allow(dead_code)]`. No code inserts, checks, or removes entries. Likewise,
the `Action` graph at `scheduler.rs:81-96` is unused. Release RPC handlers
contain only trace messages and TODOs (`tool_global.rs:627-635`).

Consequences:

- An `R` request does not coexist with another reader because no concurrent
  ordinary guest actions coexist at all.
- A `W` request does not exclude another action because no lock is acquired.
- Resource equality does not determine scheduling order or conflicts.
- Calls to `resource_release_all` have no semantic effect today.
- Bugs that skip release are latent for the current scheduler but become
  deadlocks or stale-lock bugs if lock enforcement is enabled later.

### Where resource-shaped requests do matter

Several variants are pattern-matched as commands:

- `SleepUntil` removes a thread from the run queue until logical time reaches
  a deadline.
- `BlockingExternalIO` backgrounds the guest thread.
- `BlockedExternalContinue` marks completion of that background operation.
- `PriorityChangePoint` changes run-queue priority.
- `InboundSignal` causes a signaled scheduler response.
- `TraceReplay` prevents normal logical-time treatment.
- `FutexWait` marks a thread already parked in the separate futex waiter map.

`InternalIOPolling` itself has no special availability rule. Poller behavior
comes from `Resources::poll_attempt`, which requeues the thread at polling
priority (`scheduler.rs:1506-1522`). Any resource ID with a nonzero attempt
would take that path.

### The single-resource limit is incompatible with Linux operations

Many Linux operations are atomic over several objects:

- `rename` touches two directory inodes, source and target dentries, and
  possibly source and target inodes;
- `link` and `unlink` touch a directory, dentry, inode link count, and ctime;
- `splice`, `copy_file_range`, and `sendfile` touch two open file
  descriptions and often two backing objects;
- `poll` and `epoll` observe many wait queues;
- `sendmsg(SCM_RIGHTS)` transfers multiple open file descriptions;
- shared mappings connect an `mm_struct` to an inode/address space;
- futex requeue atomically operates on two futex keys.

A realistic resource model needs atomic multi-object effects or a canonical
transaction protocol. Panicking for more than one resource means the current
schema cannot be incrementally populated syscall by syscall.

## Resource inventory

| Resource | Hermit use | Linux analogue | Current assessment |
| --- | --- | --- | --- |
| `FileContents(DetInode)` | Never constructed | inode/address-space contents, often by byte or page range | Dormant; key is incomplete and whole-file granularity is coarse |
| `FileMetadata(DetInode)` | Never constructed | inode attributes, size, timestamps, ACL/xattr/link state | Dormant; falsely independent from contents and directory operations |
| `DirectoryContents(DetInode)` | Never constructed | directory inode plus dentry namespace | Reasonable starting concept, but mount/dentry identity and multi-dir transactions are missing |
| `MemAddrSpace(DetPid)` | Child startup only | `mm_struct`, VMAs, page tables, mapped backing objects | Incorrect identity and no persistent enforcement |
| `Path(PathBuf)` | `openat`, initial stdio | resolved `(vfsmount,dentry)` under fs/mount namespace | Raw spelling is neither stable identity nor alias-aware |
| `PathsTransitive(PathBuf)` | Never constructed | no direct kernel object; intended directory subtree | Lexical-prefix model is invalid across symlinks and mounts |
| `Device(Device)` | Never constructed | driver/open-file private state, tty, pipe, socket, device queues | Three stdio names are much too coarse and do not track redirection |
| `Exit(bool)` | `exit`, `exit_group` | task exit and thread-group exit protocol | Useful control token, not a shareable resource |
| `ParentContinue()` | clone parent rendezvous | no kernel resource; deterministic scheduling policy | Useful control token with a globally colliding identity |
| `SleepUntil(LogicalTime)` | sleeps, yield, pause | hrtimer/timerqueue wait against a clock | Active and useful, but collapses Linux clock and restart semantics |
| `InternalIOPolling` | fd calls, poll/epoll, wait4, sigtimedwait, polling futex | wait queues and readiness predicates on specific objects | Active backoff marker, not an object model |
| `TraceReplay` | schedule replay | no Linux analogue | Internal control token and should be typed as such |
| `FutexWait` | marker for precise waiter map | task queued on a futex hash bucket | Marker only; actual key and queue live elsewhere |
| `BlockingExternalIO` | potentially blocking external call begins | task sleeping in a kernel wait queue on external state | Explicit nondeterministic boundary; unsafe independence assumption |
| `BlockedExternalContinue` | external call completion | task becomes runnable after kernel wake | Active rendezvous; completion timing is nondeterministic |
| `PriorityChangePoint(u64, LogicalTime)` | chaos/yield/connect/bind heuristics | no kernel object; Hermit scheduling choice | Internal policy command, not a resource |
| `InboundSignal(SigWrapper)` | signal-delivery turn | per-task/shared pending signal state | Marker loses target, queue, mask, and `siginfo` identity |

## Per-resource analysis

### `FileContents(DetInode)`

**Linux object.** Regular file contents are mediated through an inode and its
`address_space` page cache, with direct-I/O and filesystem-specific paths as
exceptions. `struct inode` contains `i_mapping` and `i_rwsem`
(`include/linux/fs.h:793-848`). File mappings also point to the backing file
and page offset (`include/linux/mm_types.h:829-874`).

**What is sound.** Unifying hard links by inode is directionally correct: two
names of the same inode share contents. Serializing conflicting writes to one
backing object is a valid conservative policy.

**Missed behavior and false negatives.** The variant is never constructed.
Even if it were, Detcore's `InodePool` keys only on raw `st_ino`
(`tool_global.rs:76-143`), while Linux inode identity is at least superblock or
device plus inode number. Two filesystems may reuse the same inode number and
would be falsely merged. Conversely, an inode can be reached through hard
links, bind mounts, open descriptors, and mappings without going through the
same path resource.

The model also omits writes performed by `mmap(MAP_SHARED)`, truncate,
fallocate/hole punching, splice, sendfile, copy-file operations, io_uring,
filesystem ioctls, and external actors. File content changes can invalidate or
dirty mapped pages in other processes.

**False sharing.** One whole-file ID serializes disjoint `pread`/`pwrite`
ranges even when range-level concurrency would be safe. Start with whole-file
serialization for correctness, then add page or byte ranges only after
aliases are correct.

### `FileMetadata(DetInode)`

**Linux object.** Metadata spans inode fields and filesystem-specific state:
mode, ownership, size, timestamps, link count, xattrs, ACLs, seals, writeback
errors, and block allocation. Linux uses several locks rather than one
abstract metadata lock.

**What is sound.** Inode identity is appropriate for metadata shared by hard
links. Separating metadata from contents can permit read-only stat operations
to coexist with content reads in a future parallel scheduler.

**Missed coupling.** Content and metadata are not independent. A write may
change size, mtime, and ctime; truncate changes both page cache and size; link,
unlink, and rename change link counts, ctime, and directory state. These calls
need multi-resource effects.

Hermit virtualizes selected stat fields, but `DetFd.stat` is cached with a
comment that stat is valid while the fd is open. Linux does not provide that
invariant: size, timestamps, permissions, ownership, link count, and many
other fields may change while an fd remains open. The global mtime map has the
same raw-inode collision described above.

**Current status.** Never constructed; neither reads nor writes are ordered by
this ID.

### `DirectoryContents(DetInode)`

**Linux object.** Directory mutation is rooted in the directory inode and
dentries. Linux pathname code uses directory `i_rwsem`; cross-directory rename
also uses ordered parent locks and sometimes `s_vfs_rename_mutex`
(`fs/namei.c:3321-3419`, `5049-5371`). Lockless lookup observes dentry and
global rename sequence state (`fs/namei.c:1726-1845`,
`include/linux/dcache.h:233`).

**What is sound.** A directory-inode key is a useful base for create, unlink,
link, and readdir ordering. Two names in the same directory contend even when
they are different strings because directory enumeration and negative-dentry
state can observe both.

**Missed behavior.** The variant is never constructed. Rename needs two
directory resources and source/target object effects. Mount points, bind
mounts, overlay filesystems, case-folded lookup, whiteouts, symlinks, and
namespace-specific roots prevent a raw path tree from replacing resolved
directory identity.

**False sharing.** A whole-directory key is conservative for independent
lookups. That is acceptable initially; per-name dentry keys can be added only
if readdir and rename still take the directory-wide effect.

### `MemAddrSpace(DetPid)`

**Linux object.** Address-space identity is `struct mm_struct`; `CLONE_VM`
shares the same object, while fork normally duplicates it with copy-on-write
(`kernel/fork.c:1513-1548`). VMAs may point to shared file/shmem backing and
therefore connect different `mm_struct` instances. `MAP_SHARED` sets
`VM_SHARED|VM_MAYSHARE` (`mm/mmap.c:435-501`).

**Identity error.** A process ID is not an `mm_struct` ID. Separate tasks may
share one mm via `CLONE_VM`, and different processes may map the same file,
memfd, or shared-memory object at different virtual addresses. Conversely,
`execve` replaces the mm while preserving the process ID.

**Enforcement gap.** The request is created only when a child starts and is
immediately granted. It is not associated with every timeslice despite the
comment in `resources.rs`, and it creates no conflicts.

**Missed effects.** `mmap`, `munmap`, `mprotect`, `mremap`, `madvise`, brk,
userfaultfd, process-vm access, ptrace, file truncation, and shared page faults
can affect other tasks. Hermit passes most mapping mutations through and does
not track VMA intervals or backing aliases. Serialized execution orders guest
loads and stores in one shared mm, but it does not simulate weak memory and
does not capture external processes or background kernel/device writes.

**Required replacement.** Give each observed `mm_struct` a stable `MmId`, and
model shared backing separately as `(BackingObjectId, page range)`. `CLONE_VM`
propagates `MmId`; fork creates a new one with COW ancestry; exec creates a new
one; shared mappings connect multiple mm IDs to one backing object.

### `Path(PathBuf)`

**Linux object.** A resolved Linux path is a `(vfsmount,dentry)` pair
(`include/linux/path.h`), interpreted relative to a task's `fs_struct`, mount
namespace, root, cwd, or dirfd. Lookup walks components, symlinks, mountpoints,
and `..` under rename sequence validation (`fs/namei.c:2096-2751`).

**Identity error.** Raw bytes are not object identity:

- `foo` in two cwd values names different objects;
- the same object can be named by absolute, relative, `.`/`..`, hard-link,
  symlink, bind-mount, and `/proc/self/fd` paths;
- the same absolute spelling can name different objects in two mount
  namespaces or roots;
- an `openat` dirfd changes the base but is absent from the key;
- a rename can change path-to-object mapping after a request is made.

Hermit reads `openat`'s pathname and requests `Path(path.clone())`, ignoring
dirfd and flags. `O_CREAT`, `O_EXCL`, and `O_TRUNC` have different effects but
receive the same `R` request. Other path-mutating syscalls do not use the
resource.

**False sharing and missed sharing.** Same spelling can falsely collide;
different aliases of one object fail to collide. Stdio resources use
`/proc/<pid>/fd/{0,1,2}` strings, so stdout and stderr redirected to the same
open file description remain distinct.

**Recommendation.** Treat unresolved pathnames as inputs, not resource IDs.
Resolve to a namespace-aware dentry/mount identity where possible. Model the
parent directory plus final component for create/unlink operations and retain
the open file description after open succeeds. External mutable filesystems
still require record/replay or an immutable-input contract.

### `PathsTransitive(PathBuf)`

There is no Linux object corresponding to a byte-prefix subtree. `/a/b` is a
prefix of `/a/b2`; symlinks escape or enter lexical trees; bind mounts graft
unrelated trees; `..` and mount namespaces change ancestry. The variant is
correctly labeled unstable and is never constructed.

Do not activate it. If subtree exclusion is required, key it by resolved mount
and dentry ancestry and define behavior for rename and mount changes. For an
initial implementation, a namespace-wide VFS mutation resource is safer than
an incorrect lexical prefix.

### `Device(Device)` and stdio variants

**Linux object.** Device behavior usually lives in an open file description's
`private_data` and driver-specific structures. A tty additionally has input
and output queues, terminal settings, session, foreground process group, and
signal behavior. Pipes and sockets have their own endpoint/queue objects.

**Current model.** `ContainerStdin`, `ContainerStdout`, and
`ContainerStderr` are never constructed. Initial stdio is instead represented
by three distinct `/proc/<pid>/fd/N` path strings.

**Missed aliases.** `dup`, shell redirection, inherited descriptors,
`SCM_RIGHTS`, `/dev/tty`, and two stdio numbers pointing at one open file
description are not represented. A container-global stdout ID would also be
too conservative when stdout has been redirected per process.

**Recommendation.** Remove the fixed stdio device enum. Propagate an
`OpenFileId` through inherited and duplicated descriptors. Add typed backing
IDs such as `PipeId`, `SocketId`, `TtyId`, and driver/external IDs only when
their behavior is modeled.

### `Exit(bool)`

This is an active control request, not a lock. `false` gates one task's exit;
`true` asks the scheduler to logically remove the thread group before the
kernel executes `exit_group`.

Linux group exit is attached to the thread group's shared `signal_struct`, and
`do_group_exit` calls `zap_other_threads` (`kernel/exit.c:1079-1118`). Exit
also closes fd-table references, releases mm and signal state, handles robust
futexes, clears `child_tid`, wakes pidfds and parents, and creates/reaps zombie
state.

Hermit's thread-tree grouping from `CLONE_THREAD` is a useful approximation,
and it explicitly simulates a clear-child-tid futex wake. It does not model
the full set of exit side effects or their resource aliases. If ordinary
resource locking were enabled, the two global boolean IDs would also falsely
serialize exits in unrelated processes.

Keep this behavior as `SchedulerRequest::Exit { group, process_id }`, separate
from object resources. Add explicit process/thread-group identity and tests
for wait, pidfd, robust futex, shared fd table, and blocked-thread teardown.

### `ParentContinue()`

This token creates a deterministic parent/child rendezvous after clone. Linux
does not expose a parent-continue resource; absent `CLONE_VFORK`, parent and
child are simply runnable and kernel scheduling chooses. Hermit's deterministic
choice is legitimate policy.

The singleton identity would falsely collide across unrelated clones if
resource locking were implemented. Represent it as a request containing
parent and child IDs. Model `vfork` separately because Linux blocks the parent
until child exec or exit and shares the address space during that interval.

### `SleepUntil(LogicalTime)`

This is one of the soundest active abstractions. It parks a thread on an
absolute logical deadline, orders equal deadlines deterministically, and can
advance logical time when no runnable work exists.

Linux timers are more nuanced. Relative `CLOCK_REALTIME` sleeps are treated as
monotonic; absolute realtime timers react to clock changes; hrtimers apply
timer slack; sleeps can restart or return remaining time after signals
(`kernel/time/hrtimer.c:1629-1634`, `2107-2201`). Time namespaces and
boottime/TAI/CPU clocks have distinct domains.

Hermit largely collapses these into one logical clock. That is a documented
virtual-time policy, but handlers must validate timespecs, preserve absolute
versus relative semantics, define signal restart behavior, and state which
clock IDs are intentionally normalized. The resource should become a typed
timer request rather than a lock ID.

### `InternalIOPolling`

This token labels calls converted to nonblocking probes. `poll_attempt`, not
the ID, implements backoff. One guest attempt runs per deterministic turn, so
internal pipe/socket state changed only by other serialized guest syscalls is
often reproducible.

Linux readiness belongs to concrete wait queues and objects: pipe read/write
queues (`fs/pipe.c:269-676`), socket wait queues and receive/write queues
(`include/linux/net.h:98-127`, `include/net/sock.h:354-479`), eventfd counters,
timerfd timers, signalfd pending signals, pidfd task state, userfaultfd queues,
and epoll ready lists.

**Too conservative if locks are enabled.** One global ID would serialize all
unrelated polling operations.

**Too permissive now.** The ID does not say what can make the call ready. An
external packet, helper process, device interrupt, signal, timer, or file
change can arrive between probes nondeterministically. Mixed `pollfd` arrays
have no internal/external classification.

Replace it with a `PollingRequest` containing a wait-set of object IDs,
requested events, a logical deadline, signal-mask state, and an explicit
external flag. Per-attempt scheduling can remain global initially, but the
dependency graph must identify producers of readiness.

### `TraceReplay`

This is purely an internal scheduling marker used to suppress ordinary time
behavior during schedule replay. It has no Linux analogue and no meaningful
R/W permission. It works as a command but obscures the type system by living
among object resources. Move it to `SchedulerRequest::TraceReplayStep`.

### `FutexWait`

`FutexWait` is a marker; actual precise waiters are stored in
`BlockedPool::futex_waiters`. That separation is reasonable, but the futex key
and supported operations are incomplete.

Linux keys private futexes by `(mm_struct, virtual address)` and shared futexes
by inode sequence, mapping page offset, and within-page offset
(`kernel/futex/core.c:521-630`). Hermit uses `(DetPid, virtual address)` for
every futex (`detcore-model/src/futex.rs:11-14`). It therefore misses:

- `CLONE_VM` tasks with different process IDs sharing private futexes;
- shared futexes mapped by separate processes;
- the same shared backing mapped at different virtual addresses;
- remap and file-backed identity changes.

Linux atomically locks the futex hash bucket, reads the value, and enqueues the
waiter to avoid lost wakeups (`kernel/futex/waitwake.c:591-663`). Hermit's
serialized precise path can preserve the basic compare-and-sleep ordering for
supported operations, but it ignores the bitset parameter in the scheduler:
`recv_futex_action` names `_mask` and does not use it. `FUTEX_WAIT_BITSET` and
`FUTEX_WAKE_BITSET` therefore do not filter wakees.

The model also lacks requeue, wake-op, PI futexes, robust-list cleanup,
futex2/waitv, and kernel wake ordering/priority semantics. Deterministic LIFO
or fuzzed selection is acceptable only where Linux leaves the choice
unspecified; bitset and operation semantics are not optional.

Introduce a `FutexKey` matching Linux private/shared identity, keep bitsets in
waiter entries, and treat two-key operations as atomic multi-object effects.

### `BlockingExternalIO`

This begins the only normal path where a guest action executes concurrently
with later deterministic turns. The scheduler removes the thread from the run
queue and lets its kernel call run in the background. Its own comment assumes
the call does not interfere with other actions, but no resource check verifies
that assumption (`scheduler.rs:1592-1620`).

This is too permissive. A background read, accept, connect, filesystem call,
or device ioctl may share an open file description, socket, pipe, file offset,
mapping, signal state, or external service with another guest thread. The
inactive resource model cannot prevent conflicting work.

The operation should carry an effect set and an external-operation ID. Either:

1. freeze conflicting guest effects until completion; or
2. record completion order, result, output memory, and all acquired external
   data, then replay without consulting live state.

Unknown ioctls and descriptors should default to a conservative process- or
container-wide external barrier, not assumed independence.

### `BlockedExternalContinue`

This completion rendezvous is active and necessary, but readiness is sampled
asynchronously. The code calls the ready set a "nondeterministic snapshot" and
warns when completions jump into runnable work (`scheduler.rs:1271-1355`). In
record/replay mode it busy-waits on the lowest thread ID rather than recording
the actual completion schedule.

The token needs an `ExternalOpId`; completion must be a recorded scheduler
event. A replay should deliver the recorded completion and outputs without
waiting on the original external endpoint. A singleton resource name cannot
distinguish multiple outstanding operations.

### `PriorityChangePoint(u64, LogicalTime)`

This is a Hermit chaos/scheduling command, not a Linux resource. It is useful
for deterministic policy and schedule replay. Its `Permission` is meaningless,
and resource equality embeds the chosen priority and time rather than an
object identity. Move it to a typed scheduling event.

### `InboundSignal(SigWrapper)`

Linux signal state spans:

- per-task blocked masks and pending queues (`task_struct`);
- process-shared pending signals (`signal_struct::shared_pending`);
- shared or copied dispositions (`sighand_struct`, controlled by
  `CLONE_SIGHAND`);
- standard-signal coalescing versus queued realtime signals and `siginfo`;
- target selection among unblocked threads;
- signalfd consumption of the same pending queues.

Relevant structures are in `include/linux/sched.h:1196-1202` and
`include/linux/sched/signal.h:21-107`; dequeue first checks per-task pending and
then shared pending (`kernel/signal.c:603-666`).

Hermit's token contains only the signal number. The target is implicit in the
request's thread, and no queued-event or `siginfo` identity exists. Alarm
delivery prefers a hinted thread or group leader without considering each
thread's blocked mask. Physical external signal arrival is not recorded.
`rt_sigtimedwait` uses polling, and signalfd is treated as an unusual fd rather
than connected to the signal model.

The current marker is useful for ordering a known delivery turn, but it is not
a signal resource model. Introduce explicit `SignalEventId`, target kind
(thread or process), full `siginfo`, pending-queue state, mask/disposition
versions, and signalfd wait dependencies. Keep the reserved PMU signal as a
separate instrumentation concern.

## Kernel-object domains missing from `ResourceID`

### Descriptor tables and open file descriptions

This is the largest structural gap.

Linux has a per-task or shared `files_struct` containing descriptor slots and
close-on-exec bits (`include/linux/fdtable.h:26-57`). Slots point to reference-
counted `struct file` objects. `struct file` holds shared file status flags,
offset, path, backing mapping, private driver state, and epoll hooks
(`include/linux/fs.h:1185-1249`). `CLONE_FILES` shares the descriptor table;
without it, Linux copies slots but keeps references to the same open file
descriptions (`kernel/fork.c:1572-1600`). `dup` also creates another slot for
the same `struct file`.

Hermit has one `DetFd` per numeric slot. It combines descriptor flags, status
flags, path, inode, type, stat cache, and resource. `dup_fd` clones that value
and replaces flags, so it does not represent shared offset or shared
`O_NONBLOCK`/`O_APPEND` state. Fork without `CLONE_FILES` clones all `DetFd`
values, again losing open-file-description sharing. The kernel still shares
the real `struct file`, so Hermit's logical classification can diverge from
what the injected syscall sees.

Required identities:

- `FilesId`: descriptor table and fd-slot allocation/close-on-exec state;
- `FdSlot(FilesId, fd)`: descriptor-number lifetime and replacement;
- `OpenFileId`: shared offset, status flags, ownership, and private data;
- `BackingObjectId`: inode/page cache, pipe, socket, eventfd, epoll, etc.;
- separate descriptor flags (`FD_CLOEXEC`) from open-file status flags.

These IDs must propagate through dup, fork/clone, exec, close, pidfd_getfd,
`SCM_RIGHTS`, and `/proc/<pid>/fd` reopen semantics.

### Pipes

Linux pipe endpoints share `pipe_inode_info`, including ring buffers,
read/write wait queues, reader/writer counts, capacity, packet flags, and a
mutex (`include/linux/pipe_fs_i.h:62-110`). Reads and writes update readiness
and wake the opposite queue (`fs/pipe.c:269-676`). Writes up to `PIPE_BUF` have
atomicity guarantees; larger writes can be partial/interleaved.

Hermit records only `FdType::Pipe`. The two ends receive no shared resource ID,
and duplicates/inheritance do not carry endpoint identity. Nonblocking polling
can make simple producer/consumer cases deterministic because guest turns are
serialized, but close/HUP, capacity, multi-writer atomicity, splice, and
external inherited endpoints are not modeled.

Add `PipeId` plus endpoint role/open-description identity. Effects should
include bytes or conservative whole-ring state, reader/writer reference
counts, readiness transitions, and `PIPE_BUF` atomic writes.

### Sockets

Linux binds a `struct socket` to `struct file::private_data`
(`net/socket.c:463-533`). Protocol state and receive/write queues live in
`struct sock` (`include/net/sock.h:354-479`), with a socket wait queue for
readiness.

Hermit has only `FdType::Socket` and no socket identity, peer relation, queue,
address/port namespace, accept queue, or internal/external classification.
Every socket is often made physically nonblocking and retried. This does not
make packet arrival, connection completion, peer closure, ancillary data, or
SCM_RIGHTS deterministic.

At minimum distinguish in-container connected endpoints from external
endpoints. Internal Unix socketpairs can use stable `SocketId`/peer IDs;
external sockets require recorded input, completion order, control messages,
and received fd identities.

### Epoll and poll wait sets

Linux gives every epoll fd a `struct eventpoll` with a ready list, overflow
list, mutex/spinlock, and wait queues (`fs/eventpoll.c:131-208`). Each
`epitem` keys a watched `struct file` and numeric fd, registers callbacks on
the target's wait queues, and implements edge-triggered, one-shot, exclusive,
and nested-epoll behavior.

Hermit does not create a `DetFd` for epoll fds and does not model
`epoll_ctl` state. `epoll_wait` is reduced to repeated zero-timeout probes
under global `InternalIOPolling`. The kernel object persists, so this can
observe real ready-list order, but that order may include external callbacks
and unmodeled concurrent state.

Add `EpollId`, watched `(OpenFileId, fd)` registrations, mode flags, ready
queue order, and nested dependencies. Poll/ppoll need an ephemeral wait-set
with the same underlying readiness keys. Recorder/replayer events must capture
all output entries and ordering.

### Inotify and fsnotify

Linux inotify is an fsnotify group with a notification queue plus marks
attached to inodes/mounts. It is not just an fd or path string. Events can be
coalesced, overflow, rename-cookie paired, and triggered by external actors.

Hermit has no inotify dispatch/resource model; helper code only knows how to
extract its fd for generic handling. A correct model requires `FsnotifyGroupId`,
mark IDs tied to resolved inode/mount identities, queue order/overflow state,
and external filesystem recording.

### Eventfd, timerfd, signalfd, pidfd, and userfaultfd

Each anonymous/special fd has state behind one open file description:

- eventfd has a shared counter and wait queue (`fs/eventfd.c`);
- timerfd has a clock/timer, expiration count, cancellation state, and wait
  queue (`fs/timerfd.c`);
- signalfd has a mask and consumes task/shared pending signals
  (`fs/signalfd.c:41-247`);
- pidfd points to `struct pid` and polls task exit (`fs/pidfs.c:247-253`);
- userfaultfd connects VMAs, fault queues, and resolver ioctls
  (`fs/userfaultfd.c`).

Hermit classifies these fd types but gives them no state identity and sends
reads through generic record/replay. Creation tracking alone is insufficient.
Each needs a stable backing ID, dup/fork propagation, readiness effects, and
integration with timer, signal, process, or memory domains.

### File locks, leases, and async ownership

`flock`, POSIX record locks, open-file-description locks, leases, and `F_SETOWN`
have different ownership keys: process, open file description, inode and byte
range, or signal recipient. They affect blocking, close, fork, exec, and
signals. `ResourceID` has no representation for any of them, and `fcntl`
mostly passes through.

### Namespaces, cwd/root, credentials, and mounts

Path resolution depends on `fs_struct` (cwd/root), mount namespace, idmapped
mounts, credentials, umask, and LSM state. Clone flags independently share or
copy fs, files, mm, sighand, and namespaces. Hermit tracks only a subset of
clone relationships and uses raw path strings. These objects must either be
modeled or explicitly frozen by the container contract.

### Other high-impact omissions

- System V IPC, POSIX message queues, and shared memory.
- io_uring rings, registered files/buffers, completion queues, and worker
  threads.
- arbitrary ioctl driver state.
- `rseq`, robust futex lists, scheduler affinity, and priority inheritance.
- process credentials, pid namespaces, process groups, sessions, and tty job
  control.
- asynchronous filesystem writeback/error reporting and memory pressure.

These should not all be reimplemented. Unsupported internal objects need a
declared boundary; external or asynchronous results need complete record and
replay.

## Findings by priority

### P0: The resource API claims enforcement that does not exist

The enum, permissions, acquire/release vocabulary, and comments imply a lock
manager. There is no availability check or release. This is dangerous because
new syscall handlers can appear to request correct resources while gaining
only an extra scheduler turn.

**Action:** Rename/split the API now. Use `SchedulerRequest` for active control
tokens. Mark object-resource support experimental and fail tests if a handler
claims a lock before an enforcing backend exists.

### P0: Open file description identity is absent

Numeric descriptor entries are not kernel objects. Shared offsets, status
flags, private data, epoll registrations, pipes, sockets, and special-fd state
all attach below the fd-table layer. Hermit's cloned `DetFd` model cannot
express common Linux aliases.

**Action:** Introduce `FilesId`, `FdSlot`, `OpenFileId`, and backing-object IDs
before expanding fd resource annotations.

### P0: External background operations are untracked concurrent effects

`BlockingExternalIO` deliberately runs outside serialized turns, but no object
locks or effect sets prove independence. Completion order is sampled from host
timing and is not fully recorded.

**Action:** Give every external action an ID and effect set. Conservatively
barrier conflicts, and record/replay completion plus outputs.

### P0: Futex identity and bitset semantics differ from Linux

`(DetPid,uaddr)` cannot represent Linux shared futex keys or all `CLONE_VM`
cases. The precise scheduler ignores bitsets. These are missed wakeups or
wrong wakeups, not performance differences.

**Action:** Implement Linux-shaped private/shared keys and waiter bitsets;
explicitly reject unsupported two-key/PI operations until modeled.

### P1: Path and inode keys are not namespace-safe

Raw path spelling is not resolved identity, and raw inode number without
superblock/device collides across filesystems.

**Action:** Use namespace-aware resolved identities and `(superblock, inode)`;
model parent directories and final names for path mutations.

### P1: Shared mappings are absent

`MemAddrSpace(DetPid)` neither identifies `mm_struct` nor shared backing, and
it is not enforced. Cross-process MAP_SHARED, memfd, shmem, and userfaultfd
interactions remain outside the model.

**Action:** Add `MmId`, VMA intervals, and backing-object page ranges, or state
that multi-process shared memory is unsupported.

### P1: Readiness is global polling without dependency identity

`InternalIOPolling` hides which pipe, socket, eventfd, epoll, timer, signal, or
external actor can make progress. This is conservative for scheduling but too
permissive for determinism.

**Action:** Represent wait sets and readiness producers. Record external
readiness; replay must not query live state.

### P1: Signal state is not modeled as shared/per-thread queues

Signal number alone cannot represent target selection, masks, process-shared
pending state, realtime queue entries, siginfo, signalfd, and restart behavior.

**Action:** Create explicit signal events and pending-state identities,
starting with alarm, ppoll/pselect/epoll_pwait, sigtimedwait, and signalfd.

### P2: Whole-object keys would over-serialize future parallelism

Whole file, directory, address-space, device, and global polling keys are safe
only as initial conservative barriers. They would erase most useful
parallelism if lock enforcement is added.

**Action:** Get alias correctness first; then introduce range and queue
granularity based on measured contention.

## Recommended architecture

### 1. Separate commands from resources

Use disjoint types:

```rust
enum SchedulerRequest {
    Yield,
    SleepUntil { clock: ClockDomain, deadline: LogicalTime },
    PollRetry { wait_id: WaitId, attempt: u32 },
    ExternalBegin { op_id: ExternalOpId, effects: EffectSet },
    ExternalComplete { op_id: ExternalOpId },
    FutexWait { waiter: FutexWaiterId },
    DeliverSignal { event: SignalEventId, target: DetTid },
    CloneRendezvous { parent: DetTid, child: DetTid },
    Exit { process: ProcessId, group: bool },
    PriorityChange { priority: u64, at: LogicalTime },
    TraceReplayStep,
}
```

Object resources should contain only stable kernel-object identities and
ranges. They should not encode an action or a deadline.

### 2. Build a Linux-shaped object graph

Minimum identity layers:

```text
Task -> Process/ThreadGroup
Task -> MmId
Task -> FsContextId (cwd/root/umask)
Task -> FilesId -> FdSlot -> OpenFileId -> BackingObjectId
OpenFileId -> shared offset/status/private state
BackingObjectId -> inode/page cache, pipe, socket, epoll, eventfd, ...
VMA -> MmId + address range + BackingObjectId/page range
Path input -> MountNamespaceId + base path + resolved dentry/mount
```

Assign IDs when Hermit creates or first observes objects. Propagate them across
dup, clone/fork, exec, SCM_RIGHTS, and shared mappings. Record object creation
and transfer when replay cannot reconstruct it locally.

### 3. Describe effects, not just locks

Each handled syscall should declare:

- object/range reads;
- object/range writes;
- wait dependencies;
- readiness events it may produce;
- object creation, transfer, or destruction;
- external inputs and output memory;
- whether the kernel operation is injected, emulated, recorded, or replayed.

The scheduler may keep global serialization at first. Effect declarations are
still valuable for auditing, external conflict barriers, schedule exploration,
and future parallelism.

### 4. Support atomic multi-object transactions

Do not acquire resources incrementally in handler order. Build a complete
effect set before committing the turn. Sort canonical keys or have the
scheduler atomically validate the whole set. Reader/writer state must support
multiple readers; the current one-`ActionID` map cannot.

### 5. Make external boundaries explicit

Classify every object as:

- deterministic internal;
- immutable external input;
- recorded external state;
- unsupported.

Mixed operations use the strictest class. For example, a poll array containing
one external socket records the entire observed result and output order. Replay
never asks the live socket.

### 6. Preserve the serialized baseline

One guest thread at a time is the strongest correctness baseline available.
Do not add parallel guest execution merely because resource names exist.
Enable concurrency only for effect sets proven independent, and continuously
compare the concurrent engine with the serialized oracle.

## Validation plan

### Static invariants

1. Every handled syscall has a declared effect classification.
2. Every object resource variant has at least one constructor and conflict
   test, or is removed.
3. No resource acquisition path can skip release on error or cancellation.
4. Dispatch and release subscriptions reach every handler in optimized builds.
5. Multi-object effects cannot partially acquire.
6. Replay paths do not consult live external readiness or data.

### Descriptor and file tests

- `dup` shares offset and `O_NONBLOCK`/`O_APPEND`, but not `FD_CLOEXEC`.
- Fork without `CLONE_FILES` copies slots while preserving open-file-
  description sharing; `CLONE_FILES` shares slot close/replacement.
- Two fds for separate opens of one inode have separate offsets.
- Hard links unify inode contents/metadata; equal inode numbers on different
  filesystems do not.
- `SCM_RIGHTS` transfers open-file-description identity.
- Concurrent append, truncate, mmap-shared write, and stat effects are ordered.

### Path and directory tests

- Same relative spelling under different cwd/dirfd does not alias.
- Absolute, relative, hard-link, symlink, bind-mount, and `/proc/self/fd`
  aliases resolve as Linux does.
- Rename across two directories is one transaction.
- Readdir observes deterministic create/unlink/rename order.
- Separate mount namespaces do not collide by path string.

### Memory tests

- `CLONE_VM` shares `MmId`; exec replaces it.
- MAP_SHARED memfd pages alias across processes at different addresses.
- MAP_PRIVATE fork/COW separates writes while preserving initial ancestry.
- truncate/hole-punch invalidation interacts with mapped readers.
- userfaultfd queue and resolver operations are ordered.

### Pipe, socket, and readiness tests

- Pipe endpoints and duplicates share one ring and reader/writer counts.
- `PIPE_BUF` writes remain atomic; larger writes permit deterministic partials.
- Close produces deterministic EOF/HUP/SIGPIPE transitions.
- Unix socketpair queues, accept queues, shutdown, and SCM_RIGHTS are modeled.
- poll/ppoll and epoll agree on readiness for internal objects.
- epoll edge-triggered, one-shot, duplicate-fd, close, and nested cases match
  native Linux.
- eventfd counter, timerfd expiration count, signalfd queue, and pidfd exit
  readiness survive dup/fork and replay.

### Futex and signal tests

- Private futexes key by shared mm plus address.
- Shared futexes match across different virtual addresses of one memfd.
- WAIT/WAKE_BITSET filters correctly.
- requeue and robust clear-child-tid behavior either match or fail explicitly.
- Process-directed signals choose an unblocked thread and preserve shared
  pending state.
- Standard signals coalesce; realtime signals retain ordered `siginfo`.
- ppoll/pselect temporary masks, EINTR, restart, and signalfd consumption are
  coherent.

### External and replay tests

- Two external operations completing in opposite host orders replay in the
  recorded order.
- A blocking external operation that aliases an internal fd/file is barred
  from conflicting guest effects.
- Mixed internal/external poll records the full result.
- Replay succeeds with original network peers and helper processes absent.

## Phased implementation plan

### Phase 0: Make current truth explicit

- Rename `Resources` used as scheduler commands to `SchedulerRequest`.
- Document that ordinary guest actions are globally serialized.
- Remove unused resource variants or place them behind an explicitly
  non-enforcing `EffectKey` type.
- Add assertions/tests proving release handlers are currently no-ops so future
  enforcement cannot be enabled accidentally.

### Phase 1: Correct fd identity and blocking boundaries

- Add `FilesId`, `FdSlot`, `OpenFileId`, and backing IDs.
- Fix dup/fork/exec/SCM_RIGHTS propagation.
- Attach pipe, socket, epoll, and special-fd state.
- Give external operations IDs and record completion order and outputs.
- Add wait-set dependency descriptions.

### Phase 2: Correct futex, signal, path, and mapping aliases

- Implement Linux-shaped private/shared futex keys and bitsets.
- Add signal event/pending state sufficient for atomic mask-and-wait calls.
- Replace raw path and raw inode keys with namespace-aware resolved identity.
- Add `MmId`, shared backing IDs, and VMA/page ranges.

### Phase 3: Optional safe parallelism

- Implement reader/writer compatibility and atomic multi-resource effects.
- Start with conservative whole-object conflicts.
- Compare every parallel result against serialized execution and record all
  nondeterministic external completions.
- Add range granularity only where profiling proves whole-object conflicts are
  a bottleneck.

## What is modeled well today

The review should not obscure several solid choices:

- The scheduler explicitly distinguishes internal polling from external
  blocking rather than letting a serialized guest silently deadlock.
- Poll attempts are visible to deterministic scheduling and have starvation
  backoff.
- Logical timed waits use a sorted deadline structure and advance time when
  no runnable work exists.
- Precise futex wait/wake is modeled in scheduler state rather than leaving a
  guest thread blocked in the kernel.
- Clone/exit and signal delivery have explicit scheduler rendezvous points.
- The source comments are unusually candid about nondeterministic external
  completion and missing resource locking.

These are useful scheduler primitives. The problem is the gap between those
working primitives and the broader `ResourceID` abstraction, not the absence
of any deterministic scheduling design.

## Final assessment

Hermit's determinism currently rests on global guest-thread serialization and
specific syscall protocols. It does not rest on a faithful model of Linux
resource sharing. That is adequate for many single-process, internally
coordinated workloads, but it is not a sound foundation for relaxing
serialization, claiming complete multi-process determinism, or reasoning that
two operations are independent because they requested different
`ResourceID`s.

The highest-value correction is architectural clarity followed by open file
description identity. Once descriptor aliases, external operations, futex
keys, signals, paths, and shared mappings have stable identities, a resource
or effect model can become meaningful. Before then, adding more enum variants
or resource requests creates the appearance of coverage without changing
scheduler behavior.

## Linux source map

The following local kernel files were the primary cross-reference points:

- `include/linux/fdtable.h`: fd tables and close-on-exec slots
- `include/linux/fs.h`: inode and open file description structures
- `fs/file.c`, `fs/fcntl.c`, `fs/read_write.c`: fd lookup, shared offsets, flags
- `kernel/fork.c`: CLONE_FILES, CLONE_VM, CLONE_SIGHAND sharing
- `fs/namei.c`, `fs/dcache.c`: path lookup, directory locking, rename
- `mm/mmap.c`, `mm/memory.c`, `mm/shmem.c`: VMAs and shared backing
- `kernel/futex/core.c`, `waitwake.c`, `requeue.c`, `pi.c`: futex keys/queues
- `kernel/signal.c`, `include/linux/sched/signal.h`: pending signal state
- `fs/pipe.c`, `include/linux/pipe_fs_i.h`: pipe ring/readiness semantics
- `net/socket.c`, `include/linux/net.h`, `include/net/sock.h`: socket state
- `fs/eventpoll.c`: epoll registrations and ready queues
- `fs/notify/`, `include/linux/fsnotify_backend.h`: inotify/fsnotify
- `fs/eventfd.c`, `fs/timerfd.c`, `fs/signalfd.c`, `fs/pidfs.c`,
  `fs/userfaultfd.c`: special-fd backing objects
- `kernel/time/hrtimer.c`: timer domains, slack, restart, and nanosleep
- `kernel/exit.c`: task/thread-group exit and wait lifecycle
