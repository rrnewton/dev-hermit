# KVM masked-ppoll ready-descriptor parity ratchet

## Question

The ptrace-green corpus cell `ppoll_readv` fails under the KVM backend (exit 1).
Its first step writes data into a self-pipe, then calls
`ppoll(&pfd, 1, &timeout, &mask)` with a **non-null** signal mask (blocking
SIGUSR1) on the now-readable fd, asserting the return is `1` with `POLLIN` set.
Why does KVM diverge, and can it be flipped to ptrace parity without breaking the
sibling cell `ppoll_simulation`, which deliberately expects a masked *blocking*
`ppoll` to fail with `ENOSYS`?

## Method

Root-caused in `reverie-kvm/src/executor.rs` (`fn ppoll`). The handler rejected
**any** non-null signal mask up front:

```rust
if args[3] != 0 {
    if args[4] != KERNEL_SIGSET_SIZE as u64 { return negative_errno(libc::EINVAL); }
    return negative_errno(libc::ENOSYS);   // <-- unconditional
}
```

So a masked `ppoll` on an already-ready descriptor returned `ENOSYS` before ever
polling, and `ppoll_readv` step 1 failed.

Golden behavior lives in detcore's reviewed `handle_internal_ppoll`
(`hermit/detcore/src/syscalls/io.rs:922-939`, `TODO-HUMAN-REVIEW(PR-273)`):

```rust
// A zero probe can honor a temporary signal mask atomically. Keeping that mask
// active while parked would require scheduler-level pending-signal state, so fail
// closed rather than letting a masked signal interrupt a simulated wait.
if call.sigmask().is_some() {
    let (probe, _guard) = self.prepare_ppoll_probe(guest, call).await?; // timeout = 0
    let result = guest.inject_with_retry(probe).await;
    if probe.syscall_would_have_blocked(result) { return Err(Errno::ENOSYS.into()); }
    ...
    return result;
}
```

detcore services a masked `ppoll` as a **zero-timeout probe**: if a descriptor is
ready it returns the count; if it would block it fails closed with `ENOSYS`.

Fix: for a non-null (valid-size) mask, do a non-blocking poll
(`poll_with_timeout(.., 0)`) and return `ENOSYS` only when the result is `0`
(nothing ready); otherwise return the count / propagate any error. Because an
instantaneous poll never parks, the signal mask cannot affect its outcome, so a
plain non-blocking poll reproduces the kernel result without applying the mask.
Timeout validation was also hoisted above the mask branch to match detcore's
`handle_ppoll` ordering (malformed timeout → `EINVAL` regardless of mask).

This is a refinement of the already-dispatched `SYS_ppoll` handler (not new
syscall support, not a core-abstraction/scheduling change), so no
`post-facto-human-review` label; the handler's existing
`TODO-HUMAN-REVIEW(PR-172)` marker is retained.

## Results

- `cargo test -p reverie-kvm` — **194/194 pass on real /dev/kvm** (host devbig)
  at reverie SHA `35971125` (155 lib including the new test). New test
  `ppoll_masked_ready_returns_count_and_blocking_fails_closed` covers: masked
  ready descriptor → `1` (POLLIN); masked would-block → `ENOSYS`; wrong sigset
  size → `EINVAL`; malformed timeout with a mask → `EINVAL`; unmasked timeout-0
  path → `0` (never `ENOSYS`).
- `cargo fmt -p reverie-kvm -- --check` and `cargo clippy -p reverie-kvm
  --all-targets` — clean.
- Assurance: Reverie-only => floored **L0**. Full-stack `hermit run --backend
  kvm` of the cell is not asserted (debug-build KVM container boot is
  pathologically slow; see memory kvm-fullstack-debug-boot-unusably-slow).

## Interpretation

Expected parity effect once landed + reverie pin bumped: **+1 cell**
(`ppoll_readv`) flips to KVM/ptrace parity. The sibling `ppoll_simulation`
masked-blocking `ENOSYS` contract is preserved by construction (a drained pipe
still yields `0` from the non-blocking poll → `ENOSYS`); that cell remains a KVM
gap for unrelated reasons (masked-path is fine, but it also needs ppoll timeout
write-back and cross-thread scheduling).

## Reproduction

```bash
cd worktrees/kvm/reverie   # branch codex/kvm-ppoll-masked-ready @ 35971125
cargo test -p reverie-kvm
cargo test -p reverie-kvm --lib ppoll_masked_ready_returns_count_and_blocking_fails_closed
```

PR: https://github.com/rrnewton/reverie/pull/346
