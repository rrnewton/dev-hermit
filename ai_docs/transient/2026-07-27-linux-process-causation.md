# Linux/POSIX Inter-Process Causation: A Ground-Truth Model

**Date:** 2026-07-27
**Task:** `research-linux-inter-process-causation` (agent hermit-274)
**Question:** By what mechanisms can process A causally affect a later
time-slice of process B on Linux? Conversely, under what conditions can two
processes be *guaranteed* independent?

## Why this matters (the Hermit framing)

`goal-hermit-v2` states the long-range aim that *"non-communicating processes
can execute in parallel."* Determinism only requires imposing an order between
two processes when one can *observe* the other. If A and B share **no causal
channel**, then interleaving their time-slices in any order yields identical
observable results, and Hermit is free to run them truly in parallel (no
serializing schedule between them, no cross-process log records).

So this document is really a taxonomy of **causal channels**: every kernel- or
POSIX-level mechanism by which the execution of one process can change what
another process observes. A pair of processes is *independent for determinism
purposes* exactly when it touches **none** of these channels (or touches only
channels Hermit has already virtualized). The final section turns the taxonomy
into a decision procedure.

Terminology used throughout:

- **Causal channel**: any shared kernel object or observable resource where a
  write by A changes a value read by B (or A's mere existence changes B's view).
- **Directed** vs **bidirectional**: signals are mostly directed (A→B); a shared
  memory segment is bidirectional.
- **Explicit** vs **implicit/ambient**: `pipe(2)` is explicit cooperation;
  observing the global PID counter is ambient — B need not opt in.
- **Content channel** vs **metadata/existence channel**: a pipe carries bytes
  A chose; the PID allocator leaks only *ordering/existence* information.

Sources are cited inline by `[n]` and listed at the end. Where a fact is stock
kernel/POSIX knowledge (man pages, kernel docs) it is stated directly.

---

## 1. Explicit IPC (both sides opt in)

These are the textbook mechanisms [1][2][3]. In all of them A and B hold
handles to a *shared kernel object*; the object's state is the channel.

| Mechanism | Shared object | Namespace/scope that gates sharing | Content or metadata |
| --- | --- | --- | --- |
| Anonymous pipe / `pipe2` | pipe inode + kernel buffer | inherited fd across fork; or via fd passing | content (byte stream) |
| FIFO / named pipe | filesystem inode | mount ns + path + permissions | content |
| UNIX domain socket | socket inode | mount/abstract ns + path | content + fd passing (SCM_RIGHTS) + creds (SCM_CREDENTIALS) |
| INET/INET6 socket | network stack endpoint | **network namespace** | content + connection state |
| POSIX shared memory (`shm_open`+`mmap`) | tmpfs object on `/dev/shm` | mount ns + path | content (raw memory) |
| System V shared memory (`shmget`/`shmat`) | SysV shm segment (key/id) | **IPC namespace** | content |
| POSIX message queue (`mq_open`) | `mqueue` fs object | **IPC namespace** + path | content (discrete messages) |
| System V message queue (`msgget`) | SysV msg queue (key/id) | **IPC namespace** | content |
| POSIX/SysV semaphores | sem set | IPC ns (SysV) / shm-backed (POSIX) | synchronization only (metadata) |
| Signals (`kill`, `sigqueue`, `pidfd_send_signal`) | target task's pending-signal set | **PID namespace** + permission check | metadata (signal no.) + up to a word via `sigqueue` |
| `eventfd`/`signalfd`/`timerfd` shared across fork | kernel fd object | fd inheritance / fd passing | counter/event content |
| `futex` on shared memory | futex word address | must alias the *same* physical page (MAP_SHARED or shared anon) | synchronization + the word's value |
| `pidfd` + `pidfd_getfd` | reference to remote task / its fds | PID ns + permission | steals a live fd from B |
| ptrace (`PTRACE_ATTACH`, seize) | debugger relationship | permission (`ptrace_scope`) + PID ns | full read/write of B's memory, regs, syscalls |
| `process_vm_readv`/`process_vm_writev` | cross-process address space copy | permission (same as ptrace) | reads/writes B's memory directly |

Key Hermit-relevant notes:

