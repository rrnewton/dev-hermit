# DBI M1: reverie-dbi `Guest` trait audit (method-by-method vs reverie-ptrace)

Status: audit / research. Author: agent `hermit-069`
(tasks `dbi-m1-guest-trait-audit` + `impl-dbi-simple-tools`). Grounded in reverie
rev **`e3e2c965`** (the rev pinned by hermit; see `hermit-cli/Cargo.toml`) and the
hermit frontier `344200e`. Empirically validated by running the prebuilt
PrototypeTool client (`/tmp/reverie-dbi-69f-target/.../libreverie_dbi_client.so`)
over `/bin/echo` under a source-built DynamoRIO (`branches=130548 syscalls=112
rewritten_writes=54`, exit 0).

Companion to `2026-07-22-20260722_dbi-reverie-interface-gap.md` (which frames the same gap
around *hosting Detcore*). This doc adds the two things that doc does not: a
**complete per-method Guest-trait table**, and a **per-tool feasibility verdict**
for the four simple tools in `impl-dbi-simple-tools`.

## The two layers you must separate

A Reverie backend has two halves. Auditing only the first is misleading:

1. **`Guest<T>` handle** — per-thread accessors a `Tool` calls *during* a handler
   (`memory`, `regs`, `inject`, `thread_state`, …). **reverie-dbi implements most
   of this.**
2. **The event driver** — the loop that reads `T::subscriptions` and *dispatches*
   `T`'s callbacks (`handle_syscall_event`, `handle_cpuid_event`,
   `handle_signal_event`, lifecycle, …) over the guest's life, owns
   `T::GlobalState`, and returns it. **reverie-dbi implements almost none of
   this, and hardcodes one tool.**

The `Guest` trait can look "mostly done" while the backend still cannot host an
arbitrary tool, because the driver is the missing half.

## Table 1 — `Guest<T>` trait, method by method (rev e3e2c965)

Source: `reverie/src/guest.rs` (trait), `reverie-dbi/src/lib.rs:67-207`
(`DbiGuest`), `reverie-ptrace/src/task.rs:2352-2500` (`TracedTask`).

Legend: ✅ real · ⚠️ present-but-degenerate/coarse · ❌ stub/panic/missing.

| Guest item | ptrace `TracedTask` | DBI `DbiGuest` | DBI status | What full impl needs on DBI |
|---|---|---|---|---|
| `type Memory` | `Stopped` (`/proc/pid/mem`) | `LocalMemory` (direct in-proc ptr r/w) | ✅ | done — in-process is *simpler* & faster than ptrace |
| `type Stack` | `GuestStack` (remote alloc) | `UnsupportedStack` | ❌ panics on `push`/`reserve` | allocate guest scratch stack via DR |
| `tid()` | ✅ stored | ✅ stored | ✅ | — |
| `pid()` | ✅ stored | ✅ stored | ✅ | — |
| `ppid()` | ✅ real (tracks tree) | ⚠️ field exists, **always built with `None`** (`lib.rs:725`) | ⚠️ degenerate | track process tree on clone/fork |
| `memory()` | ✅ | ✅ `LocalMemory::new()` | ✅ | — |
| `thread_state_mut()` | ✅ | ✅ | ✅ | — |
| `thread_state()` | ✅ | ✅ | ✅ | — |
| `regs()` | ✅ `getregs` | ✅ native `read_registers` (DR mcontext) | ✅ | — |
| `stack()` | ✅ `GuestStack` | ❌ returns `UnsupportedStack` | ❌ | see `type Stack` |
| `daemonize()` | ✅ reaper accounting | ⚠️ empty no-op | ⚠️ | daemon accounting if multi-task matters |
| `inject()` | ✅ `do_inject` | ✅ native `invoke_syscall` = `dr_invoke_syscall_as_app` | ✅ | — |
| `tail_inject()` | ✅ `do_tail_inject` | ❌ `panic!` (`lib.rs:193`) | ❌ | no-return syscall path (optional: `inject` covers most tools) |
| `set_timer()` | ✅ `Imprecise` RCB | ❌ `ENOSYS` (`lib.rs:196`) | ❌ | RCB threshold trap in DR |
| `set_timer_precise()` | ✅ `Precise`/instr | ❌ `ENOSYS` (`lib.rs:200`) | ❌ | RCB + single-step in DR |
| `read_clock()` | ✅ `timer.read_clock` (RCB) | ⚠️ returns `branch_count` **sampled at syscall entry** (`lib.rs:205`) | ⚠️ coarse | continuous RCB read |
| `is_main_thread()` (default) | ✅ | ✅ (tid==pid) | ✅ | — |
| `is_root_process()` (default) | ✅ | ⚠️ **always true** (ppid always None) | ⚠️ | fixed once `ppid` real |
| `is_root_thread()` (default) | ✅ | ⚠️ always true | ⚠️ | ditto |
| `auxv()` (default) | ✅ reads `/proc/pid/auxv` | ⚠️ works (in-proc pid) but returns **real host auxv**, unsanitized | ⚠️ | sanitize if needed |
| `inject_with_retry()` (default) | ✅ | ✅ (`inject` is real) | ✅ | — |
| `backtrace()` (default None) | ✅ overridden (libunwind) | ❌ default `None` | ❌ | in-process unwind is possible |
| `has_cpuid_interception()` (default false) | ✅ overridden (real flag) | ❌ default **false** | ❌ | CPUID is done in C, never surfaced to Rust (see below) |
| `GlobalRPC::send_rpc()` | ✅ `WrappedFrom`→`receive_rpc` (serializes in debug) | ✅ direct `global_state.receive_rpc` (in-proc, no serialize) | ⚠️ | works only single-address-space; cross-process needs a transport |
| `GlobalRPC::config()` | ✅ `&cfg` | ⚠️ `&self.config` but **always `&static ()`** (`lib.rs:660`) | ⚠️ | plumb real `T::Config` |

