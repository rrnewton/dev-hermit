# SaBRe backend status (reverie-sabre)

Status: point-in-time snapshot. Author: agent `hermit-061`
(task `impl-sabre-cleanup-docs`). Read-only audit; no code changed.

Sources: `reverie/experimental/reverie-sabre/{ASSESSMENT.md,CAPABILITIES.md}`,
the crate source (`src/{lib,internal,tool,rpc,callbacks,signal,thread,vdso}.rs`),
`reverie/experimental/riptrace/` (the demo tool), and rrnewton/reverie **PR #1**
("Restore and stabilize experimental SaBRe backend",
`impl-sabre-runtime-stabilize-slot01`, open/draft as of 2026-07-22).

Related: `ai_docs/transient/address-space-architecture.md` (how SaBRe fits the
multi-address-space backend model), `ai_docs/sabre_backend_assessment.md`,
`ai_docs/sabre-determinism-analysis.md`.

---

## TL;DR

- **What it is:** `reverie-sabre` is the experimental **in-process / DBI** Reverie
  backend built on the MIT-licensed **SaBRe** selective binary-rewriting loader.
  The tool is compiled to a `cdylib` (`libriptrace_plugin.so`), loaded *into the
  guest process*, intercepts rewritten syscall sites, and calls a **synchronous**
  `reverie_sabre::Tool` callback with direct local guest memory. Process-global
  state lives out-of-process and is reached over a blocking Unix-socket RPC.