- **Futex** is the single most important synchronization channel for
  determinism. A futex is only a cross-process channel when the futex word lives
  in memory *shared* by both processes (`MAP_SHARED` file/`shm`, or shared
  anonymous memory inherited across `clone(CLONE_VM)`/fork of MAP_SHARED). A
  private-anonymous futex word is *intra-process only*. Detcore already
  virtualizes futex ordering (see memory `futex-op-coverage-catalog`,
  `detcore-futex-wait-bitset-virtual-time-bug`).
- **SCM_RIGHTS** passes an fd from A into B, adding B a handle to whatever
  object the fd names — this is how a channel can be *created dynamically*
  rather than only inherited at fork. Hermit note: SCM_RIGHTS-passed fds are not
  currently tracked in Detcore's fd table (memory `scm-rights-fds-not-in-detcore-fdtable`).
- **ptrace / process_vm_* / pidfd_getfd** are the "total" channels: they defeat
  address-space isolation entirely, subject only to the `Yama ptrace_scope` LSM
  and capability checks.

**Independence guarantee for §1:** two processes share *no* explicit-IPC channel
iff (a) they hold no fd to a common pipe/socket/eventfd (check by fd-table
provenance: no inheritance across their common fork ancestor of a shared object,
no fd passing between them), (b) they `mmap` no common file/shm region with
`MAP_SHARED` and no shared anonymous memory, (c) they are in different IPC
namespaces *or* use no SysV/POSIX-MQ objects with colliding keys/paths, (d)
neither has permission to `kill`/ptrace/`pidfd`/`process_vm_*` the other. All of
these are statically checkable from the fd tables, the mmap list
(`/proc/pid/maps`), namespace membership, and the credential/`ptrace_scope`
policy.

---

## 2. Implicit sharing via the filesystem

The filesystem is the largest ambient channel: A writes a path, B reads it,
without either naming the other. This is *not* made deterministic by Hermit —
"Hermit does not make a changing filesystem or external network deterministic"
(hermit AGENTS.md). It must be handled by isolation, not virtualization.

Channels:

- **Shared files / directories** — the obvious one. A `write(2)` visible to B's
  `read(2)`. Scope gate: mount namespace + path resolution + DAC/LSM permissions.
- **`/tmp` and other world-writable dirs** — the canonical *ambient* channel: no
  prior arrangement needed, both sides just agree on a path convention. Also the
  source of classic TOCTOU and predictable-name races. Hermit note: a *private*
  `/tmp` mount turns writes into `ENOENT` for programs expecting host state
  (memory `debug-batch...` "PRIVATE /tmp→ENOENT" trap).
- **File locks** — `flock(2)` (open-file-description scoped), `fcntl(2)` POSIX
  record locks (process + inode scoped), and OFD locks (`F_OFD_SETLK`). A
  holding a lock changes whether B's lock call blocks/succeeds — a pure
  *synchronization/metadata* channel that needs no data bytes to be written.
- **Directory entries as a channel** — `rename`, `link`, `unlink`, `mkdir`
  create/remove names B can `stat`/`open`. `O_CREAT|O_EXCL` is the classic
  "who-got-there-first" atomic rendezvous.
- **File *metadata*** — size, mtime/atime/ctime, `st_nlink`, mode bits, xattrs.
  B can observe A's activity purely through `stat` without reading content.
  Hermit note: `/proc` leaks host PID via `st_nlink` (memory `ls-la-flaky-verify-nss-poll`).
- **inotify/fanotify** — B subscribes to filesystem *events*; A's file ops
  become an event stream delivered to B. A push-style filesystem channel.
- **`syncfs`/writeback** — a documented *covert/side channel*: forcing writeback
  on a shared filesystem lets one process time and infer another's write
  activity even across containers on the same fs [4].
- **Disk/quota exhaustion** — A filling a filesystem makes B's writes fail
  `ENOSPC`; per-user disk quotas make it a per-uid channel.

**Independence guarantee for §2:** guaranteed only under **filesystem
isolation** — disjoint mount namespaces with no shared bind mounts, or provably
disjoint path sets with no common writable ancestor directory, plus disjoint
lock domains (no two processes locking the same inode), and no inotify watch by
one on paths the other mutates. Because filesystem state is *external mutable
state*, Hermit's determinism model requires a stable/isolated filesystem rather
than trying to virtualize it (hermit AGENTS.md; memory
`verify-persistent-workdir-false-nondeterminism`, `gcc-nondet-is-fs-state-not-compiler`).