**Summary of Table 1:** 12 of the 16 required items are real (`Memory`, `tid`,
`pid`, `memory`, `thread_state[_mut]`, `regs`, `inject`, plus the working
`send_rpc`/`config` for a `()` global). The Guest *handle* is in surprisingly
good shape for syscall-driven tools. The hard-blocked items are `Stack`,
`tail_inject`, `set_timer*`, and (degenerate) `ppid`/`read_clock`.

## Table 2 — the event driver: which `Tool` callbacks are dispatched

Source: `reverie/src/tool.rs` (trait), `reverie-dbi/src/lib.rs:648-765`
(C-ABI entry points), `reverie-dbi/native/client.c` (DR event registrations).

| `Tool` callback | Dispatched on DBI? | Notes |
|---|---|---|
| `subscriptions` | ❌ ignored | `filter_syscall` returns `true` for all; not consulted |
| `new` | ❌ | `static PROTOTYPE_TOOL` is a compile-time constant |
| `init_thread_state` | ❌ | `reverie_dbi_runtime_thread_init` writes `Default`, never calls the tool |
| `handle_thread_start` | ❌ | — |
| `handle_syscall_event` | ✅ **only this** | native `pre_syscall` → `reverie_dbi_runtime_pre_syscall` → `PROTOTYPE_TOOL.handle_syscall_event` |
| `handle_post_exec` | ❌ | — |
| `handle_cpuid_event` | ❌ **bypassed** | CPUID trapped + emulated in **C** (`deterministic_cpuid` table, `client.c`); Rust tool never sees it |
| `handle_rdtsc_event` | ❌ | no rdtsc trap registered |
| `handle_signal_event` | ❌ | no signal event registered |
| `handle_timer_event` | ❌ | no RCB threshold trap (branch counter is sampled, never compared) |
| `on_exit_thread` | ❌ | thread-exit event only reads counters |
| `on_exit_process` | ❌ | exit event prints the summary line |

## Three structural facts that gate everything

1. **The tool is not selectable.** The client links a `static PROTOTYPE_TOOL:
   PrototypeTool` (`lib.rs:658`) compiled into `libreverie_dbi_client.so`. There
   is no runtime dispatch to an arbitrary `Tool`. "Running tool X on DBI" = *edit
   `PrototypeTool` (or add a monomorphized entry point) and rebuild the client*.
2. **e3e2c965 handlers may not suspend.** `run_ready` polls the handler future
   **once** with a noop waker and `panic!`s on `Poll::Pending` (`lib.rs:654`).
   Fine for synchronous tools (`inject`/`memory` resolve immediately); fatal for
   anything that awaits a real future. *(The newer reverie branch — e.g.
   `worktrees_reverie/slot77` — replaces `run_ready` with `block_on`, lifting
   this; but it still hardcodes `PROTOTYPE_TOOL` and still stubs
   `tail_inject`/`stack`/timers.)*
