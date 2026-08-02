# KVM SO_INCOMING_CPU → virtual CPU 0 parity ratchet

## Question

Three ptrace-green corpus cells fail under the KVM backend (exit 1):
`so_incoming_cpu_tcp4`, `so_incoming_cpu_tcp6`, `so_incoming_cpu_udp4`. Each
opens a loopback TCP/UDP connection and reads `getsockopt(SOL_SOCKET,
SO_INCOMING_CPU)`, asserting the value is `0` (`return 2` if wrong, `return 1`
if the syscall itself fails). Why does KVM diverge, and can it be flipped to
ptrace parity cleanly?

## Method

Root-caused in `reverie-kvm/src/executor.rs`. The KVM `getsockopt` handler
serviced only `SO_NETNS_COOKIE`, `SO_COOKIE`, and `SO_TYPE`; every other
`SOL_SOCKET` option fell through to `negative_errno(libc::ENOPROTOOPT)`. A guest
reading `SO_INCOMING_CPU` therefore got `ENOPROTOOPT` → the cell's `getsockopt`
call fails → `return 1`. The full TCP/UDP loopback path the cells exercise
(socket/bind/listen/connect/accept) already works under KVM — the sibling cell
`socket_cookie_tcp` exits 0 — so the *only* missing piece is this one option.

Golden behavior lives in detcore's reviewed `handle_getsockopt`
(`hermit/detcore/src/syscalls/files.rs:1995-2008`,
`TODO-HUMAN-REVIEW(PR-898)`): it forwards `SO_INCOMING_CPU` to the host, then —
because Hermit exposes a single virtual CPU and must not leak which host CPU
processed a socket's most recent packet — overwrites the returned CPU id with
`0` (truncated to the returned length).

Fix: add a `SO_INCOMING_CPU` arm to the KVM `getsockopt` handler that mirrors
this exactly — forward to the host `getsockopt` for the deterministic option
length, then write a canonical `0` CPU value bounded by
`capacity.min(length).min(4)` (never more than a 32-bit CPU id).

This is routine golden-ptrace parity on an already-dispatched syscall (a new
option name, not new syscall support, not a core-abstraction/scheduling change),
so no `post-facto-human-review` label. The arm carries the local file's
per-option breadcrumb convention (`// AUTONOMOUS-BOT-IMPLEMENTED` +
`// TODO-HUMAN-REVIEW(PR-345)`).

## Results

- `cargo test -p reverie-kvm` — **194/194 pass on real /dev/kvm** (host devbig)
  at reverie SHA `d6ac1843`. New unit test
  `getsockopt_so_incoming_cpu_is_canonical_zero` pre-loads a non-zero sentinel
  (`0x5a5a5a5a`) into the guest optval, drives `SYS_getsockopt` through the
  dispatch, and asserts the returned length is `sizeof(int)` and the value is
  `0`; it also asserts a null optlen pointer → `EFAULT`.
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.
- Assurance: Reverie-only ⇒ floored **L0**. Full-stack `hermit run --backend
  kvm` of the cells is not asserted (debug-build KVM container boot is
  pathologically slow; see memory kvm-fullstack-debug-boot-unusably-slow).

## Interpretation

**Correction (2026-08-02): this change flips 0 cells today.** The original
"+3 cells" claim was wrong. `so_incoming_cpu_tcp4.c` (and tcp6/udp4) run a full
AF_INET loopback — `socket(AF_INET)` → `bind` → `getsockname` → `listen` →
`socket(AF_INET)` → **`connect(client, AF_INET addr)`** → `accept` →
`getsockopt(SO_INCOMING_CPU)`. Under KVM, `connect` accepts only `AF_UNIX`
(`reverie-kvm/src/executor.rs:4810` returns `EAFNOSUPPORT` for any other
family), so the cell fails at `connect()` **before** reaching the new
`getsockopt(SO_INCOMING_CPU)` arm. The earlier "sibling `socket_cookie_tcp`
exits 0" evidence does not support the claim: `socket_cookie_tcp.c` only creates
two AF_INET sockets and reads `SO_COOKIE`; it never calls `connect`.

The added arm is still **correct golden parity** (mirrors detcore
`handle_getsockopt`, files.rs:1995-2008) and unit-tested, so it is a valid
*incremental* fix that removes one gap on the path — but it is
necessary-but-not-sufficient. The `so_incoming_cpu_*` cells stay KVM-fail until
KVM supports AF_INET loopback `connect` (a larger subsystem lift that also
blocks `tcp_info_*` and `socket_timestamp_*`). See the PR #345 correction
comment.

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-so-incoming-cpu @ d6ac1843
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib getsockopt_so_incoming_cpu_is_canonical_zero
```

PR: https://github.com/rrnewton/reverie/pull/345