---

## 3. procfs / sysfs — the introspection channel

`/proc` and `/sys` turn nearly all kernel-held process state into a *readable
filesystem*, so B can observe A without any cooperation from A. This is a
one-directional, ambient, metadata-rich channel.

- **`/proc/[pid]/…`** — B can read A's `cmdline`, `environ` (if same uid),
  `status` (memory, state, uids, `Seccomp`), `stat`/`statm` (CPU time, RSS),
  `maps`/`smaps` (address layout → defeats ASLR secrecy), `fd/` (what A has
  open), `wchan`, `io` (bytes read/written), `schedstat`. Gated by PID namespace
  (which PIDs are visible at all) + `ptrace`-style permission checks for the
  sensitive files + `hidepid=` mount option.
- **Global `/proc` aggregates** — `/proc/loadavg`, `/proc/stat` (context
  switches, total CPU), `/proc/meminfo`, `/proc/interrupts`, `/proc/net/*`,
  `/proc/sys/kernel/*`. A's activity perturbs these system-wide counters that B
  can sample: an ambient *aggregate* channel that no namespace fully hides on a
  shared kernel.
- **`/proc/sys` (sysctl)** — writable knobs (e.g. `kernel.pid_max`,
  `vm.overcommit`, `random/*`) that a privileged A can change and B observes.
- **The `/proc/[pid]` existence test** — even `kill(pid, 0)` or a bare
  `stat("/proc/pid")` reveals whether A is alive: a minimal *existence* channel.

**Independence guarantee for §3:** guaranteed only when B **cannot see A in
`/proc`** — separate PID namespaces with a private `/proc` mount (`hidepid`
alone is not enough for the global aggregates), *and* B does not read global
aggregate files whose values A perturbs. On a shared kernel the aggregate
counters (`/proc/stat`, `loadavg`, `meminfo`) are essentially impossible to
fully isolate without cgroup-namespaced/virtualized `/proc`; this is why
container `/proc` is often overlaid (e.g. lxcfs). For Hermit, `/proc` reads are
a nondeterminism source that must be virtualized (Hermit already virtualizes
syscall *identity* — getpid/getuid — but not the `/proc` filesystem view; memory
`hermit-virtualizes-syscall-identity-not-env`).

---

## 4. Kernel-allocated identifiers (existence/ordering channels)

These leak *no content*, but they leak **ordering and existence** information —
which is exactly what determinism cares about, because the value A gets depends
on what B did first.

- **PID / TID allocation** — Linux allocates PIDs roughly monotonically up to
  `pid_max`, then wraps and reuses the lowest free number [6][7]. So the PID a
  process receives *depends on how many processes were created before it* — a
  global, cross-process ordering channel. Two independent programs racing to
  `fork` get PIDs whose *relative order* encodes who ran first. Hermit
  **virtualizes PID identity** (getpid returns a deterministic virtual PID) —
  this is precisely to close this channel (memory
  `hermit-virtualizes-syscall-identity-not-env`).
- **File descriptor numbers** — within *one* process fds are the "lowest unused"
  number [8], so fd numbers are deterministic *given* that process's own history
  — **not** a cross-process channel by themselves (each process has a private fd
  table). They become cross-process only when *observed via `/proc/pid/fd`* (§3)
  or passed via SCM_RIGHTS (§1).
- **inode numbers** — assigned by the filesystem; when A creates files, the
  inode numbers B later sees on those paths depend on allocation order/history.
  A weak ambient channel; also a determinism hazard because inode numbers appear
  in `stat` output and `readdir`.
- **SysV IPC ids** (`shmid`, `msgid`, `semid`), **POSIX timer ids**, **inotify
  wd**, **socket ephemeral ports** — all allocated from kernel counters whose
  values depend on prior allocations, gated by the relevant namespace (IPC ns
  for SysV; network ns for ports).
- **Ephemeral port selection** — the source port `bind(0)` picks depends on
  what's already bound and on `net.ipv4.ip_local_port_range` + randomization; a
  network-namespace-scoped ordering channel.

