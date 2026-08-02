# KVM seccomp(2) → EOPNOTSUPP parity ratchet

## Question

The ptrace-green corpus cell `tests/c/syscall_quick_wins.c` fails under the KVM
backend (empty stdout, exit 1). It bundles many "quick-win" syscalls; among them
it probes seccomp capability:

```c
struct seccomp_notif_sizes sizes;
errno = 0;
if (syscall(SYS_seccomp, SECCOMP_GET_NOTIF_SIZES, 0, &sizes) != -1 ||
    errno != EOPNOTSUPP) {
  fputs("seccomp capability probe did not return EOPNOTSUPP\n", stderr);
  return 1;
}
```

Why does KVM diverge, and can it be flipped to ptrace parity cleanly?

## Method

Root-caused in `reverie-kvm/src/executor.rs`. The `execute_basic_syscall_*`
dispatch chain had **no `SYS_seccomp` arm** (`grep -c SYS_seccomp` = 0), so a
guest `seccomp()` fell through to the default `negative_errno(libc::ENOSYS)`.
The probe therefore observed `ENOSYS`, not the asserted `EOPNOTSUPP`, and the
cell returned 1.

Golden behavior lives in detcore's reviewed `seccomp_result`
(`hermit/detcore/src/syscalls/misc.rs:48`): Hermit cannot enforce a
guest-installed BPF policy across every backend, so every operation that
survives argument validation is reported as `EOPNOTSUPP` rather than claiming a
filter was installed. Its validation ladder is:

1. `op > SECCOMP_GET_NOTIF_SIZES (3)` → `EINVAL`
2. `SECCOMP_SET_MODE_STRICT (0)` with non-zero flags or a non-null args ptr → `EINVAL`
3. `SECCOMP_SET_MODE_FILTER (1)` with any flag bit outside `TSYNC` → `EINVAL`
4. `SET_MODE_FILTER` / `GET_ACTION_AVAIL (2)` / `GET_NOTIF_SIZES (3)` with a null args ptr → `EFAULT`
5. otherwise → `EOPNOTSUPP`

Fix: add a `SYS_seccomp` dispatch arm calling a new free helper `seccomp(args)`
that replicates this ladder exactly against the raw hypercall arguments
(`op = args[0] as u32`, `flags = args[1] as u32`, `has_args = args[2] != 0`).
The helper is a pure function of the syscall immediates — no shared state, so it
is directly reachable from the free-function dispatch.

This is routine backend-parity work mirroring an already-established,
human-reviewed detcore determinization (not a new strategy, not a
core-abstraction/scheduling change), so no `post-facto-human-review` label; the
two required new-dispatch audit breadcrumbs are present
(`// AUTONOMOUS-BOT-IMPLEMENTED` + `// TODO-HUMAN-REVIEW(PR-344)`).

## Results

- `cargo test -p reverie-kvm` — **194/194 pass on real /dev/kvm** (host devbig)
  at reverie SHA `3157b542`. New unit test
  `seccomp_reports_unsupported_matching_detcore` drives `syscall_result` through
  the dispatch arm and covers: the `GET_NOTIF_SIZES` + non-null-args probe →
  `EOPNOTSUPP`; `op > 3` → `EINVAL`; `SET_MODE_STRICT` + flags / + args ptr →
  `EINVAL`; clean `SET_MODE_STRICT` → `EOPNOTSUPP`; `SET_MODE_FILTER` + bad flag
  → `EINVAL`; `SET_MODE_FILTER` + `TSYNC` + null prog → `EFAULT`; each of
  FILTER/ACTION_AVAIL/NOTIF_SIZES + null args → `EFAULT`.
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.
- Assurance: Reverie-only ⇒ floored **L0**. Full-stack `hermit run --backend
  kvm` of the cell is not asserted (debug-build KVM container boot is
  pathologically slow; see memory kvm-fullstack-debug-boot-unusably-slow).

## Interpretation

Expected parity effect once landed + reverie pin bumped: `syscall_quick_wins.c`
flips to KVM parity, **contingent on close_range (PR #340) also landing** — the
test executes its `close_range` check before the `seccomp` probe, so both gaps
must be closed for the cell to reach ptrace parity. `close_range` was the first
KVM divergence in this bundle (fixed in #340); `seccomp` is the next. All other
syscalls the cell exercises (getresuid/getresgid, munlock/munlockall, fsync,
sendfile, shutdown, F_DUPFD_CLOEXEC) are already dispatched under KVM.

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-seccomp-eopnotsupp @ 3157b542
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib seccomp_reports_unsupported_matching_detcore
```

PR: https://github.com/rrnewton/reverie/pull/344
