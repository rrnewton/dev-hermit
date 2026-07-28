---
name: dbi-no-runtime-tool-selection
description: "DBI backend has no runtime tool selection; the Tool is compiled into client.so, only handle_syscall_event is dispatched"
---

The reverie-dbi (DynamoRIO) backend has **no runtime tool selection**. The
native client (`libreverie_dbi_client.so`) links a `static PROTOTYPE_TOOL`
compiled in; `DbiRunner`/`hermit run --backend dbi` just shells
`drrun -c client.so`. Running any other Reverie `Tool` = editing the client's
Rust and rebuilding the `.so` (cross-repo: reverie-dbi is a pinned git dep).

The driver dispatches **only** `Tool::handle_syscall_event` (via the native
`pre_syscall` event). NOT dispatched: `subscriptions` (ignored; all syscalls
filtered), `handle_cpuid_event` (CPUID is emulated in C, bypassing Rust;
`has_cpuid_interception()` returns false), `handle_rdtsc_event`,
`handle_signal_event`, `handle_timer_event`, thread/exec/exit lifecycle.

The `DbiGuest<T>` handle itself is generic and real for syscall-driven tools:
`memory` (in-process LocalMemory), `tid`/`pid`, `regs`, `inject`, `thread_state`
work. As of DBI-M2 (PR rrnewton/reverie#32, 2026-07-22) `stack` and
`tail_inject` are ALSO implemented: `stack` = in-process heap arena `DbiStack`
(shared address space, addrs valid immediately); `tail_inject` = inject +
stash-result-in-thread-local + suspend (`future::pending().await`), driver
installs the result on `Poll::Pending` and drops the future. Still stubbed
(TODO-STUB(#31)): `set_timer*` (ENOSYS), `ppid` (always None — correct for
single-proc root), `read_clock` (branch count sampled at syscall entry).

**Why:** established by the Guest-trait audit + building working syscall-counter
and strace tools on DBI (tasks dbi-m1-guest-trait-audit, impl-dbi-simple-tools,
2026-07-22). Simple syscall-driven tools work by using `inject` (not
`tail_inject`) and process-global state (not GlobalState RPC, since DBI global
is `()`).

**How to apply:** for DBI tool work, edit reverie-dbi and rebuild the client;
don't expect reverie-examples tools to run on DBI as-is. Full audit:
`ai_docs/transient/20260722_dbi-guest-trait-audit.md`. Related:
[[dbi-client-must-be-release-built]], [[dbi-client-rev-e3e2c965-broken]].
