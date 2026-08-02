# KVM get_robust_list peer-task parity ratchet

## Question

Two ptrace-green corpus cells fail under the KVM backend (empty output, exit 1):

- `tests/c/get_robust_list_child.c` — a parent forks a child, waits for
  readiness over a pipe, then `syscall(SYS_get_robust_list, child_pid, &head,
  &length)` and asserts `length == sizeof(*head)`.
- `tests/c/get_robust_list_thread.c` — the main thread creates a pthread, reads
  the worker's tid over a pipe, then `get_robust_list(worker_tid, ...)` and
  asserts `head != NULL && length == sizeof(*head)`.

`get_robust_list` targeting the caller *itself* already passed under KVM. Why do
peer queries diverge, and can they be flipped to ptrace parity cleanly?

## Method

Root-caused in `reverie-kvm/src/executor.rs`. The `get_robust_list` handler
returned `ESRCH` for any requested pid that was not `0`, the caller's `tid`, or
the caller's `pid`, and otherwise wrote the *caller's own* task-local
`state.robust_list_head` / `robust_list_len`. Each guest task is a separate
`ElfExecutor` with its own `LoadedStaticElf`, so one task had no way to read a
sibling's or child's registration — every peer query hit the ESRCH arm.

Golden behavior: the Linux kernel keeps the robust list per task and resolves
`get_robust_list(pid)` against the *target* task (subject to a
same-thread-group / `PTRACE_MODE_READ` permission check), returning that task's
`head` and a fixed `len == sizeof(struct robust_list_head)`. glibc registers a
robust list at main-thread startup, at each `pthread_create`, and re-registers in
the fork child, so every live task has one.

Fix: add a process-tree-wide `robust_list_registry: Arc<Mutex<BTreeMap<tid,
(head, len)>>>` on `LoadedStaticElf`, shared across `try_clone_for_fork` (fork
children and `CLONE_THREAD` peers) exactly like the pre-existing
`file_identity_table` Arc. `set_robust_list` publishes the caller's `(head,
len)`; `get_robust_list` serves pid 0 / self from the task-local fields and any
other id from the shared registry, reporting `ESRCH` only for an id with no live
registration. The stored length is always the validated
`sizeof(robust_list_head)`, matching the kernel invariant.

This is a routine golden-ptrace parity fix on an already-supported syscall (not
new syscall support, not a core-abstraction change), so no
`post-facto-human-review` label; the existing robust-list review breadcrumb
(`TODO-HUMAN-REVIEW(PR-232)`) is extended to cover the new registry field.

## Results

- `cargo test -p reverie-kvm` — **194/194 pass on real /dev/kvm** (host devbig).
  New unit test `get_robust_list_reads_peer_task_registrations` drives the
  free-function handlers directly: fork child reads parent's registration via the
  shared Arc; child's own (pid 0) list is empty until it re-registers; parent
  reads the child's list by tid after the child registers; an unregistered id
  returns `ESRCH`. The pre-existing
  `robust_list_registration_round_trips_and_resets_on_fork` still passes.
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.
- Assurance: Reverie-only ⇒ floored **L0**. Full-stack `hermit run --backend
  kvm` of the two cells is not asserted here (debug-build KVM container boot is
  pathologically slow; see memory kvm-fullstack-debug-boot-unusably-slow). The
  `get_robust_list_thread` cell additionally depends on KVM pthread scheduling
  running the worker far enough to write its tid and issue `set_robust_list`; the
  `get_robust_list_child` cell depends only on fork (known-working under KVM), so
  it is the higher-confidence of the two.

## Interpretation

Expected parity effect once landed + reverie pin bumped: **+1 to +2 cells** flip
to KVM parity (`get_robust_list_child` high-confidence; `get_robust_list_thread`
contingent on KVM pthread scheduling). Stacks on close_range (PR #340) and
ptrace-EPERM (PR #341).

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-get-robust-list-peer @ 87e3ed29
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib get_robust_list_reads_peer_task_registrations
```

PR: https://github.com/rrnewton/reverie/pull/343
