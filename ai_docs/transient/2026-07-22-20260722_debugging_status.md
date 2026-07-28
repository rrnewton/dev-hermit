# Hermit Debugging Support: Current-State Audit

- Status: audit of the current debugging frontier (what works, what is broken,
  what is next)
- Date: 2026-07-22
- Method: source inspection of `hermit-cli/`, `detcore-model/`, and the pinned
  `reverie-ptrace` gdbstub, plus live end-to-end tests on this host and a
  literature/ecosystem scan of debugger MCP servers.
- Companion document: `docs/AGENT_DEBUGGING_VISION.md` is the forward-looking
  design proposal (a reproducible-evidence MCP server). This document is the
  concrete "where are we today" audit that it should be read against.

## TL;DR

- **GDB remote protocol works.** Hermit ships a hand-rolled GDB Remote Serial
  Protocol (RSP) server inside `reverie-ptrace` and wires it into the CLI. A
  record → replay → GDB session was verified end-to-end on this host:
  breakpoints, backtrace, argument/variable inspection, `finish`, and
  continue-to-exit all functioned.
- **There is a session-crashing bug.** Issuing `continue` after `finish` (or
  any resume where GDB sends a plain `Continue` while the stub is waiting to
  step over a breakpoint) panics the entire replay process at
  `reverie-ptrace/src/task.rs:1815` — `unexpected resume action Continue(None),
  expecting: StepOver`.
- **`hermit run --gdbserver` is effectively unreachable from the host** because
  the guest runs in an unshared network namespace; the listening socket lives
  inside that namespace. The working path is `hermit replay`, which launches the
  GDB client itself.
- **LLDB does not work yet.** LLDB can open a `gdb-remote` connection to the
  stub, but the session hangs and never reaches a usable stop. The stub is
  GDB-tuned and omits the LLDB handshake packets (`qHostInfo`, `qProcessInfo`,
  `qRegisterInfo`, `jThreadsInfo`, thread-suffix support).
- **Reverse / time-travel debugging is dormant.** The protocol layer has a
  `ReplayLog::{Begin,End}` notion and emits `replaylog:begin/end`, but
  `ReverseStep`/`ReverseContinue` are **not** advertised in `qSupported`, so no
  client is offered reverse execution.
- **Fastest path to agent-driven debugging:** fix the `finish`→`continue`
  panic, then wrap the already-working `record`/`replay --gdbserver` path with a
  GDB/MI-backed MCP layer (off-the-shelf GDB-MCP for a prototype; hermit-native
  per the vision doc for the real product). LLDB support and LLDB's own built-in
  MCP become available "for free" once the stub speaks LLDB's gdb-remote
  dialect.

## 1. Current State Audit

### 1.1 Does Hermit support the GDB remote protocol? Yes.

The GDB server is implemented in the pinned Reverie checkout, not in Hermit
itself:

- `reverie/reverie-ptrace/src/gdbstub/` — a from-scratch GDB Remote Serial
  Protocol server (async, Tokio-based). Modules: `server.rs`, `session.rs`,
  `commands/`, `packet.rs`, `request.rs`, `response.rs`, `breakpoint.rs`,
  `inferior.rs`, `regs/` (amd64 + aarch64).
- Command coverage (`commands/base/`, ~30 packets): `c`, `s`, `vCont`, `g`/`G`,
  `p`/`P`, `m`/`M`, `X`, `z`/`Z` (breakpoints), `H`, `T`, `k`, `vKill`, `D`
  (detach), `qSupported`, `qXfer` (features + auxv), `qfThreadInfo`/
  `qsThreadInfo`, `qC`, `qAttached`, `QStartNoAckMode`, `QThreadEvents`,
  `vFile` (remote file I/O), `?`.
- Extended mode (`commands/extended_mode/`): `!`, `vRun`, `vAttach`, `r`
  (restart), `QDisableRandomization`, `QEnvironment*`, `QSetWorkingDir`,
  `QStartupWithShell`.

Feature negotiation. The `qSupported` reply
(`reverie-ptrace/src/gdbstub/session.rs:315`) advertises:

```
PacketSize=8000;vContSupported+;multiprocess+;exec-events+;fork-events+;
vfork-events+;QThreadEvents+;QStartNoAckMode+;swbreak+;
qXfer:features:read+;qXfer:auxv:read+;
```

Notably **absent**: `ReverseStep+`, `ReverseContinue+`, `hwbreak+`,
`qXfer:libraries*`, and any LLDB-specific extensions. So reverse execution and
hardware breakpoints are not offered, and library discovery relies on the
client reading `/proc`-style paths over `vFile`.

