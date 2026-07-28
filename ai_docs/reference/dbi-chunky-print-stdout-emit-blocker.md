# DBI chunky_print port: blocked on a native stdout emit path

**Task:** impl-dbi-ratchet-5 (hermit-dbi). **Date:** 2026-07-26.
**Repo:** rrnewton/reverie, `reverie-dbi/`.

## Context / what already works

The DBI (DynamoRIO) observation-tool host in `reverie-dbi/src/tools.rs` runs a
matrix of example tools end-to-end. Re-validated this session on a **release**
client build (`PROFILE=release reverie-dbi/scripts/test-example-tools.sh`,
`DYNAMORIO_HOME=$HOME/<repo>/dynamorio/build`), on branch `impl-dbi-ratchet-4`
(reverie PR #161, base `origin/main` c4fccf0):

```
17/17 PASS: deferred-lifecycle-syscall, deferred-identity, clone-identity-handoff,
noop, strace, counter(histogram), counter1(GlobalState RPC), counter2(admission
+ exit lifecycle), + concurrent pthread and exit_group lifecycle variants.
All DBI example tools passed.
```

Tool selection is env-gated (`HERMIT_DBI_STRACE`, `_COUNTER1`, `_COUNTER2`, …)
and dispatched through `run_active_tool` → `run_tool_syscall`. A tool handler
that returns `Ok(v)` without `tail_inject` maps to
`DbiSyscallOutcome::Suppress(v)`; a `tail_inject(call)` maps to
`ExecuteOriginal`. So write-suppression semantics are available.

## The blocker

`chunky_print` (`reverie-examples/chunky_print.rs`; ported to sibling backends
in LiteInst #152) **suppresses** guest `write(1|2, …)` calls, buffers the bytes
in a `GlobalState`, and **re-emits** them to the real stdout/stderr later
(epoch flush + a final flush at process exit). Under ptrace the example does
this from the tracer process via `io::stdout()`.

Under DBI the client runs **in-process** with the guest. Re-emitting the
buffered bytes with an ordinary `write()` (libc / `io::stdout()`) is unsafe:

> client.c:135-136 — "the guest can close its stderr before exit, and
> **app-level writes re-enter the syscall interception path.**"

So a client-issued `write` would be re-intercepted by our own
`pre_syscall` filter (re-entrancy / possible recursion), and is also fragile if
the guest closed the fd. The existing diagnostic emitter avoids this by using
DynamoRIO's own I/O on a DR-private handle:

```c
static void reverie_dbi_emit(const char *buf, size_t len) {
  dr_write_file(diagnostic_file, buf, len);   // diagnostic_file = STDERR
}
```

That emitter is **stderr-only**. counter1/strace diagnostics ride it. There is
no stdout equivalent, so chunky_print cannot faithfully re-emit guest **stdout**
bytes.

## Proposed additive design (round-6)

Small, pattern-following, no per-syscall ABI change:

1. **Native (`client.c`)**: add
   `static void reverie_dbi_emit_stdout(const char *buf, size_t len) {
   dr_write_file(STDOUT, buf, len); }` — same re-entrancy-safe DR I/O path,
   `STDOUT` instead of the stderr `diagnostic_file`. Deliver its fn pointer to
   Rust **at init** via the `runtime_callbacks_t` struct consumed by
   `reverie_dbi_runtime_background_init` (currently a no-op that ignores its
   argument), NOT via the per-syscall `reverie_dbi_runtime_pre_syscall` ABI
   (that ABI is under `TODO-HUMAN-REVIEW(PR-154)` — leave it untouched).
2. **Rust (`tools.rs`)**: `set_stdout_emitter` analogous to `set_emitter`; a
   `ChunkyPrintGlobal` (`Mutex<Inner>`: per-tid printbuf, tick/epoch, stdout/
   stderr redirect flags) served in-process like `COUNTER1_GLOBAL`; a
   `ChunkyPrintTool` whose `handle_syscall_event` ticks each syscall, tracks
   `Dup2/Dup3` redirection to fd 1/2, suppresses `Write(1|2)` (read guest mem →
   buffer → `Ok(len)`), and flushes on `exit`/`exit_group`. Flush routes
   `Which::Stdout` → stdout emitter, `Which::Stderr` → existing stderr emitter.
3. **ABI-layout care**: matching the C `runtime_callbacks_t` to the Rust
   `DbiRuntimeCallbacks` (`#[repr(C)]`) exactly is the one real risk; add the
   field at the end and verify field order/types on both sides before building.
4. **Test**: extend `test-example-tools.sh` with a single-thread
   `bash -c 'printf hello'` guest under `HERMIT_DBI_CHUNKY_PRINT=1`; expect
   stdout == `hello` (suppressed then re-emitted at flush).

## Why not landed this round

The change is additive and low-risk in shape, but it touches the native client
ABI (init struct layout) on a backend where **a Rust panic aborts the process
and `catch_unwind` is dead**. It was scoped, not rushed to a commit, during a
fleet wind-down. The working example-tool matrix (PR #161) is the current
landable DBI artifact; this note is the concrete next step.
