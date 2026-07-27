# KVM ratchet 9: determinize select(2)

## Question

Following ratchet 7 (readv) and ratchet 8 (recvmmsg), what is the next
I/O-sibling gap where a syscall is Detcore-classified `Unsupported` (and so
fail-closes under `--strict`) while its sibling is already `Determinized`?
Fix it Detcore-side and validate.

## Answer

`select(2)`. It was `Unsupported` while its sibling `pselect6` and the
`poll`/`ppoll` family were already `Determinized`. Any guest issuing the raw
`select` syscall under `--strict` aborted with
"Sandbox container exited unexpectedly".

## Method

- Add `handle_select` / `handle_internal_select` in
  `detcore/src/syscalls/io.rs`, mirroring the mature `pselect6` deterministic
  poll loop and reusing the shared `fd_set` helpers.
- Reclassify `select` `Unsupported -> Determinized`
  (count guard `[205,91,77] -> [206,91,76]`) and wire the dispatch arm
  `Syscall::Select => handle_select`.
- Add `tests/rust/select.rs`, a regression guest exercising the ready path
  (a pipe with pending data) and the timeout path (an empty pipe). It issues
  the **raw** `select` syscall, because on x86-64 glibc's `select()` wrapper is
  implemented on top of `pselect6` and would otherwise never reach
  `handle_select`.

## Results

| Check | Result |
| --- | --- |
| `hermit run --strict --verify -- rustbin_select` (ptrace) | PASS (L2); DETLOG shows `select(...)=Ok(1)` ready, `select(...)=Ok(0)` timeout |
| `hermit run --backend=kvm --strict --verify -- rustbin_select` | BLOCKED: `select`=ENOSYS in the reverie-kvm executor |
| `cargo test -p detcore --lib` | 132 passed |
| `cargo fmt --all --check` | clean |
| `cargo clippy -p detcore --all-targets` | clean |

## select vs pselect6 (ABI details handled)

1. Timeout is `struct timeval` (sec + usec), not `timespec`;
   `select_timeout_duration` converts and range-checks `tv_usec`.
2. Linux writes the time-not-slept back into that `timeval` in place, so the
   probe timeout is a writable scratch cell (`stack.reserve`, re-zeroed each
   iteration to keep every probe a non-blocking poll) and completion writes the
   remaining time derived from deterministic virtual time.
3. `select` carries no signal mask, so pselect6's sigmask handling is dropped.

## gVisor comparison

gVisor ref `012cf0b0b14d7b8aa2a4424867ecec8e6121e69d`,
`pkg/sentry/syscalls/linux/sys_poll.go`.

gVisor's `Select` -> `doSelect` converts the fd sets to `pollfd`s and calls the
shared `pollBlock`. `pollBlock` **reimplements** the wait entirely in the sentry
(Go userspace): it registers `waiter` event queues on each sentry-internal file
object, then `t.BlockWithTimeout(ch, haveTimeout, timeout)` blocks on a Go
channel until a readiness notification arrives or the timeout — measured against
the sentry's own clock — elapses. Readiness is computed from gVisor's
virtualized file/socket objects (`file.Readiness(...)`); the host kernel never
sees a `select`. This gives gVisor an internally consistent event/time model,
but the *ordering and timing* still follow real notification arrival and the
sentry monotonic clock, so it is not run-to-run bitwise-reproducible.

Hermit/Detcore takes the opposite split. It does **not** reimplement `select`:
the real host kernel stays authoritative for fd readiness and ABI semantics.
`handle_internal_select` injects a **non-blocking** `select` probe (zeroed
`timeval`) against the real fds and turns the blocking/timeout into a
deterministic scheduler loop — each probe is one `InternalIOPolling` scheduler
turn, the timeout deadline is measured in scheduler-owned **virtual** time
(`thread_observe_time`), and the time-not-slept written back to the guest's
`timeval` is derived from virtual time. Determinism (L2, bitwise-identical
replay) comes from the scheduler + virtual clock; correctness of readiness and
ABI comes from the real kernel.

## KVM status

The fix is backend-agnostic, but the KVM path cannot exercise it yet: the
reverie-kvm executor (`reverie-kvm/src/executor.rs`) is an explicit syscall
if/else chain that falls through to `ENOSYS` for anything unlisted, and `select`
is unlisted — the same executor injection gap that blocks `pselect6`,
`socketpair`, and `recvmmsg`. Closing it is a separate cross-repo reverie change
(a reverie PR plus a hermit pin bump), not a Detcore classification issue.

## Reproduction

```bash
cargo build -p hermit --bin hermit -p hermetic_infra_hermit_tests --bin rustbin_select
target/debug/hermit run --strict --verify -- target/debug/rustbin_select
target/debug/hermit --log=info run -- target/debug/rustbin_select 2>&1 | grep 'select('
cargo test -p detcore --lib syscall_classification
```
