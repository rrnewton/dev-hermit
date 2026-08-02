# KVM AF_INET / AF_INET6 loopback connect lift

## Question

Every ptrace-green corpus cell that performs an `AF_INET`/`AF_INET6` loopback
connection fails under the KVM backend. The five such cells are
`so_incoming_cpu_tcp4`, `so_incoming_cpu_tcp6`, `tcp_info_accept4`,
`tcp_info_accept6`, and `tcp_info_client4` (`hermit/tests/c/`). Their sequence is
`socket → bind → getsockname → listen → socket → connect → accept → <target
getsockopt>`. Why do they all fail, and what is the minimal Reverie-side fix?

## Method

Root-caused in `reverie-kvm/src/executor.rs`:

- `connect` accepted **only** `AF_UNIX` and returned `EAFNOSUPPORT` for every
  other family. So an `AF_INET` client could be created and a server could
  `bind`/`listen`, but the loopback `connect(AF_INET)` failed before the cell
  reached its target syscall.
- `bind` omitted `AF_INET6` from its family allowlist
  (`AF_INET | AF_UNIX | AF_NETLINK`), so the IPv6 cells failed even earlier, at
  `bind`.

Golden behavior:

- `handle_connect` (`hermit/detcore/src/syscalls/io.rs:1138`) performs **no
  address rewriting**: it executes the call against the kernel and only records a
  best-effort loopback-peer SaBRe scheduling hint. The KVM executor is the
  host-execution layer for that same call.
- `handle_bind` (`hermit/detcore/src/syscalls/files.rs:2133` for AF_INET,
  `:2175` for AF_INET6) rewrites a port-zero bind to a deterministic port from
  the global `RequestPort` allocator and writes it back into guest memory
  **before** the syscall reaches the executor. So the guest-visible port is
  already deterministic; the executor never sees a raw host ephemeral port in the
  full stack.

Fix: forward `AF_INET`/`AF_INET6` `connect` to the host exactly like the
`AF_UNIX` connect and the `AF_INET` `bind`/`listen`/`getsockname` paths, and add
`AF_INET6` to the `bind` allowlist. This widens two already-dispatched handlers'
accepted address families (routine golden-ptrace parity), so no new
`AUTONOMOUS-BOT-IMPLEMENTED` audit tag and no `post-facto-human-review` trigger; a
`TODO-HUMAN-REVIEW(PR-349)` breadcrumb marks the new INET translation.

## Results

- Backend: KVM (`reverie-kvm` executor unit tests, real host sockets, host
  devbig). Reverie-only ⇒ floored **L0**.
- `cargo test -p reverie-kvm` at `703b1e76ac65dcb8c267c5c7778970a3c2d93fea` —
  **157 lib tests pass / 0 fail**, plus vmcall + integration suites green.
- New unit tests (all pass):
  - `inet_loopback_connect_completes_handshake` — full AF_INET loopback
    `socket → bind(127.0.0.1:0) → listen → getsockname → socket → connect →
    accept4 → sendto/recvfrom("ping")` round-trip via `ElfExecutor::execute`.
    `connect` returned `EAFNOSUPPORT` before the fix.
  - `inet6_loopback_connect_completes_handshake` — same over `::1`; also
    exercises the new AF_INET6 bind allowlist entry.
  - `connect_rejects_unsupported_address_family` — `AF_APPLETALK` still returns
    `EAFNOSUPPORT`.
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.

## Interpretation — corpus impact (honest accounting)

This lift is the **shared structural prerequisite** for all five INET-connect
cells but **flips 0 cells on its own**, because each has a *further* KVM gap
after `connect`:

- `so_incoming_cpu_tcp4` / `so_incoming_cpu_tcp6` also need the `SO_INCOMING_CPU`
  getsockopt arm from **PR #345** (verified **not** present in base `ef5ffeb` —
  `grep SO_INCOMING_CPU reverie-kvm/src` is empty). **This PR + #345 together**
  flip those two cells; tcp6 additionally required the AF_INET6 bind added here.
- `tcp_info_accept4` / `tcp_info_accept6` / `tcp_info_client4` also need a
  `getsockopt(SOL_TCP, TCP_INFO)` arm in the executor mirroring detcore's
  `canonicalize_tcp_info` (`hermit/detcore/src/syscalls/files.rs:95`), which
  zeroes the nondeterministic TCP_INFO fields (rtt, timestamps, retrans, …). That
  is a tractable follow-up (detcore already determinizes it), enabled by this PR.

`so_incoming_cpu_udp4`, `socket_cookie_*`, and `socket_timestamp_*` do **not**
call `connect` and are unaffected.

Full-stack `hermit run --backend kvm --verify` of the cells is not asserted here
(debug-build KVM container boot is pathologically slow in this environment; see
memory `kvm-fullstack-debug-boot-unusably-slow`). This is the Reverie-side
prerequisite.

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-inet-connect @ 703b1e7
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib -- \
  inet_loopback_connect_completes_handshake \
  inet6_loopback_connect_completes_handshake \
  connect_rejects_unsupported_address_family
# confirm SO_INCOMING_CPU (#345) is absent from the base:
git grep -n SO_INCOMING_CPU reverie-kvm/src   # empty at ef5ffeb
```

PR: https://github.com/rrnewton/reverie/pull/349