### 1.2 CLI / config surface

| Surface | Location | Meaning |
| --- | --- | --- |
| `--gdbserver` | `detcore-model/src/config.rs:193` (Detcore config flag) | Start the gdbserver; disabled by default. |
| `--gdbserver-port <uint16>` | `detcore-model/src/config.rs:201` | Port to listen on. **Default `1234`.** |
| `hermit run --gdbserver [--gdbserver-port]` | `hermit-cli/src/lib.rs:82,104` | Live run with a server attached; the tracer starts stopped and waits for a client. |
| `hermit replay [--gdbserver-port] [--gdbex CMD]` | `hermit-cli/src/bin/hermit/replay.rs:36-47` | Replay a recording under a server; Hermit **spawns the `gdb` client itself** and forwards `-ex CMD` via repeated `--gdbex`. |
| `hermit record start --verify-with-gdbex CMD[;CMD...]` | `hermit-cli/src/bin/hermit/record_start.rs:54` | After recording, verify by replaying under gdbserver and running the given GDB commands. |
| `hermit::replay_with_gdbserver(dir, port)` | `hermit-cli/src/lib.rs:301` | Library entry point used by both paths. |

The client is **hard-coded to `gdb`** in `replay.rs:75` and
`record_start.rs:184` (`std::process::Command::new("gdb")`). There is no
`--debugger`/LLDB selection flag and no "server-only, no client" replay mode.

### 1.3 Was there a VSCode / DAP debugging demo? Not found.

No Debug Adapter Protocol (DAP) support, no `.vscode/launch.json`, and no
IDE-integration demo exist in the tree. Integration is purely the raw GDB RSP
server plus a spawned `gdb` client.

### 1.4 LLDB support and the GDB-protocol bridge

LLDB is installed here (`Meta lldb version 23.6.7`) and, like all LLDB builds,
can attach to a gdb-remote server (`gdb-remote host:port` /
`process connect connect://host:port`). In principle GDB and LLDB can share the
same wire protocol.

In practice, **LLDB does not currently work against Hermit's stub.** In a live
test LLDB opened the TCP connection but the session hung indefinitely and never
reached a usable stopped state (it did not even respond to `SIGTERM`). Root
cause is a protocol-dialect gap, not a transport problem:

- The stub advertises only the GDB feature set above.
- It does **not** implement the packets LLDB relies on during its handshake —
  `qHostInfo`, `qProcessInfo`, `qRegisterInfo<N>`, `qMemoryRegionInfo`,
  `jThreadsInfo`, `QThreadSuffixSupported`, `QListThreadsInStopReply`.
- Unknown packets are answered with a valid empty packet (`$#00`, via
  `session.rs:671` → `response.rs::finish`), which is the correct "unsupported"
  reply — so the hang is from LLDB missing a handshake element it needs (and/or
  the live-run scheduler interaction below), not from the server going silent.

Precise packet-level triage (an LLDB `log enable gdb-remote packets` capture)
could not be completed in this sandbox because reaching the run-mode server
requires entering the guest network namespace with elevated privileges. That
capture is the first concrete follow-up for LLDB work.

### 1.5 Existing tests

- `reverie/tests/gdbserver-integration/` — the real coverage. A
  `gdbserver-helper` binary drives a `RemoteGdbSession` (spawns the reverie
  gdbserver over a Unix socket + a `gdb` client, compares stdout/stderr/exit)
  and has `#[tokio::test]` cases: `debug_ls_b_main_detach`,
  `debug_ls_with_b_main_kill`, `debug_ls_with_b_main_continue`,
  `debug_uname_with_b_main_continue`, `debug_file_does_not_exist_...`. Test C
  guests live under `test-src/` (threads, fork/exec, nested, openat).
- `hermit-cli/tests/cli.rs:130` — only asserts that `--gdbserver-port` appears
  in `--help`.
- `detcore/tests/testutils/src/lib.rs` — sets `gdbserver: false` defaults; no
  gdb behavior is exercised.

There are **no Hermit-level end-to-end debugging tests**; the behavioral tests
live at the Reverie layer and depend on a system `gdb`.

### 1.6 What actually works vs. broken (verified live on this host)

Setup: `gcc -g -O0` program with `main → helper(21) → return x*2`; recorded with
`hermit record start`, replayed with `hermit replay <id> --gdbex ...`.

**Works (clean end-to-end GDB session):**

