# KVM AF_UNIX SOCK_SEQPACKET socket parity ratchet

## Question

One ptrace-green corpus cell fails under the KVM backend (exit 1):
`c-programs/unix-autobind-seqpacket` (`hermit/tests/c/unix_autobind_seqpacket.c`).
It creates `socket(AF_UNIX, SOCK_SEQPACKET)`, binds with an autobind request
(bare family, `addrlen == offsetof(sun_path)`), then `getsockname` and checks the
abstract-name shape. Its `SOCK_STREAM` and `SOCK_DGRAM` siblings
(`unix-autobind-stream`, `unix-autobind-dgram`) both pass at KVM/ptrace parity.
Why does only SEQPACKET diverge, and can it be flipped cleanly?

## Method

Root-caused in `reverie-kvm/src/executor.rs`. The KVM `socket()` handler
allowlisted only `SOCK_DGRAM`/`SOCK_STREAM` for `AF_UNIX`/`AF_INET`/`AF_INET6`
(the `else` branch of the type check); `SOCK_SEQPACKET` fell through to
`negative_errno(libc::EPROTONOSUPPORT)`. The guest therefore failed at the very
first `socket()` call, before any bind/getsockname.

Golden behavior lives in detcore's `handle_socket`
(`hermit/detcore/src/syscalls/files.rs:1806`): it forwards **every** socket type
to the host via `record_or_replay` and lets the kernel validate the
`(family, type)` pair — the type is used only for nonblocking/cloexec
bookkeeping, never to reject a socket. So under the ptrace backend
`socket(AF_UNIX, SOCK_SEQPACKET)` succeeds; the KVM allowlist was an artificial
narrowing.

The `bind()` and `getsockname()` handlers in the KVM executor are type-agnostic
(they forward the raw sockaddr to the host), so `AF_UNIX SOCK_SEQPACKET` takes
the identical `socket -> bind(autobind) -> getsockname` path already proven by
the passing STREAM/DGRAM siblings.

Fix: add `SOCK_SEQPACKET` to the non-netlink allowlist. For `AF_INET`/`AF_INET6`
the host `libc::socket` call then rejects it with `EPROTONOSUPPORT` (no valid
INET SEQPACKET protocol), matching the ptrace forward-to-host result, so no new
nondeterministic surface is introduced. `AF_NETLINK` is untouched.

This is routine golden-ptrace parity on an already-dispatched syscall (widening
an accepted socket type, not new syscall support / core-abstraction / scheduling
change), so no `post-facto-human-review` label and no new audit-tag breadcrumb.

## Results

- Backend: KVM (reverie-kvm executor unit tests, real `/dev/kvm`, host devbig).
  Reverie-only ⇒ floored **L0**.
- `cargo test -p reverie-kvm` at reverie SHA `4ef93d5` — **155 pass / 1 fail**.
  The single failure `fcntl_pipe_capacity_get_and_set_forward_to_host` is
  **pre-existing and environmental**: it fails identically on the clean base
  `ef5ffeb` (verified on `HEAD~1`), because this container's pipe-buffer
  accounting rejects `F_SETPIPE_SZ` growth (`/proc/sys/fs/pipe-max-size` is 1 MiB
  but the syscall returns `-1`). It is unrelated to the socket-type change.
- New unit test `unix_seqpacket_socket_autobinds_like_stream_and_dgram` — pass.
  Drives `socket -> bind(autobind) -> getsockname -> close` for each of
  `SOCK_STREAM`/`SOCK_DGRAM`/`SOCK_SEQPACKET`, asserting the autobind shape
  (family `AF_UNIX`, length `offsetof(sun_path)+6`, leading NUL, five
  lowercase-hex bytes), and asserts an unsupported AF_UNIX type (`SOCK_RAW`)
  still returns `EPROTONOSUPPORT`.
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.

## Interpretation

This flips `unix-autobind-seqpacket` from KVM-fail to the same behavior as its
already-passing stream/dgram siblings. Unlike the SO_INCOMING_CPU ratchet
(PR #345), which is blocked by the AF_INET-loopback `connect` gap upstream of its
target syscall (see experiments/kvm_so_incoming_cpu_20260802), this cell uses
**only** AF_UNIX and never calls `connect`, so the socket-type widening is
sufficient on its own to close the divergence. Full-stack `hermit run --backend
kvm` of the cell is not asserted (debug-build KVM container boot is pathologically
slow; see memory kvm-fullstack-debug-boot-unusably-slow); this is the Reverie-side
prerequisite.

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-unix-seqpacket-socket @ 4ef93d5
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib unix_seqpacket_socket_autobinds_like_stream_and_dgram
# confirm the lone failure is pre-existing:
git switch --detach HEAD~1 && cargo test -p reverie-kvm --lib fcntl_pipe_capacity_get_and_set_forward_to_host
```

PR: https://github.com/rrnewton/reverie/pull/347
