# KVM ratchet 8 — determinize `recvmmsg(2)`, with a gVisor comparison

**Date:** 2026-07-26
**Task:** `impl-kvm-ratchet-8`
**Hermit branch:** `kvm-ratchet8` (based on `origin/main` `617f8188`)
**Reverie pin:** `4c6e9a0b` (unchanged — detcore-only fix, no reverie gate)
**gVisor reference:** `012cf0b0b` (`experiments/gvisor`)

## Question

`recvmmsg(2)` was Detcore-classified **Unsupported** while its scalar sibling
`recvmsg` and the send-side `sendmmsg` were **Determinized**. Under `--strict`
(which sets `shutdown_on_unsupported_syscall = true`) any guest that issues
`recvmmsg` aborts with "Sandbox container exited unexpectedly". This is the same
I/O-sibling gap class that ratchet 7 fixed for `readv` (PR #781). Can `recvmmsg`
be determinized the same way, and how does that approach compare to gVisor's?

## Method

Reclassify `recvmmsg` Unsupported → Determinized in
`detcore/src/syscall_classification.rs` (count guard `[200,91,82]` →
`[201,91,81]`) and wire the dispatch arm in `detcore/src/lib.rs` to the existing
`handle_sendrecv` path. The `NonblockableSyscall for Recvmmsg` impl already
existed in `detcore/src/syscalls/helpers.rs` (it routes through
`network_comm_syscall` and deliberately ignores the timeout argument because the
fd is made temporarily nonblocking and the Detcore scheduler owns blocking) — it
was wired but never classified, so this change only flips the classification and
uncomments the dispatch.

Repro guests (each queues two datagrams on a `SOCK_DGRAM` `AF_UNIX` socketpair
and drains both in one `recvmmsg`):

- C: `ignored/recvmmsg_repro`
- Rust regression guest added to the repo: `tests/rust/recvmmsg.rs`
  (`rustbin_recvmmsg`).

Commands:

```
hermit run --strict --verify -- ./target/debug/rustbin_recvmmsg   # ptrace, L2
hermit run --backend=kvm --strict --verify -- ./ignored/recvmmsg_repro  # KVM
cargo test -p detcore --lib
```

## Results

| Backend | Command | Result |
| --- | --- | --- |
| ptrace | `run --strict --verify -- rustbin_recvmmsg` | **PASS (L2)** — "Determinism verified" |
| ptrace | `run --strict --verify -- recvmmsg_repro` (C) | **PASS (L2)** — 244\|244 DETLOG, no diff |
| KVM | `run --backend=kvm --strict --verify -- recvmmsg_repro` | **blocked before recvmmsg** — see below |
| unit | `cargo test -p detcore --lib` | **130 passed** (incl. classification guards) |

`cargo fmt --all -- --check` and `cargo clippy -p detcore --all-targets` are
both clean.

### KVM: recvmmsg is unblocked in Detcore but the KVM executor lacks sockets

On the KVM backend the guest never reaches `recvmmsg`: socket creation itself
fails in the reverie-kvm executor.

```
socketpair(1, 1, 0, ...) = Err(Errno(ENOSYS))          # SOCK_STREAM and SOCK_DGRAM
socket(AF_INET, SOCK_DGRAM, 0) = EAFNOSUPPORT
```

This is a **cross-repo reverie-kvm executor gap** (socket-family syscalls are not
serviced when Detcore injects them under KVM), not a Detcore/hermit-side
classification issue. It is the same class of KVM-executor blocker recorded for
grep/awk (`mincore`), python/git (`#UD`), and node (memory OOB) in
`kvm-ratchet7-readv-io-sibling-gap`. The `recvmmsg` Detcore fix is backend-
agnostic and will benefit KVM as soon as the executor services socket syscalls;
it is validated end-to-end on ptrace today.

## gVisor comparison

gVisor's sentry (`pkg/sentry/syscalls/linux/sys_socket.go:RecvMMsg`, `012cf0b0b`)
takes the **userspace-reimplementation** approach:

- It loops `i in 0..vlen`, calling its own `recvSingleMsg` (the shared `recvmsg`
  path) for each `mmsghdr`, and copies each returned length back into
  `msg_len`.
- It honors the `timeout` argument by converting it to a deadline against
  `t.Kernel().MonotonicClock()` (`haveDeadline`/`deadline`), and also folds the
  socket's own `SO_RCVTIMEO` into that deadline; a negative socket timeout
  degrades to `MSG_DONTWAIT`.
- It validates `vlen` against `UIO_MAXIOV`, rejects unhandled flags with
  `EINVAL`, and returns the count of messages received (or the error only if
  zero were received) — exactly the kernel's partial-progress contract.

Hermit/Detcore takes the opposite approach: it does **not** reimplement the
syscall. It injects the *real* `recvmmsg` to the host kernel on a fd it has
temporarily switched to nonblocking, so the kernel fills the whole `mmsghdr`
array atomically and enforces the `UIO_MAXIOV`/flag/partial-progress contract
itself. Blocking and time are owned by the Detcore scheduler and virtual clock
rather than a sentry-level deadline, which is why the `recvmmsg` timeout
argument can be safely ignored (the fd never actually blocks in the kernel).

Consequences of the two designs:

- **Determinism source.** gVisor gets determinism from a single-threaded sentry
  and its own MonotonicClock; hermit gets it from serializing guest threads and
  replaying scheduler decisions + virtual time. Hermit therefore inherits the
  kernel's exact byte-level `recvmmsg` semantics for free, at the cost of a real
  host syscall per call.
- **Timeout.** gVisor implements the timeout precisely; hermit makes it a no-op
  because it converts the call to nonblocking + scheduler-driven retry. For a
  deterministic run the observable result (which datagrams arrive, in what
  order) is identical; only the wall-clock blocking behavior differs, and that
  is already virtualized.
- **Surface area.** gVisor must maintain per-syscall Go code (and its own
  `baseRecvFlags` allow-list); hermit's fix is a two-line classification +
  dispatch change reusing the datagram path shared with `recvmsg`/`sendmmsg`.

## Reproduction

See `metadata.json` for exact SHAs and toolchain. Build `cargo build --bin
hermit --bin rustbin_recvmmsg` in a `kvm-ratchet8` worktree, then run the
commands above. The gVisor source is `experiments/gvisor` at `012cf0b0b`.