```
Breakpoint 1, helper (x=21) at hbgtest.c:2
#0  helper (x=21) at hbgtest.c:2
#1  0x...  in main () at hbgtest.c:3
info args → x = 21
p x       → $1 = 21
finish    → Value returned is $2 = 42
continue  → y=42 ; [Inferior 1 exited normally]      (when 'finish' is NOT
                                                       followed by 'continue')
```

Symbol loading, pending breakpoints, remote file transfer (`vFile`), backtrace,
argument/local inspection, `finish`, and continue-to-normal-exit all function.

**Broken / rough edges:**

1. **`finish` then `continue` panics the whole session (correctness bug).**
   ```
   thread 'main' panicked at reverie-ptrace/src/task.rs:1815:
   [pid = 3] unexpected resume action Continue(None), expecting: StepOver
   ...
   panic in a function that cannot unwind → Remote connection closed
   ```
   Mechanism: resuming from a software breakpoint calls
   `await_gdb_resume(task, ExpectedGdbResume::StepOver)` (`task.rs:1917`),
   expecting the client to single-step over the breakpoint. `handle_gdb_resume`
   (`task.rs:1801`) only accepts `Step` (or `Continue` when the expectation is
   `Resume`/`StepOnly`); any other action hits `panic!` at `task.rs:1815`. When
   GDB issues a plain `vCont;c` after `finish`, the stub aborts instead of
   handling it. This crashes the deterministic replay, not just the debug view.

2. **`hermit run --gdbserver` is unreachable from the host.** The guest runs in
   an unshared network namespace (verified: guest `net:[…6477]` ≠ host
   `net:[…1840]`), so the `127.0.0.1:<port>` listener is inside the container.
   A host-side client times out. Only `hermit replay` works out of the box,
   because it spawns the GDB client from Hermit itself before entering the
   container. Reaching the run-mode server requires `nsenter -t <guest> -n`.

3. **Host GDB auto-load crash (toolchain, not Hermit).** The system `gdb 9.1`
   (fb build) hit `stap-probe.c:1293: internal-error: sect_index_data not
   initialized` while auto-loading libc SystemTap probes on `continue`.
   Workaround that produced clean sessions: prepend `--gdbex "set auto-load
   off"` and `--gdbex "set sysroot /"`. Worth documenting for users, and an
   argument for testing against LLDB or a newer GDB.

4. **CPUID faulting unavailable on this host (VM limitation).** Replays log
   `WARN reverie_ptrace::task: Unable to intercept CPUID: Underlying hardware
   does not support CPUID faulting`. Non-fatal here but a determinism caveat on
   restricted hosts (see the hardware notes in `AGENTS.md`).

## 2. Architecture Assessment

### 2.1 How a debugger attaches (data flow)

```
GDB / LLDB client
      |  GDB Remote Serial Protocol over TCP :port  (127.0.0.1)
      |  (replay: client spawned by Hermit; run: inside guest net namespace)
      v
reverie-ptrace  gdbstub::GdbServer   (async Tokio; server.rs / session.rs)
      |  per-inferior request channel: SetBreakpoint, ReadRegisters,
      |  ReadMemory, Resume, Step, ...   (request.rs / commands/mod.rs)
      v
per-task control  handle_gdb_resume / await_gdb_resume   (task.rs)
      |  ExpectedGdbResume::{Resume, StepOnly, StepOver}
      v
safeptrace (ptrace)  Stopped/Running state machine
      |
      v
Detcore scheduler  (guest threads serialized; TracerBuilder::
      |             sequentialized_guest() avoids server/scheduler deadlock)
      v
Linux kernel
```

Key wiring points:

- `reverie-ptrace/src/tracer.rs`: `TracerBuilder::gdbserver(conn)` and
  `sequentialized_guest()`. `GdbConnection` accepts a `SocketAddr` or a Unix
  socket `PathBuf`; `u16` → `127.0.0.1:port`. The tracer "starts stopped and
  waits for a connection" so the client can observe the whole execution.
- `hermit-cli/src/replay.rs:99`: on replay, when a gdbserver is requested the
  builder is told the guest is **not** sequentialized in the usual way (comment:
  "Inform gdbserver not to serialize guests because this is [replay]"), which is
  the opposite of the live-run assumption and a likely factor in the LLDB hang
  differing between run and replay.

### 2.2 Can GDB and LLDB share one protocol? Yes, with additions.

