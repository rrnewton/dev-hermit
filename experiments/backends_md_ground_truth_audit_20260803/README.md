# BACKENDS.md ground-truth audit — three patching backends (sabre / liteinst / e9patch)

**Question.** Does `reverie/BACKENDS.md` describe what is *implemented* in the
three patching backends, or what was *intended*? For each of sabre, liteinst,
e9patch establish, with `file:line` evidence: (1) where the Reverie tool actually
lives (in-guest vs host-side); (2) how syscalls are intercepted; (3) where event
handling / DETLOG is produced and whether it round-trips to a host per event;
(4) how global state is reached; (5) maturity with evidence; (6) which is
furthest along for *real Detcore*. Deliverable: an apply-ready corrected
BACKENDS.md and an explicit list of every place the previous doc was wrong.

**Why this audit exists.** A prior finding proved code can *document* an
architecture it does not *implement*: `reverie-e9patch` lifecycle code describes
an in-guest fast path, yet `hermit run --backend e9patch` does DETLOG host-side
via the ptrace supervisor. So BACKENDS.md claims had to be re-checked against
source, not trusted.

## Method / provenance

- Reverie audited at **HEAD `d2fb9a055693bec30e8d48333c5694050b22e869`** (`main`,
  primary checkout, read-only). All `file:line` below are at this SHA.
- Hermit cross-checks read from the primary `hermit/` checkout (wiring only).
- Static source inspection with ripgrep; no build/run performed here. Corpus /
  perf numbers are cited from prior experiments, not re-measured.
- `search_files` MCP is unusable on these local `rrnewton` forks (biggrep does
  not index them); ripgrep is the justified substitute.

---

## Per-backend ground truth (the 6 questions)

### SaBRe

- **Q1 tool location — IN-GUEST plugin, but the audited `reverie-sabre` adapter
  cannot host Detcore.** SaBRe rewrites mapped `.text` and runs the tool as a
  guest plugin. The `reverie-sabre` `ReverieAdapter` drives the tool with
  `poll_once(...)` around every async handler
  (`experimental/reverie-sabre/src/reverie_adapter.rs:161,200,239,264,305,492`).
  `poll_once` polls the handler future exactly once; any handler that returns
  `Pending` (i.e. any real async tool, which Detcore is) is dropped. So this
  adapter is a **first-poll-only** host suited to synchronous example tools only.
- **Load-bearing correction:** Hermit's *actual* Detcore-on-SaBRe path does
  **not** use `reverie-sabre`'s adapter. It shells out to an external loader plus
  **`libdetcore_sabre.so`**, whose Rust lives in the **`detcore-sabre` crate,
  which is excluded from the default workspace** and is **not present anywhere in
  the reverie tree** (`hermit/hermit-cli/src/lib.rs:676-682, 864, 878-901`;
  `run_sabre` at `:994`, dispatched from `:1521,:1630`). BACKENDS.md audits
  `reverie-sabre` but never mentions `detcore-sabre` — the crate that actually
  carries Detcore.
- **Q2 interception:** 5-byte JMP trampoline; reserved-instruction + `SIGILL`
  fallback for sites too small to patch. No independent seccomp completeness trap
  on the Reverie host launch path, so an entirely missed site is not proved
  fail-closed (unchanged from the doc; accurate).
- **Q3 DETLOG / round-trip:** in the audited adapter, only example tools run; no
  Detcore, no DETLOG. In the `detcore-sabre` path (out of tree) Detcore runs
  in-guest; not auditable from this checkout.
- **Q4 global state:** per-thread `ProtectedFd<BlockingRpcClient<T::GlobalState>>`
  → coordinator singleton over `reverie-rpc-transport`
  (`reverie_adapter.rs:365,432`). Local adapter modes also exist.
- **Q5 maturity:** no `Backend`-trait impl; first-poll-only adapter; experimental.
  ~B1.
- **Q6:** carries Detcore only via the un-audited external `detcore-sabre` plugin.

### e9patch (confirms the standing host-side finding)

- **Q1 tool location — HOST-SIDE for every wired path.** `hermit run --backend
  e9patch` maps E9patch→Ptrace at runtime (`hermit .../run.rs:1714`;
  `ensure_backend_dispatch` rejects an e9patch runtime, `hermit .../lib.rs:975`).
  The generic `E9patchBackend::run<T>` (`reverie-e9patch/src/backend.rs:990`)
  goes through `spawn<T>` where **"ptrace remains the lifecycle owner and Guest"**
  (`backend.rs:550,564`). AOT `e9tool` rewrite is preprocessing; DETLOG is
  produced host-side by the ptrace supervisor, identically to plain ptrace.
