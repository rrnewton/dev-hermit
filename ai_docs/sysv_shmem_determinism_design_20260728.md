# Deterministic SysV shared-memory support in detcore — feasibility & scope decision

- **Task:** `compat-sysv-shmem-support` (P1). Prompted by PostgreSQL `initdb`
  hitting `shmget` ENOSYS (`compat-deep-app-postgres`,
  [[postgres-blocked-shmget-enosys-and-root]]).
- **Date:** 2026-07-28.
- **Hermit:** `codex/compat-sysv-shmem-support` @ base `origin/main`
  `0d8066f9`, worktree `worktrees/274/hermit`. Release binary built
  (`target/release/hermit`, 55 MB).
- **Author:** impl agent, opus-4.8.
- **Deliverable:** documented scope decision (the task's explicit OR branch:
  "Prototype support OR document the precise scope decision"). No code change —
  see "Why a design note, not a PR" below.

## Question

Can detcore virtualize SysV `shmget`/`shmat`/`shmdt`/`shmctl` **deterministically**
(reach `--strict --verify` L2), instead of the current fail-closed ENOSYS
([[batch125-sysv-sem-shm-enosys-pr820]], PR #820)? And does that unblock
`postgres initdb`?

## Verdict

1. **Yes, deterministic SysV shm emulation is architecturally sound.** It is a
   real determinism feature (not a shim), and it follows the same pattern detcore
   already uses for threaded shared memory + deterministic ID remapping. It is,
   however, a **substantial, design-level change to guest memory-injection
   semantics** that must be human-approved before implementation.
2. **shm support alone does NOT give postgres a clean L2.** `initdb`'s deeper
   blocker in the *isolated* namespace is the unconditional root map (root
   refusal), which shm does not touch. Only `--no-namespace` clears root, and
   that mode weakens `--verify` (shared `/tmp`, `/proc`, PID, network). So no
   trustworthy postgres L2 exists without **both** the shm feature **and** an
   opt-in non-root uid map. This is why the honest deliverable here is the scope
   decision, not a partial prototype.

## Empirical facts that scope the work

### initdb's shm usage is entirely intra-process (native strace, PG 13.23)

```
shmget(0x3224c72, 56, IPC_CREAT|IPC_EXCL|0600) = 32792
shmat(32792, NULL, 0)                          = 0x7fa4c3d06000
shmdt(0x7fa4c3d06000)                          = 0
shmctl(32792, IPC_RMID, NULL)                  = 0
```

Every bootstrap subprocess (`postgres --boot`, run once per initdb phase)
creates a **56-byte** segment, attaches it, detaches it, and removes it —
**all in one address space, no other process ever attaches**. This is the
simplest possible SysV shm lifecycle. initdb needs only *intra-process*
emulation.

### postgres *server* (out of scope to run, but scopes the design)

The postmaster does `shmget`+`shmat` **once**, then `fork()`s backends that
**inherit** the attached mapping. It never relies on an unrelated process
attaching by key. So the server needs only *fork-inherited* sharing, not the
fully general "any process attaches by key" contract.

## Current handling (three synced sites)

shm* is classified `Determinized`, then routed to a deterministic ENOSYS:

1. `detcore/src/syscall_classification.rs:405-444` — `shmat`/`shmctl`/`shmdt`/
   `shmget` (+ `sem*`) in the `Determinized` arm, under
   `TODO-HUMAN-REVIEW(PR-859)`.
2. `detcore/src/syscall_classification.rs:942-990` —
   `is_unsupported_async_ipc_syscall(sysno)` groups `msg*`/`sem*`/`shm*`.
3. `detcore/src/lib.rs:1574-1581` — the dispatch:
   ```rust
   SyscallClassification::Determinized
       if is_unsupported_async_ipc_syscall(call.number()) =>
   {
       Err(Error::Errno(Errno::ENOSYS))
   }
   ```
   This is the exact line returning ENOSYS. PR #820 chose ENOSYS specifically
   because plain passthrough leaks host-assigned shmids → nondeterministic →
   fails `--verify`; it explicitly reserved real emulation for human review.

## Why deterministic emulation is sound (the determinism argument)

detcore serializes all guest threads/processes onto one logical CPU and picks
the next runnable task deterministically (AGENTS.md "Architecture"). It
intercepts *syscalls*, not plain loads/stores. Once memory is mapped, the guest
reads/writes it directly, and **the interleaving of those accesses is fixed by
the deterministic schedule.** This is exactly why threaded shared memory already
determinizes ([[cpp-programs-determinize-under-hermit]]: mutex/atomic/condvar C++
programs pass L2). SysV shared memory is the same physics with a different
naming layer.

Therefore the only nondeterminism SysV shm introduces is in the **syscall
results**, not the memory contents:

- **shmid** — host assigns it from a global counter (nondeterministic across
  runs). Fix: allocate deterministic ids from a pool, exactly like
  `InodePool.next_inode` (`tool_global.rs:98-176`) and
  `DevicePool.determinize` (`tool_global.rs:201-244`, the `st_dev` remap
  [[stdev-anon-bdev-determinism-fix]]).
- **shmat return address** — must come from a deterministic mmap. detcore
  already virtualizes mmap addressing via `handle_mmap` (`lib.rs:1697`).
- **shmctl(IPC_STAT) struct shmid_ds** — `shm_atime/dtime/ctime` must be
  logical time; `shm_nattch`/`shm_cpid`/`shm_lpid` must be tracked in the
  registry, not read from the host.

Memory *contents* need no special handling: they inherit schedule determinism.

## Proposed design (for human review — NOT implemented)

### A. Global segment registry in `tool_global`

Add to `GlobalState` (`tool_global.rs:243-260`), modeled on the existing
`Arc<Mutex<InodePool>>` / `Arc<Mutex<DevicePool>>` fields:

```rust
segments: Arc<Mutex<ShmPool>>,   // deterministic shmid -> ShmSegment
```

```rust
struct ShmPool {
    by_id:  HashMap<DetShmId, ShmSegment>,
    by_key: HashMap<i32, DetShmId>,   // non-IPC_PRIVATE key -> shmid
    next_id: DetShmId,                // strictly increasing, start > 0
}

struct ShmSegment {
    key: i32,
    size: usize,
    perms: u16,
    backing: BackingHandle,   // memfd / anon file that is the real memory
    nattch: u32,
    cpid: DetPid, lpid: DetPid,
    atime: LogicalTime, dtime: LogicalTime, ctime: LogicalTime,
    marked_removed: bool,     // IPC_RMID before last detach
}
```

### B. Syscall handlers (new `detcore/src/syscalls/shm.rs`)

- **shmget(key, size, flags):** if `key==IPC_PRIVATE` or (`IPC_CREAT` and key
  absent), allocate `next_id`, create a real backing store sized `size`
  (host `memfd_create` + `ftruncate`), insert into registry, set `ctime` =
  logical now. If key present and `IPC_EXCL`, return `EEXIST`. Return the
  deterministic shmid. Enforce `SHMMIN`/`SHMMAX` deterministically.
- **shmat(shmid, addr, flags):** look up segment; **inject** an
  `mmap(addr_or_NULL, size, prot_from_flags, MAP_SHARED, backing_fd, 0)` into
  the guest (reverie injected-syscall path, cf. `lib.rs:318`, 744-746);
  `nattch += 1`; `lpid` = caller; `atime` = logical now; return the mapped
  guest address.
- **shmdt(addr):** find the attachment for `addr`, inject `munmap`, `nattch -=
  1`, `dtime` = logical now; if `marked_removed && nattch==0`, free backing.
- **shmctl(shmid, cmd, buf):**
  - `IPC_STAT`/`SHM_STAT`: marshal a `struct shmid_ds` from the registry with
    logical times — never host values.
  - `IPC_RMID`: set `marked_removed`; free immediately iff `nattch==0` (Linux
    deferred-free semantics).
  - `IPC_SET`: update perms/owner in registry.
- Remove shm* (keep sem*, or split them) from
  `is_unsupported_async_ipc_syscall`; move shm* dispatch to the new handlers;
  keep both audit markers (`// AUTONOMOUS-BOT-IMPLEMENTED`,
  `// TODO-HUMAN-REVIEW(PR-id)`).

### C. The hard part: cross-process backing-fd availability

The backing store must be reachable in the process that calls `shmat`.

- **Tier 1 (intra-process): trivial.** The creating process attaches; the
  backing fd already lives in that guest. **Unblocks `initdb`.**
- **Tier 2 (fork-inherited): easy.** Children inherit the backing fd (and the
  attached mapping) across fork. **Unblocks the postgres *server* single-node
  case.** Requires the registry to survive fork (it lives in `tool_global`, the
  central address space — it does).
- **Tier 3 (unrelated process attaches by key): hard.** The backing fd must be
  transported into a guest that never inherited it (SCM_RIGHTS, or a named
  tmpfs path the guest re-opens, plus a deterministic rendezvous). This is the
  fully general SysV contract and the bulk of the complexity/risk. **Not needed
  by postgres.**

## Recommended scope

**Target Tier 2** (intra-process + fork-inherited). It unblocks both `initdb`
and the postgres server's shm use, matches every real-world sharing pattern
postgres exercises, and avoids the Tier-3 fd-transport machinery. Ship Tier 1
first (smallest diff that unblocks initdb) and add fork-inheritance validation
before Tier 2. Defer Tier 3 until a workload actually needs key-based sharing
between unrelated processes; document it as an explicit gap (fail-closed ENOSYS
for the unrelated-attach path is fine as a boundary).

Keep `sem*` as-is (ENOSYS) unless a workload needs it; postgres does not use
SysV semaphores in the initdb path (strace shows only shm*), and semaphores are
a separate determinism problem (blocking + wakeup ordering).

## Risks & open questions (for the human decision)

1. **Injected mmap into the guest** touches guest memory contracts — a
   determinism-core surface the parent guide says to discuss before
   implementing. Address placement must be deterministic and must not collide
   with the guest's own mmaps.
2. **Backing store choice** (memfd vs tmpfs file) affects Tier-3 reachability
   and must not leak host inode/dev numbers into any guest-visible `fstat` of
   the backing fd (route through `InodePool`/`DevicePool`).
3. **shmid_ds ABI** must exactly match the guest's libc layout (x86_64 Linux).
4. **RCB/preemption accounting**: injected mmap/munmap must not perturb the
   deterministic branch counts that drive preemption.
5. **Interaction with record/replay**: attaches/detaches must be logged like
   other memory-map events so replay reconstructs identical mappings.

## Bottom line for postgres

- **Shm feature (this task):** feasible, Tier 2 recommended, design-level →
  needs approval. Would let `initdb` progress past the `shmget` FATAL.
- **Still blocked without a second change:** the isolated-namespace root map
  ([[postgres-blocked-shmget-enosys-and-root]] blockers 1–2). A clean postgres
  L2 needs **both** the shm feature and an opt-in non-root uid map; shm alone
  only helps under `--no-namespace`, which is not a trustworthy `--verify`
  environment.

## Why a design note, not a PR

- Deterministic SysV shm changes **guest memory-injection semantics** — the
  parent guide's "discuss the design with the user before implementation"
  category, and PR #820's `TODO-HUMAN-REVIEW(PR-859)` explicitly deferred it.
- The task protocol: no design-level code changes without approval.
- A shm-only prototype cannot satisfy the verify criterion (postgres also needs
  the container change), so shipping partial emulation would add core-path risk
  without delivering a clean L2. The documented scope decision is the complete,
  correct answer the task's OR branch calls for.