LLDB speaks gdb-remote, so a single RSP server can serve both — but only if it
also answers LLDB's handshake packets. Today the stub is GDB-only in practice.
Adding `qHostInfo`, `qProcessInfo`, `qRegisterInfo<N>` (or a complete
`qXfer:features:read` target description LLDB accepts), `qMemoryRegionInfo`, and
thread-suffix/`jThreadsInfo` support would make the same server drive both
clients, and would unlock **LLDB's own built-in MCP** (§3) against Hermit
replays.

### 2.3 Architectural blockers / gaps

- **Robustness of the resume state machine.** The `StepOver` expectation
  panics on unanticipated client behavior (§1.6.1). An interactive debugger
  must tolerate arbitrary legal client packet sequences without aborting the
  deterministic engine.
- **Run-mode reachability.** No documented way to reach the run-mode server
  from outside the guest netns; no "bind to a host-visible socket / Unix socket
  path" option exposed through the `hermit run` CLI (the capability exists in
  `GdbConnection::Path` but isn't surfaced).
- **No time-travel exposure.** Reverse execution is the natural Hermit
  differentiator (deterministic replay makes "run backward via restart +
  fast-forward" tractable) but is not advertised or wired to `qSupported`.
- **No structured/JSON interface.** Everything is raw RSP; there is no
  machine-friendly control surface (DAP or MCP) and no provenance envelope —
  exactly the gap `AGENT_DEBUGGING_VISION.md` targets.
- **Client/toolchain coupling.** Hard-coded `gdb` client plus the host GDB
  auto-load crash make the default experience fragile.

## 3. MCP Server Landscape (agent-driven debugging)

| Server | Backend | Interface | Notes for Hermit |
| --- | --- | --- | --- |
| **LLDB built-in MCP** (LLVM 21+) | LLDB itself | `protocol-server start MCP listen://host:port`; exposes **one** tool `lldb_command(debugger_id, command)` plus `lldb://debugger/<id>[/target/<i>]` resources | If Hermit's stub spoke LLDB's dialect, LLDB could attach to a Hermit replay and its MCP would drive it with **zero new Hermit code**. Cleanest reuse path, gated on §2.2. |
| **stass/lldb-mcp** | LLDB (Python `lldb_mcp.py`) | ~25 granular tools: `lldb_start/terminate/list_sessions`, `lldb_load/attach/load_core/run`, `lldb_continue/step/next/finish/kill`, `lldb_set_breakpoint`/`watchpoint`, `lldb_backtrace/print/examine/info_registers/frame_info` | Same LLDB-dialect prerequisite for the remote path; richer typed tools than the built-in. |
| **pansila/mcp_server_gdb** | GDB/MI | stdio + SSE; `create_session/get_session/...`, `continue/step/next_execution`, `get/set/delete_breakpoint`, `get_stack_frames`, `get_registers`, `read_memory` | **Works against Hermit today** by pointing its GDB at `hermit replay --gdbserver`. Fastest off-the-shelf prototype; no determinism provenance. |
| **karellen/karellen-rr-mcp** | rr + GDB/MI | recordings, reverse continue/step, event jumps, checkpoints, trace metadata | Closest workflow analogue to Hermit's differentiator; best reference for naming/lifecycle of a reverse-capable server. |
| **ChatDBG** | GDB/LLDB/Pdb | conversational, whitelisted commands | Evidence for model-directed iterative inspection; safety patterns (command whitelist, bounded output). |
| DebuggAI "attachable debug" | gdb/lldb via MCP | vendor | Signals market interest in deterministic-repro debugging over MCP. |

### Can Hermit use these off the shelf?

- **Today, yes for a prototype:** run the working `record`→`replay --gdbserver`
  path and attach **pansila/mcp_server_gdb** (or ChatDBG) to the local `gdb`
  client. This yields agent-driven debugging over deterministic replays
  immediately — limited to what GDB/MI exposes and with **no** trace-identity or
  provenance guarantees.
- **For the product, no:** none of these provide the reproducible-evidence
  contract (trace id, replay epoch, cursor, determinism boundary) that is
  Hermit's reason to exist here. That is the custom **hermit-native MCP** in
  `AGENT_DEBUGGING_VISION.md`.

### What a hermit-native MCP would look like

Per the vision doc: a local `stdio` JSON-RPC server that supervises Hermit +
GDB/MI as children, exposes `record/list/open`, replay control, and typed
read-only inspection, and stamps every response with a provenance envelope
(`trace_id`, `artifact_id`, `replay_epoch`, `cursor`, deterministic
`thread_id`, `complete`, `determinism`). The differentiating tools are
`runs_compare`, `trace_align`, `schedule_search_*`, and `counterfactual_run`.

## 4. Recommendations & Next Work Items

Ordered by ROI (impact ÷ effort):

1. **Fix the `finish`→`continue` panic (`reverie-ptrace/src/task.rs:1815`).**
   Small, high-impact robustness fix: in `handle_gdb_resume`, when the
   expectation is `StepOver` and the client sends `Continue`, perform the
   step-over and then continue (or otherwise degrade gracefully) instead of
   `panic!`. Add a regression case to `reverie/tests/gdbserver-integration`
   (`b <fn>; c; finish; c`). This is a Reverie change; treat it as an additive
   robustness fix and coordinate per the cross-repo rules in `CLAUDE.md`.

2. **Fastest path to agent-driven debugging (prototype, this week).** Wrap the
   working replay path with an off-the-shelf GDB/MI MCP (pansila) or ChatDBG,
   using `set auto-load off` / `set sysroot /` to dodge the host GDB
   auto-load crash. Deliverable: an agent that sets a breakpoint, inspects
   state, and reports a diagnosis over a deterministic Hermit replay. Explicitly
   label results as *no provenance* to avoid over-claiming.

3. **Surface a host-reachable server for `run` mode.** Expose the existing
   `GdbConnection::Path` (Unix socket) — or a host-visible bind — through
   `hermit run`, and/or document `nsenter -t <guest> -n`. Removes the "times
   out from the host" foot-gun and enables non-replay debugging.

4. **Start the hermit-native MCP at Phase 0/1** (`AGENT_DEBUGGING_VISION.md`):
   `stdio` transport; `record`/`trace_list`/`trace_open`; forward replay control
   via a GDB/MI adapter over the replay gdbserver; provenance envelope on every
   response. This is the real product surface.

5. **LLDB support (parallelizable).** Capture an LLDB `gdb-remote packets` log
   to pinpoint the stall, then add the LLDB handshake packets (`qHostInfo`,
   `qProcessInfo`, `qRegisterInfo`, `qMemoryRegionInfo`, `jThreadsInfo`,
   thread-suffix). Payoff: one server drives GDB *and* LLDB, and LLDB's built-in
   MCP works against Hermit replays for free.

6. **Reverse / time-travel debugging.** The dormant `ReplayLog` machinery plus
   Hermit's deterministic replay make restart+fast-forward reverse execution
   feasible. Advertise `ReverseStep+`/`ReverseContinue+` only once backed by a
   real implementation; this is Hermit's key differentiator over live-debugger
   MCP servers.

### Testing agent-driven debugging without CI token cost

- **Mock MCP client.** Drive the MCP server with a deterministic, scripted
  client (a fixed sequence of tool calls with expected structured responses) —
  no LLM in the loop. Assert on the provenance envelope and typed results. This
  is a natural extension of the existing `gdbserver-helper` pattern
  (`--iex`/`--ex` command files, compare stdout/stderr/exit).
- **Contract tests** for hostile/edge behavior: stale cursor/epoch, pagination,
  cancellation of a running replay, and the `finish`→`continue` regression.
- Reserve real-LLM evaluation for periodic, out-of-CI benchmark runs (the
  evaluation plan in `AGENT_DEBUGGING_VISION.md`), pinning model, seeds, and
  trace/artifact digests.

## Appendix: Evidence

- **Reverie pin used by this build:** `Cargo.lock` →
  `reverie-ptrace ... git+https://github.com/facebookexperimental/reverie.git?branch=main#96693397ed60aa07c59ffeed4df3deed89b183e2`.
  (The gdbstub source cited here is the sibling `reverie/` checkout, which
  matches the same code paths; verify line numbers against the pinned SHA
  before landing a fix.)
- **Working GDB session command:**
  `hermit replay <id> --gdbex "set sysroot /" --gdbex "set auto-load off"
  --gdbex "b helper" --gdbex "c" --gdbex "bt" --gdbex "info args"
  --gdbex "finish" --gdbex "c" --gdbex "quit"`.
- **Panic reproduced by** the same command (the trailing `finish` then `c`).
- **Namespace check:** `readlink /proc/<guest>/ns/net` (guest) differs from the
  host net namespace; the `:port` listener is only visible via
  `nsenter -t <guest> -n ss -ltn`.
- **Host toolchain:** `GNU gdb (GDB) 9.1` (fb), `Meta lldb version 23.6.7`;
  CPUID faulting unsupported (VM).