3. **`GlobalState` is hardwired to `()`.** `static GLOBAL_STATE: () = ()` and
   `static CONFIG: () = ()` (`lib.rs:659-660`). Any tool with a non-trivial
   global (Detcore) needs a real owner + cross-address-space RPC.

## Table 3 — the four simple tools: feasibility verdict

The `impl-dbi-simple-tools` progression, judged against the tables above.

| Tool | Feasible on DBI today? | Guest methods used (all ✅) | How / caveats |
|---|---|---|---|
| **1. Syscall counter (by nr)** | ✅ **Yes, now** | `thread_state_mut`, `inject` | Extend `PrototypeTool::handle_syscall_event` to bump a per-`Sysno` counter (per-thread `ThreadState` + a process-global map). The mechanism is *already proven* — `PrototypeCounters.observed_syscalls` / `TOTAL_SYSCALLS` count 112 syscalls for `echo`. Caveat: counts will **not** equal ptrace's — DR instruments a different process image (its own injected code) and vDSO-served calls trap in neither. Compare *shape/relative* counts, not exact equality. |
| **2. Strace-style tracer** | ✅ **Yes, now** | `inject` (retval), `memory` (ptr args), `regs` | The decoded `Syscall` already `Display`s as name+args (`reverie-syscalls`); log `format!("{}", syscall)`, call `inject`, log the retval. Emit via stderr/`dr_fprintf`. No new Guest capability needed. |
| **3. Time-pinning (clock_gettime/gettimeofday)** | ⚠️ **Yes for the syscall path; vDSO is the real gap** | `memory` (write result), (no `inject`) | Match `Syscall::ClockGettime`/`Gettimeofday`, write a deterministic `timespec`/`timeval` via `guest.memory().write_value`, return 0. The write-buffer-and-suppress pattern is already used for `sysinfo`/`getrusage`. **Caveat (not a Guest gap):** glibc serves these from the **vDSO** without a syscall, so the common fast path never traps under a syscall-only backend. Real determinism needs vDSO neutralization (out of scope for a "simple tool"). |
| **4. CPUID (via the Tool trait)** | ❌ **No, not via the Reverie Tool trait today** | would need `handle_cpuid_event` dispatch | CPUID *is* trapped and made deterministic — but **entirely in C** (`deterministic_cpuid` in `client.c`), bypassing `Tool::handle_cpuid_event`; `has_cpuid_interception()` returns `false`. A trait-based CPUID tool requires new plumbing: a C→Rust callback at the existing `rewrite_cpuid`/`emulate_cpuid` site that builds a `DbiGuest` and calls `T::handle_cpuid_event`, then writes eax/ebx/ecx/edx back. This is the first tool that forces work *beyond* `handle_syscall_event`. |

**Net for `impl-dbi-simple-tools`:** tools 1–3 validate the syscall path and need
no new Guest-trait capability — only edits to the hardcoded `PrototypeTool` plus
a client rebuild. Tool 4 is the natural forcing function to add a *second*
dispatched callback and would be the first real extension of the driver.

## Answer to the task's key question

> Does the DBI Guest implement enough of the Guest trait for simple tools to work?

**Yes — for syscall-driven tools (counter, strace, syscall-path time-pinning).**
The `Guest` handle exposes working `memory`, `regs`, `inject`, and
`thread_state`, which is the entire surface those tools touch, and the
end-to-end pipeline demonstrably runs. **Two important caveats:** (a) there is no
runtime tool selection — every "tool" is a compiled-in edit to `PrototypeTool` +
a client rebuild, not a pluggable `impl Tool`; and (b) any tool needing a
callback other than `handle_syscall_event` (CPUID, rdtsc, signals, timers,
lifecycle) hits the *event-driver* gap, not the Guest-trait gap. CPUID (tool 4)
is that boundary.

## Recommended build/validate path (for the impl task)

- Use the source-built DynamoRIO at
  `$HOME/work/dev-reverie/dynamorio/build/bin64/drrun` (prebuilt releases
  fail with `<ERROR: using undefined symbol!>`; see the companion doc).
- Client rev matters: build from `69f47d9`, **not** `e3e2c965` — the pinned rev's
  client SIGSEGVs on dynamic ELFs (see memory `dbi-client-rev-e3e2c965-broken`).
- ptrace baseline for comparison: `hermit run --backend ptrace -- <prog>` and/or
  real `strace -f -c` for the counter, real `strace -f` for the tracer.
