# KVM CLONE_THREAD workers bypass Detcore → cross-thread fd EBADF (folly AtomicNotificationQueue / python3)

Date: 2026-07-29
Backend: reverie-kvm (worktree branch `codex/kvm-execve-path-resolution`, reverie
worktree HEAD 41b15e0d; hermit pins reverie git rev 7424ea5 — validated locally
via an uncommitted `[patch]` redirect to the reverie worktree).

## Symptom

`hermit run --backend kvm --strict -- <threaded program that signals an eventfd
created by another thread>` aborts. For fbpython/folly this is:

```
terminate called after throwing an instance of 'std::system_error'
  what(): failed to signal AtomicNotificationQueue after write: Bad file descriptor
```

Minimal reproducer: `scratch/kvm-vfork-fix/evt_thread.c` (main creates
`shared_efd` before `pthread_create`; worker writes it — OK — then creates
`worker_efd`; after join, main writes `worker_efd` — EBADF under KVM only;
native and ptrace `--strict` pass). Observed:

```
MAIN write(worker_efd=4) failed: Bad file descriptor   # rc=4
shared_efd counter=1
```

Asymmetry: an fd created *before* the clone is visible cross-thread; an fd
created by the worker *after* the clone is invisible to the creator thread.

## Root cause (definitive, code-level)

**KVM CLONE_THREAD worker threads run the non-Detcore execution path and are
never observed by the Detcore tool.**

- `Vm::run_process_action_with_tool` (`reverie-kvm/src/vm.rs:874`) only
  tool-instruments `ProcessAction::Fork`. Its `let ... else` (vm.rs:887-897)
  falls back for every other action — including `ProcessAction::Thread` — to
  `run_process_action` (vm.rs:706), the **non-tool** path.
- The Thread branch of `run_process_action` (vm.rs:736-830) spawns the worker
  OS thread running `child.run_static_elf_process(&mut child_executor)`
  (vm.rs:817).
- `run_static_elf_process` (vm.rs:1052) is the non-tool loop: for every
  hypercall it calls `executor.execute(&request, &self.memory)` **directly**
  (vm.rs:1086). There is no subscription check, no `tool.handle_syscall_event`,
  and no `T::ThreadState` — Detcore never sees these syscalls.
- By contrast the Fork path (vm.rs:899-942) builds
  `child_thread_state = child_tool.init_thread_state(child_pid,
  Some((parent_pid, parent_thread_state)))` (vm.rs:912-913) and runs
  `run_static_elf_process_with_tool`, so forked processes ARE fully
  instrumented.

Consequence for fds: the reverie-kvm executor file table *is* correctly shared
across threads (Arc<Mutex<FileTableState>>, `thread_child` clones the Arc at
executor.rs:1414; install/write-back verified: worker's `eventfd2` write-back
puts fd 4 into the shared table, and the main thread's pre-install snapshot
shows `[3, 4]`). But **Detcore keeps its own deterministic fd model**, and the
instrumented main thread validates `write(fd)` against *that* model. Because the
worker bypassed Detcore, Detcore's model never recorded the worker's
`eventfd2`, so `write(4)` is rejected with EBADF **inside Detcore, before the
syscall is injected** to the executor. Instrumentation proof: main's
`write(1)`/`write(2)`/worker's `write(3)` all reach the executor's `write()`
(FDDBG-WRITE fires); main's `write(4)` never does.

This is broader than fds: worker threads under KVM currently get none of
Detcore's determinism (scheduling, virtual time, RNG, syscall sanitization).
The eventfd EBADF is just the first observable failure. The pre-existing TODOs
`vm.rs:735` ("Review concurrent CLONE_THREAD lifecycle semantics") and
`executor.rs:1625` ("Review CLONE_FILES descriptor-table sharing") mark this
gap.

## Fix direction

Route `ProcessAction::Thread` through the tool-instrumented path, analogous to
Fork: build the child's Detcore `ThreadState` via `init_thread_state` with the
parent linkage (so Detcore models CLONE_THREAD/CLONE_FILES sharing exactly as
the ptrace backend does) and run the worker via
`run_static_elf_process_with_tool`. The executor-side fd table is already
shared; the missing piece is that Detcore must observe the worker as a real
scheduled thread. This touches KVM thread lifecycle (concurrent OS threads all
entering the shared Detcore global scheduler), so it needs careful validation
against detcore's scheduler and should be checked for deadlock/ordering under
`--strict --verify`, not just the single reproducer.

