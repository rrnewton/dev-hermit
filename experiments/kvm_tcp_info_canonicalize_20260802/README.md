# KVM TCP_INFO canonicalization in getsockopt

## Question

The three ptrace-green corpus cells `tcp_info_accept4`, `tcp_info_accept6`, and
`tcp_info_client4` (`hermit/tests/c/`) each do
`socket → bind → listen → connect → accept → getsockopt(SOL_TCP, TCP_INFO)`.
The AF_INET/AF_INET6 connect lift (reverie#349) unblocks the `connect` step; what
remains for these cells on the KVM backend is the `getsockopt(SOL_TCP, TCP_INFO)`
target itself, which the KVM executor did not handle. What is the minimal
Reverie-side fix, and does it faithfully match the golden ptrace behavior?

## Method

Root-caused in `reverie-kvm/src/executor.rs::getsockopt`: the function rejects any
`level != SOL_SOCKET` with `ENOPROTOOPT`, so `getsockopt(IPPROTO_TCP, TCP_INFO)`
was never serviced.

Golden behavior — detcore `handle_getsockopt`
(`hermit/detcore/src/syscalls/files.rs:2009-2020`) forwards the call to the
kernel via `record_or_replay`, then runs the private helper
`canonicalize_tcp_info` (`files.rs:95`) over the returned bytes. That helper
retains only the bytes at offsets `0, 1, 5, 6` — `tcpi_state`, `tcpi_ca_state`,
`tcpi_options`, and the packed `tcpi_snd_wscale/tcpi_rcv_wscale` nibbles — and
zeroes everything else, hiding host RTT, delivery/pacing rate, segment/byte
counters, retransmit counts, and timestamps.

Fix: add an `IPPROTO_TCP`/`TCP_INFO` arm to the KVM executor `getsockopt` that
forwards to the host fd via `libc::getsockopt`, then applies a module-local
`canonicalize_tcp_info` — a byte-for-byte port of detcore's helper — before
copying the result into guest memory and writing back the returned length. This
implements the *already-established* detcore determinization strategy on a second
backend (routine golden-ptrace parity): not a new syscall (`getsockopt` is
already dispatched) and not a new determinization strategy, so it carries a single
`TODO-HUMAN-REVIEW(PR-350)` breadcrumb, no `AUTONOMOUS-BOT-IMPLEMENTED` tag, and
no `post-facto-human-review` label.

## Results

- Backend: **KVM** (`reverie-kvm` executor unit tests, real host TCP loopback
  sockets, host devbig). Reverie-only ⇒ floored **L0**.
- `cargo test -p reverie-kvm` at `44a456490818276ea6ae10469ef05a2cc54b1bc9` —
  **158 lib tests pass / 0 fail** (was 157 at #349's head; +1 new test), plus
  `vmcall` (6) and integration suites green; doc-tests 0.
- New unit test `getsockopt_tcp_info_is_canonicalized` (passes): establishes a
  live `AF_INET` loopback connection (`socket → bind(127.0.0.1:0) → listen →
  getsockname → socket → connect → accept4`), calls
  `getsockopt(IPPROTO_TCP, TCP_INFO)` on the connected client fd, then asserts:
  - `tcpi_state` (offset 0) `== TCP_ESTABLISHED (1)` is **retained**, proving a
    real host `tcp_info` was read (not an error-zeroed buffer);
  - every byte outside `{0, 1, 5, 6}` is zeroed (canonicalization applied).
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.

## Interpretation — corpus impact (honest accounting)

This is the **second half** of the fix for the three `tcp_info_*` cells; it
**flips 0 cells on its own**. Those cells first failed at `connect` (fixed by
reverie#349) and would then have diverged at the unhandled `TCP_INFO` getsockopt
(fixed here). **#349 + #350 together** are the Reverie-side prerequisites for
`tcp_info_accept4`, `tcp_info_accept6`, and `tcp_info_client4`.

Full-stack `hermit run --backend kvm --verify` of the cells is not asserted here
(debug-build KVM container boot is pathologically slow in this environment; see
memory `kvm-fullstack-debug-boot-unusably-slow`). This is the executor-layer
parity fix; the integrated cell flip must be confirmed by the coordinator in a
KVM-capable, release-built environment after both PRs land.

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-tcp-info-canonicalize @ 44a4564
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib -- getsockopt_tcp_info_is_canonicalized
# confirm the golden helper this mirrors:
sed -n '92,101p' ../hermit/detcore/src/syscalls/files.rs
```

PR: https://github.com/rrnewton/reverie/pull/350 (stacked on #349)