- **In-guest `ToolHost<T>` exists but is inert / opt-in only.** `install_tool<T>`
  / `ToolHost<T>` / `E9patchGuest<T>` exist but have **no in-tree caller** on the
  hermit path; `run_direct_*` is "intentionally separate from `Backend::run`,
  whose ptrace lifecycle remains the production default" (`backend.rs:457-459`).
  It **cannot run the Detcore scheduler**: `set_timer` / `set_timer_precise` /
  `read_clock` all return `Unsupported` — "e9patch direct Tool host does not
  implement RCB timer delivery / precise RCB timer delivery / an RCB clock"
  (`tool_host.rs:632,640,648`). It is **single-process**: `injected_syscall_guard`
  fails closed `EOPNOTSUPP` on clone/clone3/fork/vfork/execve/execveat
  (`tool_host.rs:656-671`, applied at `:596,:618`).
- **The "production controller" `HybridPtrace` is a skeleton.** `RuntimeMode`
  doc calls HybridPtrace the production controller (`runtime.rs:186-195`), but
  `HybridPtrace::install` returns `io::ErrorKind::Unsupported`,
  "hybrid-ptrace lifecycle controller is not yet implemented"
  (`reverie-preload/src/lifecycle.rs:97-105`, asserted by test `:125`).
  `InProcessSeccomp::install` is functional (`lifecycle.rs:68`) but cannot cover
  the pre-constructor loader syscalls, vDSO fast paths, or exec.
- **Q6:** carries Detcore **not at all at runtime** — falls back to ptrace.

### LiteInst (two modes; hermit's Detcore uses Mode B = host-side)

- **Mode A (`run` / `run_with_preload`) — tool DSO hosted IN-GUEST.**
  `install_tool::<T>` builds a `ToolHost<T>` holding `T` + per-thread state inside
  the guest (`reverie-liteinst/src/tool_host.rs:82,123-150`); events handled
  in-guest (`:156-260`); UDS RPC to the launcher's coordinator only when the tool
  calls `send_rpc` (`rpc.rs:69-115`; `backend.rs:584-590`).
- **Mode B (`run_host_with_preload*`) — tool + GlobalState HOST-SIDE in the
  ptracer.** Constructs `TracerBuilder<T>` (`backend.rs:217-226`); the preload DSO
  only installs patches and emits injected traps; **every** patched hot-site
  round-trips to the host ptracer (`reverie-ptrace/src/task.rs:2344` →
  `handle_injected_syscall` → `handle_syscall_event` `:2056-2060`).
- **Load-bearing correction:** Hermit's flagship Detcore wiring calls **Mode B**
  — `LiteinstBackend::run_host_with_preload::<Detcore>` (`hermit .../lib.rs:1534`)
  and `run_host_with_output_and_preload::<Detcore>` (`:1643`). So Detcore-under-
  hermit on liteinst is **host-side**, not the in-guest DSO of Mode A.
- **Q2/Q4:** Mode A seccomp `SECCOMP_RET_TRAP`→`SIGSYS`, replace-first hook +
  trampoline redirect; global state via guest→host RPC. Mode B ptrace rewrites
  RIP/stack to call the in-guest installer, then hosts events in-process.
- **Q5/Q6:** implements the generic `Backend` trait (`backend.rs:478`). Both
  modes single-process/single-thread, fail-closed on fork/thread/exec, no
  timer/clock (`tool_host.rs:616-638`, `task.rs:3935-3978`). **It is the only
  wired, tested, Detcore-carrying patching path that lives inside the reverie tree
  itself** → furthest along for real Detcore, but the wired path is host-side.

### Cross-cutting truth

In **every** in-guest backend, Detcore's `GlobalState` + global scheduler stay a
host **singleton reached by RPC** — intrinsic to Detcore's determinism (one
logical CPU, deterministic thread order, RCB preemption), not an e9patch defect.
Hence the (a) sequentialization cost (park + RPC to the scheduler singleton) is
present in all backends; only the (b) trap round-trip cost is an axis an in-guest
backend can win.

---

## Where the previous BACKENDS.md is WRONG (apply-ready corrections)

