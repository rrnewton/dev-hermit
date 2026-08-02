# KVM ptrace(2) EPERM parity ratchet

## Question

Two ptrace-green corpus cells — `c-programs/ptrace-attach-eperm` and
`c-programs/ptrace-seize-eperm` — fail under the KVM backend (kvm_exit=1). Both
fork a child, wait for readiness, call `ptrace(PTRACE_ATTACH|PTRACE_SEIZE,
child, ...)`, and assert the call returns `-1` with `errno == EPERM`. Why does
KVM diverge, and can it be flipped to ptrace parity with a clean, self-contained
change?

## Method

Root-caused in `reverie-kvm/src/executor.rs`. The syscall dispatch chain
(`execute_basic_syscall_with_output`) had **no** `SYS_ptrace` arm, so a guest
`ptrace(2)` fell through to the dispatch default `negative_errno(libc::ENOSYS)`.
The test asserts `errno == EPERM` specifically, so `ENOSYS` fails it.

Golden behavior: under the ptrace backend the guest is already the tracee of
Hermit's supervisor. The host kernel returns `EPERM` when a task that is already
attached to one tracer tries to `PTRACE_ATTACH`/`PTRACE_SEIZE` a sibling (or
`PTRACE_TRACEME` itself) — a task cannot acquire a second tracer. That `EPERM`
is the deterministic guest-visible result. The KVM backend has no host ptrace
supervisor to reproduce it, so it must emulate the refusal.

Fix: add a request-independent `ptrace()` handler returning `-EPERM`, dispatched
for `SYS_ptrace`. Request-independent because no ptrace request can succeed for
a guest that is itself under deterministic tracing. This is an implementation of
the already-established "refuse guest ptrace" determinization, not a new
strategy — so no `post-facto-human-review` label; the two new-syscall-support
audit breadcrumbs are present.

## Results

- `cargo test -p reverie-kvm` — **192/192 pass on real /dev/kvm** (host
  devbig). New unit test
  `guest_ptrace_is_deterministically_refused_with_eperm` drives the executor
  dispatch for `PTRACE_TRACEME`, `PTRACE_ATTACH`, `PTRACE_SEIZE`,
  `PTRACE_PEEKDATA`, and `PTRACE_CONT`, asserting `-EPERM` for each.
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.
- Assurance: Reverie-only ⇒ floored L0. Full-stack `hermit run --backend kvm`
  of the two cells is not asserted here (debug-build KVM container boot is
  pathologically slow; see memory kvm-fullstack-debug-boot-unusably-slow); the
  dispatch is verified at the fast backend-test layer.

## Interpretation

Expected parity effect once landed + reverie pin bumped: **+2 cells** flip to
KVM parity (`ptrace-attach-eperm`, `ptrace-seize-eperm`), stacking on the
close_range +1 (PR #340).

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-ptrace-eperm-parity @ 63aaac6
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib guest_ptrace_is_deterministically_refused_with_eperm
```

PR: https://github.com/rrnewton/reverie/pull/341
