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

## Repro harness

```
KVM_FDDBG=1 hermit run --backend kvm --strict -- scratch/kvm-vfork-fix/evt_thread
```
(`KVM_FDDBG` diagnostics were temporary local instrumentation in
`reverie-kvm/src/executor.rs`; not for landing.)