Each item gives the current doc text, the defect, and the exact fix. All new
`file:line` are verified at reverie `d2fb9a05`.

### D1 — Stale source-link SHA (all links) + stale perf SHAs

- **Defect.** Every `[...]:` link (lines 172-239) pins reverie
  `2f812840b718a6ac2a772a56cd05490765465ebf`; HEAD is `d2fb9a05...`. The perf
  links (`perf-harness`/`perf-e9`/`perf-sabre`, lines 237-239) pin dev-hermit
  `1490bbbf`; the CSVs themselves were produced at hermit `82a8e853` / reverie
  `a4f33d69`, a third, different pair.
- **Fix.** Re-pin each reverie link to `d2fb9a05` **only with a re-verified line
  range** (do not blanket-bump the SHA — new SHA + old line numbers can cite the
  wrong code). Verified current ranges are listed in "Link fixups" below. State
  the exact hermit+reverie SHAs the perf CSVs were produced at, next to the
  geomean claim (lines 147-155).

### D2 — SaBRe row omits the first-poll-only limitation and never names `detcore-sabre`

- **Current (line 46):** "The plugin runs the tool in guest context. The remote
  adapter keeps per-thread state and one protected blocking RPC client per
  thread; a coordinator serves the shared singleton ... Local adapter modes also
  exist."
- **Defect.** The `reverie-sabre` adapter drives handlers with `poll_once`
  (`reverie_adapter.rs:161`), so it can only host **synchronous** tools; an async
  tool (Detcore) is dropped on first `Pending`. And the doc never states that
  Detcore-on-SaBRe actually runs via the external **`detcore-sabre` crate /
  `libdetcore_sabre.so`** (excluded from the default workspace, not in this tree;
  `hermit .../lib.rs:676-682,864,878-901`), *not* via this adapter.
- **Fix.** Add: "The audited `reverie-sabre` adapter polls each async handler
  once (`reverie_adapter.rs:161`), so it hosts synchronous example tools only and
  cannot run an async tool such as Detcore. Hermit's Detcore-on-SaBRe path
  instead loads a separate `libdetcore_sabre.so` built from the out-of-tree
  `detcore-sabre` crate." Add a Contract-status caveat that `reverie-sabre`'s
  `Tool`/`Guest` path is example-tool-only today.

### D3 — LiteInst: doc never says Detcore-under-hermit uses Mode B (host-side)

- **Current (line 49, "LiteInst, direct `Backend`").** Describes the in-guest tool
  DSO (Mode A) and "supports one process and one thread."
- **Defect.** A reader infers Detcore runs in-guest under liteinst. But hermit
  wires `run_host_with_preload::<Detcore>` (Mode B, host-side ptracer;
  `hermit .../lib.rs:1534,1643`; `backend.rs:217-226`).
- **Fix.** Add one sentence to the LiteInst rows: "Hermit's Detcore integration
  uses the ptrace-owned hybrid (`run_host_with_preload`), so under hermit the
  LiteInst tool and singleton are hosted host-side, not in the in-guest DSO."

### D4 — "ptrace as a last resort" is imprecise for LiteInst hybrid

- **Current (COMPONENT:PTRACER, lines 101-103):** "'ptrace as a last resort' is
  accurate only for the LiteInst hybrid's successfully patched sites."
- **Defect.** A *successfully patched* Mode-B hot-site still traps into the
  ptracer on **every** execution (`task.rs:2344` → `handle_injected_syscall` →
  `handle_syscall_event`). Ptrace is the per-event tool host for patched sites,
  not a last resort — this contradicts the sentence's own claim.
- **Fix.** Replace with: "Even a successfully patched LiteInst hybrid site
  re-enters the ptracer on every execution via an injected hot-site trap
  (`task.rs:2344`); ptrace is the per-event tool host there, not merely a
  fallback. 'Ptrace as a last resort' is not accurate for any current path."

### D5 — e9patch direct / HybridPtrace portrayed as more capable than code

- **Current (line 48 + COMPONENT:PTRACER).** Presents e9patch "direct opt-in" and
  the HybridPtrace controller as an in-guest path that "does not replace the
  generic backend yet" — implying a working-but-narrower alternative.