- **What works:** it builds, its unit tests pass (17 per ASSESSMENT.md / 20 per
  PR #1), and it runs the `riptrace` syscall-tracing demo end-to-end on
  dynamically linked x86-64 programs under a pinned SaBRe loader. Thread
  lifecycle, exit-timeout, and central signal handling were hardened in PR #1.
- **What doesn't:** it is **not** interchangeable with `reverie-ptrace`. It uses
  a *separate synchronous* tool API (not the shared async `reverie::Tool`), has
  no tool-facing registers/stack/inject/subscription/timer/PMU/CPUID surface, is
  x86-64 + dynamically-linked only, RPC is blocking and reserves guest fd 100,
  and it is explicitly **not yet a production isolation boundary**.
- **PR #1 status:** open **draft** against `rrnewton/reverie:main`; restores the
  runtime + host/RPC crates + riptrace, vendors the upstream plugin API, pins the
  loader, and adds a ptrace/SaBRe conformance gate. Shared Reverie core
  abstractions are unchanged.

---

## Architecture (address-space model)

```
   GUEST PROCESS (untrusted)                     ┃   HOST PROCESS (trusted)
 ┌──────────────────────────────────────────┐   ┃  ┌──────────────────────────────┐
 │ application code (rewritten by SaBRe)     │   ┃  │ riptrace host bin            │
 │   │ syscall site → detour                 │   ┃  │  ├─ GlobalState (Arc)         │
 │   ▼                                       │   ┃  │  └─ reverie_host::Server      │
 │ libriptrace_plugin.so  (the TOOL)         │   ┃  │        (reverie_rpc::Service) │
 │  ├─ reverie_sabre::Tool::syscall(&self,   │   ┃  │           ▲                   │
 │  │     Syscall, &LocalMemory)  [sync]     │   ┃  │           │                   │
 │  ├─ LocalMemory (direct, in-process)      │   ┃  │           │                   │
 │  └─ MyServiceClient ─ BaseChannel(fd 100) ─╂───┃───────────►┘  UnixListener      │
 │        (send/call, bincode)                │   ┃  │        ($REVERIE_SOCK)        │
 └──────────────────────────────────────────┘   ┃  └──────────────────────────────┘
```

- Entry point: SaBRe calls the plugin's `sbr_init<T>` (`src/internal.rs`); the
  tool is a `cdylib` (`crate-type = ["cdylib","rlib"]`, see `riptrace/tool`).
- Tool ↔ global state: `reverie-rpc` `Channel`/`Service` + `#[reverie_rpc::service]`
  codegen; the guest side (`src/rpc.rs`) connects via `$REVERIE_SOCK`, `dup3`s the
  socket onto **fd 100**, and does length-prefixed bincode `send`/`call`. Host
  side is `reverie-host::Server` (a `UnixListener` accept loop).
- This is the concrete realization of Reverie's "tool in a separate address
  space from global state" design — see the address-space doc.

---

## What works (verified / claimed)

| Area | State |
| --- | --- |
| Build & unit tests | Builds on the pinned nightly; library tests pass (17 in ASSESSMENT.md; PR #1 test plan reports **20 passed** via `cargo test -p reverie-sabre`). |
| Loader | Upstream SaBRe pinned in `SABRE_UPSTREAM.toml` (commit `05816ee0…`); builds with CMake/Make/GCC; its smoke tests and 72 supported upstream tests pass after test-only portability fixes (3 host-dependent unsupported). |
| Demo | `riptrace` host launches the pinned loader, serves global-state RPC, propagates guest exit status. `/bin/true` and `/bin/echo` run end-to-end (echo produced output + an 86-line syscall trace). `exec`, a fork/wait workload, and nonzero exit-status propagation exercised. |
| Syscalls | Intercepts rewritten syscall instructions → synchronous in-process `Tool::syscall` callback (default impl performs the real syscall). |
| Guest memory | Direct `LocalMemory` access (no remote memory/register API needed — the tool runs in the guest). |
| Threads | Backend records created lazily on first observation; start/exit callbacks emitted at most once; 128× pthread create/return/join covered by the conformance gate. |
| Process exit | `exit_group` stops new thread records, requests exit on tracked threads, waits for exit callbacks; configurable timeout (PR #1 honors timeout direction and retries `exit_all` races). |
| Signals | Central handlers mediate standard catchable signals; guest `rt_sigaction` virtualized so the central handler stays installed; SIGINT/SIGTERM/SIGCHLD delivery covered by the gate; SIGCHLD keeps children waitable under `SIG_DFL`; per-thread exclusion sequencer with a bounded queue that coalesces on overflow (no panic in signal context). |
| Timing/detours | RDTSC callbacks, selected VDSO callbacks (`clock_gettime`, `getcpu`), macro-generated function detours (e.g. libc `malloc`/`free` in riptrace). |
| Global state | Synchronous generated RPC client → out-of-process host service. |
| Conformance gate | `conformance/run.sh` compiles `thread_lifecycle` (128 pthread cycles) and `signal_forwarding` and runs each under BOTH ptrace `counter2` and SaBRe `riptrace`; PR #1 reports all four passing. |

---

## What does not work / limitations

- **Separate tool API.** Uses a bespoke *synchronous* `reverie_sabre::Tool`
  (`src/tool.rs`), **not** the shared async `reverie::Tool`/`Guest`. Existing
  ptrace tools (e.g. Detcore) **cannot** switch to this backend by recompiling.
- **No parity surface.** No tool-facing register, stack, remote-inject,
  subscription, CPUID, timer, or PMU/RCB interface comparable to
  `reverie-ptrace`. Signals can be *observed* but not replaced/suppressed/
  redirected through a shared backend-neutral contract.
- **Signal fidelity is not kernel-exact.** Handler masks, `SA_NODEFER`,
  `SA_RESETHAND`, alternate stacks, and the original `ucontext_t` are not
  reproduced; `SA_SIGINFO` handlers get siginfo but a null context. Realtime
  signal ordering/payloads not implemented; standard-signal overflow may be
  coalesced at the 64-entry deferred queue. Synchronous faults (SIGILL/SIGSEGV)
  and SIGKILL/SIGSTOP are not centrally mediated; SIGSTKFLT is reserved as the
  runtime's controlled-exit signal.
- **Platform/target limits.** x86-64 only; dynamically-linked guests only
  (upstream SaBRe cannot handle static executables); only the pinned loader
  revision is validated. `execveat`, broad clone/vfork/exec stress, and loader
  distribution are unsupported/unverified.
- **RPC constraints.** Blocking; reserves guest **fd 100**; injected-process
  trace formatting may allocate. The socket path is currently discovered via the
  `$REVERIE_SOCK` env var (a `rpc.rs` FIXME notes this should instead be a
  seccomp-unotify handshake).
- **Not an isolation boundary yet.** ASSESSMENT.md flags documented correctness
  concerns around exec synchronization and thread-state initialization; it
  "should not yet be treated as a production isolation boundary."
- **Distribution.** The SaBRe loader is built/shipped separately; Cargo only
  builds the Reverie plugin + host command.

---

## reverie PR #1 summary

- **Title/branch:** "Restore and stabilize experimental SaBRe backend" /
  `impl-sabre-runtime-stabilize-slot01`; base `main`; **open, draft**.
- **Scope:** restores the in-process SaBRe runtime + host/RPC crates + riptrace
  demo; vendors upstream plugin API files; pins the validated loader; hardens
  exact-once thread lifecycle and exit-timeout behavior; virtualizes guest
  `rt_sigaction` while preserving central SIGINT/SIGTERM/SIGCHLD; adds shared
  ptrace/SaBRe conformance workloads; documents state in `CAPABILITIES.md`.
- **Test plan (as reported in the PR):** `cargo fmt --all -- --check`;
  `cargo test -p reverie-sabre` → 20 passed; conformance `run.sh all` → ptrace
  thread lifecycle (129 threads), SaBRe thread lifecycle, ptrace signal
  forwarding, SaBRe signal forwarding all passed.
- **Invariant:** shared Reverie core abstractions unchanged.

---

## Assessment / recommended next steps

1. **API convergence is the central gap.** The highest-value work to make SaBRe
   a real alternative backend is bridging its synchronous tool API to the shared
   async `reverie::Tool`/`Guest` contract (so Detcore could be hosted in-guest).
   That requires an async executor + suspension inside the injected `.so`, or an
   accepted two-API split. See the address-space doc's gap analysis.
2. **Harden before any isolation claims.** Resolve the documented exec-sync and
   thread-state-init concerns; broaden clone/vfork/exec and signal regression
   coverage beyond the two conformance workloads.
3. **RPC handshake.** Replace the `$REVERIE_SOCK` env-var discovery with the
   seccomp-unotify fd handshake noted in `rpc.rs`, and reconsider reserving fd
   100.
4. **Land PR #1** (currently draft) to make the restored runtime the baseline,
   then track the above as follow-ups.