**Independence guarantee for §4:** these are *global-allocator* channels. Two
processes are independent w.r.t. them only if (a) the identifiers are
**virtualized** (Hermit's approach: deterministic virtual PIDs/times), or (b)
the allocators are **namespaced apart** (separate PID ns → independent PID
counters; separate IPC ns → independent SysV ids; separate net ns → independent
ports) *and* neither process reads the other's identifiers via `/proc`. Note
that fd numbers and (mostly) intra-fs inode allocation are effectively private
already, so they rarely force serialization.

---

## 5. Memory: mappings, COW, and pressure

- **`mmap(MAP_SHARED)`** — the direct shared-memory channel (already in §1 as the
  substrate for shm/futex). Any two processes mapping the same file/`shm` object
  `MAP_SHARED` share physical pages; writes are immediately mutually visible.
- **Copy-on-write after fork** — child and parent *transiently* share physical
  pages read-only; the first write triggers a private copy. This is **not** a
  data channel (semantics are copy-private), but it *is*:
  - a **performance/timing** side channel (the write that faults is slower), and
  - a **memory-pressure** contributor (COW breakage consumes pages, see below).
  So COW is causally inert for *values* but not for *timing/resource* channels.
- **`MADV_MERGEABLE` / KSM** — kernel same-page merging deduplicates identical
  pages *across processes*, then COWs on write. This creates a genuine
  **timing/side channel**: B can detect that A holds a page with identical
  content by measuring the write-fault latency (documented KSM side channel).
- **OOM killer** — the sharpest *implicit* cross-process channel: when the system
  (or a memory cgroup) is out of memory, the kernel *kills* a process chosen by
  the `oom_score` heuristic [5]. A's allocation can cause **B to be killed**.
  This couples *any* two processes that share a memory domain (the whole machine,
  or a common memory cgroup) into a single fate-sharing pool. Scope gate: memory
  cgroup boundary (cgroup-v2 `memory.max` makes the OOM domain the cgroup, not
  the machine) [9][10].
- **Overcommit / swap / page cache** — A's allocations change global free memory,
  swap pressure, and page-cache residency, all of which B can observe via timing
  or `/proc/meminfo`. The page cache is also a **content-timing** channel:
  whether B's `read` hits cache depends on whether A recently touched the same
  file (the basis of `mincore`/cache side channels).

**Independence guarantee for §5:** guaranteed for *values* whenever there is no
`MAP_SHARED` aliasing (checkable in `/proc/pid/maps`). Guaranteed for *fate and
timing* only when the two processes are in **separate memory cgroups with hard
`memory.max` limits** (so one cannot trigger the other's OOM kill or steal its
reclaim), KSM is disabled or not spanning them, and they share no page-cache
working set. Note Hermit does not model machine-level memory pressure; it
assumes enough memory that OOM does not fire, so OOM is an *isolation
assumption*, not something virtualized.

---

## 6. Scheduling, CPU, and cgroup resources

Even with fully disjoint memory and no IPC, two processes on a shared machine
compete for CPU. This is where "a time-slice of A affects a later time-slice of
B" is most literally true.

- **CPU time competition** — the CFS/EEVDF scheduler divides CPU by weight. A's
  runnable-ness changes *when* B runs. Pure timing, but timing is observable via
  any clock (§7). Gate: shared runqueue = shared CPU set.
- **`nice`/priority, `sched_setscheduler`, `ioprio`** — A raising its priority
  (or a privileged A lowering B's) directly changes B's CPU/IO share. `nice` is
  itself partly a channel: an unprivileged process can only *raise* its own
  niceness, but `setpriority` on another process (same uid) is possible. Hermit
  determinizes the *effects* of these on virtual time (memory
  `batch35-sched-ioprio-getitimer-determinized`, `batch77-credential-setting-family-passthrough`).
- **CPU affinity (`sched_setaffinity`, cpusets)** — pins processes to CPUs;
  overlapping affinity masks = shared cores = mutual slowdown; disjoint pinned
  cpusets = no runqueue sharing.
- **cgroup CPU controllers** — `cpu.weight`/`cpu.max` (bandwidth throttling),
  `cpuset.cpus`. A cgroup with `cpu.max` throttling couples every task in it: A
  burning the quota throttles B in the same cgroup. cgroup-v2 makes the cgroup
  the resource-accounting boundary [9].
- **cgroup other controllers** — `io.max`/`io.weight` (block-IO bandwidth),
  `pids.max` (A forking to the limit stops B in the same cgroup from forking →
  `EAGAIN`), `memory.max` (§5). Each is a *resource-exhaustion channel* scoped
  to the cgroup.
- **CPU microarchitectural sharing** — shared last-level cache, TLB, branch
  predictors, memory bandwidth, SMT sibling execution ports. A's memory access
  pattern **evicts B's cache lines**, changing B's timing. This is the entire
  field of cache side/covert channels (Flush+Reload, Prime+Probe) [11][12] and
  the Spectre/Meltdown class. It requires *co-location on the same physical core
  / cache*, so `cpuset` isolation and SMT-disabling mitigate it. Purely a
  *timing/microarchitectural* channel — no architectural state crosses, but
  wall-clock-observable.

**Independence guarantee for §6:** true CPU independence requires **disjoint
pinned CPU sets** (no shared runqueue) *and* disjoint caches (no shared LLC / no
SMT siblings) *and* separate cgroup resource domains for CPU/IO/pids. Absent
that, A and B are *always* weakly coupled through timing and resource
contention. **Crucial point for Hermit:** Hermit sidesteps this entirely by
*abolishing real time and real parallelism* — it serializes threads onto one
logical CPU and replaces wall-clock/PMU-derived time with deterministic virtual
time. So under Hermit the scheduling channel is not "isolated" but *replaced by a
deterministic scheduler*, which is why chaos mode can then legitimately permute
interleavings (memory `qemu-chaos-mode-permutes-interleavings`,
`detcore-blocking-syscall-model-and-gaps`). Two processes that only ever
interacted through *timing* become genuinely independent under Hermit because
Hermit removes the timing channel.

---

## 7. Time and clocks as a channel

Timing deserves its own section because it is the channel that converts every
resource-contention effect in §5–§6 into an *architecturally observable* value.

- **`clock_gettime(CLOCK_MONOTONIC/REALTIME)`, `gettimeofday`, RDTSC, `times`,
  `getrusage`** — any of these lets B measure *how long* something took, and
  that duration encodes A's contention/activity. Without a clock, most timing
  side channels are unobservable to the victim program itself.
- **Shared wall-clock (`CLOCK_REALTIME`)** — a global value A (if privileged, via
  `settimeofday`/`adjtimex`) can move, and everyone reads.
- **Timer expiry ordering** — `timerfd`, POSIX timers, `alarm`, `setitimer`:
  when several timers fire relative to each other depends on the shared timeline.

**Independence / Hermit note:** Hermit makes `CLOCK_MONOTONIC` and friends
**deterministic** (virtual time from a counter, plus a virtualized vDSO; memory
`clock-monotonic-already-deterministic`). Because it removes the real clock, it
simultaneously removes the *observability* of every timing side channel: even if
A and B contend for cache under Hermit, B literally cannot measure the
difference, so the channel carries zero bits. This is the deepest reason Hermit
can treat timing-only-coupled processes as independent.

---

## 8. Randomness / entropy

- **`/dev/random`, `/dev/urandom`, `getrandom(2)`** draw from a shared kernel
  entropy pool/CSPRNG. Historically `/dev/random` *blocking* on low entropy was a
  cross-process resource channel (A draining entropy could block B); modern
  kernels (post-5.6, and `getrandom` with the RNG initialized) do not deplete in
  a way that blocks, so this channel is largely closed. The *values* are
  independent draws — not a data channel — but they are a **nondeterminism
  source**.
- **RDRAND/RDSEED** — hardware RNG, per-core, not really a cross-process channel
  but a determinism hazard (Hermit virtualizes via CPUID/RDRAND interception;
  hermit AGENTS.md warns these tests are host-CPUID sensitive).
- **ASLR seed** — each `execve` gets a random base; not cross-process, but a
  per-process nondeterminism source Hermit must pin.

**Independence guarantee:** randomness draws between two processes are already
*independent as values*; the only coupling was the legacy entropy-depletion
blocking, now effectively gone. For determinism, Hermit replaces the RNG with a
deterministic stream, closing the nondeterminism (not a causal A→B channel, but
in-scope for the task's "subtle channels").

---

## 9. Signals and process-lifecycle coupling

Partly in §1, but the lifecycle coupling is distinct:

- **`kill`/`sigqueue`/`pidfd_send_signal`** — directed A→B; needs matching uid or
  `CAP_KILL`, and target reachable in A's PID namespace. `sigqueue` carries a
  word of data.
- **Parent/child `wait` coupling** — a child's exit status is delivered to the
  parent via `wait`/`waitpid`/`waitid`; `SIGCHLD` notifies. The child's exit
  *code and timing* is a channel to the parent by construction.
- **`prctl(PR_SET_PDEATHSIG)`** — child asks to be signaled when *parent* dies:
  reverse lifecycle channel (memory `debug-batch138-prctl-pdeathsig`).
- **Controlling terminal / session / process group** — `SIGHUP` on hangup,
  `SIGINT`/`SIGTSTP` to the foreground process group, `tcsetpgrp` — a shared-tty
  broadcast channel to a whole process group.
- **Orphan reparenting** — when A (a parent) dies, B (its child) gets reparented
  to init/subreaper; B can observe A's death through `getppid` changing (Hermit
  determinizes ppid in the DBI backend; memory `dbi-ratchet-11-ppid`).

**Independence guarantee:** no signal coupling iff neither can `kill` the other
(disjoint uid or separate PID namespace with no `pidfd`), they are not in a
common process group/session, and neither is an ancestor of the other (no
wait/PDEATHSIG/ppid relationship). Ancestry is the subtle one — a parent and
child are *never* fully independent because of the exit-status/`wait` channel.

---

## 10. Namespaces: what each one actually isolates

Consolidating the "scope gate" column from every section above. A namespace
severs exactly the channels keyed to it [13][14][15]:

| Namespace | Cuts these channels | Does **not** cut |
| --- | --- | --- |
| **PID** | signals by pid, `/proc/[pid]` visibility, pidfd-by-pid, ptrace-by-pid, wait across the boundary | shared files, shared memory, network, IPC objects, CPU/timing, cgroup pressure |
| **Mount** | shared filesystem paths, FIFOs/UNIX-socket paths, `/tmp`, `/dev/shm` POSIX shm, bind mounts | abstract UNIX sockets, network, SysV IPC, signals (by pid), CPU, memory pressure |
| **Network** | INET/INET6 sockets, ports, `/proc/net`, abstract UNIX sockets (netns-scoped), netfilter | files, SysV/POSIX IPC, signals, CPU, memory |
| **IPC** | SysV shm/msg/sem, POSIX message queues | pipes/UNIX sockets, files, shared `mmap` of files, signals, CPU |
| **UTS** | hostname/domainname visibility | everything else |
| **User** | uid/gid mapping → *permission* to signal/ptrace/access files across the map; capabilities | the objects themselves if permissions still allow; timing/CPU |
| **cgroup** | the *view* of cgroup paths in `/proc/[pid]/cgroup` (cosmetic) | actual resource limits — those are the cgroup *controllers*, not the namespace |
| **Time** | `CLOCK_MONOTONIC`/`BOOTTIME` offset (per-ns) | `CLOCK_REALTIME` largely shared, CPU, files |

Critical caveats:

- **No namespace isolates CPU, cache, memory bandwidth, or the OOM/scheduler
  resource channels.** Those are **cgroup controllers** (§5–§6), a *separate
  mechanism* from namespaces. A container = namespaces (isolate *identity/view*)
  **+** cgroups (isolate *resources*). Confusing the two is the most common
  error; the cgroup *namespace* only changes the *path view*, not the limits.
- **The clock (`CLOCK_REALTIME`) and system-wide `/proc` aggregates leak across
  almost all namespaces** on a shared kernel — the residual ambient channels.
- **User namespaces change *permission*, not *existence*.** Two processes in
  different user namespaces can still share a file if the underlying inode is
  reachable and the uid mapping grants access.

---

## 11. The complete channel taxonomy (summary table)

| # | Channel class | Direction | Content/Metadata/Timing | Primary scope gate | Hermit disposition |
| --- | --- | --- | --- | --- | --- |
| 1 | Pipes/sockets/eventfd | bi | content | fd inheritance/passing | schedule + record cross-proc reads |
| 2 | Shared `mmap`/shm/futex | bi | content + sync | MAP_SHARED aliasing / IPC ns | virtualize futex order |
| 3 | SysV/POSIX IPC objects | bi | content | IPC ns + key/path | determinized (#820, msg #731) |
| 4 | Signals / sigqueue / pidfd | dir | metadata + word | PID ns + creds | determinized send family |
| 5 | ptrace / process_vm / pidfd_getfd | dir | total memory access | ptrace_scope + creds | out of guest scope (Hermit *is* the tracer) |
| 6 | Filesystem content | bi | content | mount ns + path + perms | **isolate** (not virtualized) |
| 7 | File locks (flock/fcntl/OFD) | bi | sync/metadata | inode identity | isolate / flock unsupported strict |
| 8 | FS metadata + inotify | dir | metadata | mount ns + perms | isolate; readdir canonicalized |
| 9 | procfs per-pid | dir | rich metadata | PID ns + hidepid + perms | virtualize identity; /proc view not fully |
| 10 | procfs/sysfs global aggregates | dir | metadata | (shared kernel) | isolate assumption |
| 11 | Kernel id allocators (PID/inode/ids/ports) | ambient | ordering/existence | namespace or virtualize | **virtualize PID/time** |
| 12 | COW / KSM | — (timing only) | timing | memory cgroup | assume enough memory |
| 13 | OOM killer | dir (fate) | existence | memory cgroup `memory.max` | isolate assumption |
| 14 | Memory pressure / page cache | bi | timing | memory cgroup | removed w/ timing |
| 15 | CPU scheduling / priority / affinity | bi | timing | cpuset / cpu cgroup | **replaced by det. scheduler** |
| 16 | cgroup resource exhaustion (pids/io/cpu/mem) | bi | metadata (errors) | cgroup boundary | isolate assumption |
| 17 | CPU microarch (cache/TLB/BP/SMT) | bi | timing | cpuset + SMT-off | removed w/ timing (§7) |
| 18 | Clocks / RDTSC / timers | dir (enables 12–17) | timing | (shared kernel) | **virtualize time** |
| 19 | Entropy pool / RNG | (legacy) fate | nondeterminism | (mostly closed) | deterministic RNG stream |
| 20 | Process lifecycle (wait/ppid/pgrp/tty) | dir | metadata + status | ancestry / session / PID ns | determinize ppid; wait modeled |

---

## 12. When is independence GUARANTEED? A decision procedure

Two processes A and B are **causally independent** — running a time-slice of A
can have *no observable effect on any later time-slice of B* — iff **every** row
below holds. This is the checklist Hermit (or any parallel-determinism engine)
can use to decide "no schedule needed between A and B."

1. **No shared address space.** `/proc/A/maps` ∩ `/proc/B/maps` contains no
   `MAP_SHARED` region backed by a common object, and no shared anonymous
   memory. ⇒ closes §1(shm/futex), §5(values).
2. **No shared fd to a common kernel object.** By fd-table provenance: no pipe,
   socket, eventfd, timerfd, or shm fd reaches both (neither inherited from a
   common ancestor nor passed via SCM_RIGHTS). ⇒ closes §1.
3. **No shared writable filesystem state.** Disjoint mount namespaces, or
   provably disjoint path sets with no common writable ancestor, no shared inode
   under lock, no inotify watch spanning them. ⇒ closes §2, §7-locks, §8.
4. **No `/proc` observation.** B cannot see A in `/proc` (separate PID ns +
   private `/proc`) and reads no global aggregate A perturbs. ⇒ closes §3.
5. **No shared identifier allocator observation.** Either identifiers are
   virtualized (Hermit's virtual PID/time), or allocators are namespaced apart
   (PID/IPC/net ns) and neither reads the other's ids. ⇒ closes §4.
6. **No signal/lifecycle relationship.** Neither may `kill`/ptrace/`pidfd` the
   other (disjoint uid or separate PID ns), they share no process group/session,
   and **neither is an ancestor of the other**. ⇒ closes §1(ptrace), §9.
7. **No shared resource-pressure domain.** Separate memory cgroups with hard
   limits (no cross OOM/reclaim), separate CPU/io/pids cgroup domains, disjoint
   pinned cpusets. ⇒ closes §5(fate), §6, §16.
8. **No timing observability.** Either disjoint caches/cores/SMT *and* disjoint
   resource domains (so no timing difference exists), **or** — the Hermit way —
   *no real clock at all* (virtual deterministic time), so any residual timing
   difference is unobservable. ⇒ closes §6-microarch, §7, §12–§14, §17–§18.
9. **No shared nondeterministic source that feeds a comparison.** Deterministic
   RNG; no shared `CLOCK_REALTIME` read into observable output. ⇒ closes §19.

**The two regimes.**

- **Pure isolation regime** (containers/sandboxes): independence is achieved by
  making rows 1–7 true through *namespaces + cgroups + filesystem isolation*, and
  row 8 through *cpuset/SMT partitioning*. This is imperfect: the shared kernel
  still leaks global `/proc` aggregates (row 4 residual) and microarchitectural
  timing (row 8 residual). **Perfect independence is not achievable for two
  processes on one shared kernel via isolation alone** — the clock and the
  microarchitecture are always weakly shared.

- **Hermit / virtualization regime**: Hermit makes rows 5, 8, and 9 true *by
  construction* — it virtualizes PID/time/RNG and **removes real parallelism and
  the real clock**, so timing, scheduling, cache, and OOM channels carry **zero
  observable bits** even when the underlying resources are shared. What Hermit
  does **not** virtualize — and therefore must *isolate* — is rows 1–4, 6, 7:
  shared memory, shared fds, the **filesystem** (explicitly out of scope per
  hermit AGENTS.md), `/proc` views, signal/ancestry relationships, and
  external resource fate. For those, two guests must genuinely not share the
  object.

**Bottom line for `goal-hermit-v2`'s "non-communicating processes run in
parallel":** a set of guest processes may be scheduled in parallel with no
inter-process ordering **iff** they pairwise satisfy rows 1–7 (no shared
*architectural* channel), because Hermit already neutralizes row 8/9 (timing +
nondeterminism). The practical detector is cheap and static: shared-memory
aliasing (maps), shared-object fds (fd-table provenance including SCM_RIGHTS),
shared filesystem writes/locks, `/proc` cross-reads, IPC-object keys, and
signal/ancestry edges. If that graph has no edge between two processes, their
time-slices commute and no schedule between them is needed.

---

## Sources

1. IPC Mechanisms in Linux — dev.to/vivx_developer/ipc-mechanisms-in-linux
2. "A guide to interprocess communication in Linux" — opensource.com (Kaiser)
3. *Understanding the Linux Kernel, 3rd ed.*, ch.19 (IPC) — O'Reilly
4. "I Know What You Sync: Covert and Side Channel Attacks on File Systems via
   syncfs" — arXiv:2411.10883
5. "Linux OOM Killer: A Detailed Guide to Memory Management" — last9.io
6. "Process identifier" — en.wikipedia.org/wiki/Process_identifier
7. "How Are Linux PIDs Generated?" — tutorialspoint.com
8. "File descriptor" — en.wikipedia.org/wiki/File_descriptor
9. Linux kernel cgroup-v2 documentation — kernel.org (Documentation/admin-guide/cgroup-v2)
10. cgroup-v1 memory controller — kernel.org/doc/Documentation/cgroup-v1/memory.txt
11. "CPU Cache and Side-Channel Attacks" — Flush+Reload/Prime+Probe overview
12. "Timing Cache Accesses to Eliminate Side Channels in Shared Software" —
    arXiv:2009.14732
13. `namespaces(7)` man page — man7.org / mankier.com
14. "Linux namespaces" — en.wikipedia.org/wiki/Linux_namespaces
15. M. Kerrisk, "Containers unplugged: Linux namespaces" (LCA talk)

*Cross-references to Hermit's own behavior cite the dev-hermit agent memory
store and the hermit `AGENTS.md`.*