- **Defect.** (a) `HybridPtrace::install` returns `Unsupported` — it is a
  skeleton (`lifecycle.rs:97-105` + test `:125`); `runtime.rs:186-195` calling it
  "the production controller" is aspirational. (b) The in-guest `ToolHost<T>` has
  no in-tree caller and **cannot run the Detcore scheduler**: timer/clock return
  `Unsupported` (`tool_host.rs:632,640,648`). (c) It is single-process
  (`injected_syscall_guard`, `tool_host.rs:656-671`).
- **Fix.** State plainly: "e9patch's in-guest direct path is not active on any
  wired hermit path. Its `HybridPtrace` lifecycle controller is an unimplemented
  skeleton returning `Unsupported` (`reverie-preload/.../lifecycle.rs:97-105`),
  and its in-guest `ToolHost` provides no RCB timer/clock
  (`reverie-e9patch/.../tool_host.rs:632-648`), so the Detcore scheduler cannot
  run there. As wired, `--backend e9patch` executes on the ptrace backend
  (`hermit .../run.rs:1714`)."

### D6 — SaBRe C-side citations pin inconsistent SHAs

- **Defect.** SaBRe C links (`sabre-scan/-jump/-ud/-sigill/-api/-loader`, lines
  194-199) pin `rrnewton/SaBRe` at `df1839a1...` while the Rust-side links pin
  reverie `2f812840`. Verify the C SHA is the one actually vendored/pinned by the
  reverie build and make the pinning internally consistent (or annotate that the
  C repo is a separate pin on purpose).

### Claims that CHECK OUT (no change needed)

- e9patch `e9tool` rewrite rejects partial coverage and signal-based B0 sites
  (`reverie-e9patch/src/rewrite.rs` `partial_patch_coverage_fails_closed` @:953,
  B0 rejection @:283). ✓
- `PtraceBackend`/`E9patchBackend`/`LiteinstBackend` implement generic `Backend`
  (liteinst `backend.rs:478`). ✓
- `reverie-preload` `BuiltinTool` hosts only `Passthrough`/`SpoofGetpid`, not
  Detcore (`reverie-preload/src/lib.rs:85-91`). ✓
- LiteInst Mode-A replace-first hook + `EOPNOTSUPP` on unpatchable generic sites;
  CoordinatorRpc → launcher RpcServer. ✓ (per liteinst sub-audit)

## Link fixups (verified current ranges at `d2fb9a05`)

Update these link targets to SHA `d2fb9a05` with these ranges (others in the doc
still need per-link range re-verification before their SHA is bumped):

- `e9-backend` / `e9-generic-run` → `reverie-e9patch/src/backend.rs` `run<T>` @ **990**.
- `e9-hybrid` → `reverie-e9patch/src/backend.rs` `spawn<T>` "ptrace remains the
  lifecycle owner and Guest" @ **550-595**.
- `e9-direct-boundary` → `reverie-e9patch/src/backend.rs` **457-459**
  (`run_direct_with_output_and_preload`).
- e9patch timer/clock Unsupported → `reverie-e9patch/src/tool_host.rs` **632-651**;
  single-process guard **656-677**.
- `preload` HybridPtrace skeleton → `reverie-preload/src/lifecycle.rs` **97-108**
  (+ test **122-125**); `InProcessSeccomp` **68-79**.
- LiteInst: `lite-tool-host`→`tool_host.rs` **82-135**; `lite-launcher`→
  `backend.rs` **555-649**; `lite-hybrid-api`→`backend.rs` **196-230**;
  `lite-dispatch`→`runtime.rs` **1622-1701**; `lite-fallback`→`runtime.rs`
  **1699-1700**; `lite-ptrace-site`→`task.rs` **3570-3585**;
  `lite-ptrace-helper`→`task.rs` **3361-3554**; `lite-ptrace-trap`→`task.rs`
  **2314-2360**; `lite-readme`→`README.md` **99-124**; `lite-backend`→
  `backend.rs` **478**.
- SaBRe adapter first-poll → `experimental/reverie-sabre/src/reverie_adapter.rs`
  `poll_once` @ **161**; remote RPC state **365,432**.

## How to land the doc correction (not done here)

Editing `reverie/BACKENDS.md` is forbidden in the primary checkout and no slot is
assigned to this task; committing is a destructive SCM op the task does not
authorize. Apply D1-D6 + the link fixups in a dedicated reverie slot on a feature
branch and open a PR to `rrnewton/reverie:main` per the reverie PR workflow. This
artifact + the task note are the apply-ready source of truth.