## Confirmed implementation plan (Option A — user-approved 2026-07-29)

Coordinated Hermit+Reverie change making KVM consistent with the existing
multi-backend contract (ptrace/sabre already set
`backend_dispatches_thread_tools = true`).

**Critical ordering mechanism (verified by cross-crate trace):** For the WORKING
Fork path, the child is spawned *synchronously inside* `guest.inject(clone)`
(detcore threads.rs:325), via `KvmGuest::inject` → `complete_injection`
(reverie-kvm runtime.rs:437 → 249) → `run_process_action_with_tool` →
`init_thread_state` — all nested inside the `.await` at threads.rs:325, i.e.
BEFORE `ts.clone_flags = None` at threads.rs:342. That is why
`init_thread_state`'s `clone_flags.expect(...)` (detcore lib.rs:1069) sees
`Some`. The outer-loop `take_process_action`/`run_process_action_with_tool`
(runtime.rs:1408/1424) is a SEPARATE path used only for the direct
(unsubscribed) executor — which is how thread-clones flow today (no Tool init,
no clone_flags dependency).

Consequences for the Thread fix — BOTH must change together:
1. **Subscription:** remove `|| is_thread_clone_request(&request, &memory)` from
   the `backend_owned` computation (reverie-kvm runtime.rs:1319-1321) so a
   `CLONE_THREAD` clone/clone3 IS subscribed → reaches `handle_clone_family` →
   sets clone_flags (281) and, with `backend_dispatches_thread_tools=true`,
   `!backend_uninstrumented_thread` so `create_child_thread` (threads.rs:366-367)
   registers the child in the scheduler.
2. **Tool-instrumented Thread spawn nested in inject:** add a
   `ProcessAction::Thread` arm to `run_process_action_with_tool`
   (reverie-kvm vm.rs:874, currently Fork-only at 887-897) that builds the child
   via `init_thread_state(child_tid, Some((context.pid, context.thread_state)))`
   (clone_flags still Some because we're inside inject) and runs it via
   `run_static_elf_process_with_tool` (issues `handle_thread_start` → scheduler
   participation, consuming the child's turn). Merge with the existing Thread
   register/TLS/stack setup from `run_process_action` (vm.rs:736-830):
   thread_child executor (shares fd table Arc), set_thread_context, TLS/segment
   bases, syscall-frame copy, clear_child_tid, clear_tid_and_wake on exit. Must
   ensure the Thread spawn happens via the `complete_injection` path (inside
   inject) so clone_flags visibility holds — NOT the outer-loop path.
3. **detcore Config:** `hermit-cli/src/lib.rs:1304`
   `backend_dispatches_thread_tools = backend != Backend::Kvm` → `true` for KVM;
   flip assert at lib.rs:1854 (`assert!(kvm.backend_dispatches_thread_tools)`).

CLONE_FILES fd sharing then works automatically: `init_thread_state` Arc-clones
`file_metadata` when CLONE_FILES (detcore lib.rs:1109-1110), so the worker's
`eventfd2` `add_fd` lands in the shared table and the main thread's `write(4)`
resolves. This resolves the documented prior deadlock (runtime.rs:1314-1318),
which was the half-done state (parent through detcore, child still direct →
scheduler turn never consumed). Determinism-critical: validate `evt_thread`,
frozen corpus, python3 at `--strict` and `--strict --verify` (deadlock/order).

Option B (`discover_live_file_metadata`, SaBRe-only) was RULED OUT: KVM allocates
guest fd = lowest-free number (executor.rs:3113 `insert_file_with_flags`), not
the host fd, so `discover_fd_from_current_process`'s `fcntl(guest_fd)` on the
host process (tool_local.rs:420) would target an unrelated reverie fd. It would
also leave workers outside detcore scheduling/time/RNG (L2/B3 still divergent).

## Repro harness

```
KVM_FDDBG=1 hermit run --backend kvm --strict -- scratch/kvm-vfork-fix/evt_thread
```
(`KVM_FDDBG` diagnostics were temporary local instrumentation in
`reverie-kvm/src/executor.rs`; not for landing.)
