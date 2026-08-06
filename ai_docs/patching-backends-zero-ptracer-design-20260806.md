# Getting the ptracer off the patching backends' syscall path — the design

**Task:** `patching-backends-remove-ptracer-from-syscall-path` · **Agent:** herdr-dev
(`[impl agent, opus-5]`) · **2026-08-06**

Deliverable is a **design**, not a measurement. Per the owner gate, hop costs on these backends are
explicitly *not* decomposed here: a backend whose syscall path needs a ptrace round trip is not
architecturally correct, so its per-hop budget is not yet a meaningful quantity. The ~67 µs det-mode
hop is cited once, as *evidence of the defect*, and never broken down.

## Status of prior work, and what this adds

Three artifacts already cover parts of this question, all dated 2026-08-04 and all measured against
hermit `f80b1c09` / reverie `04a46b43`:

| Artifact | Covers |
| --- | --- |
| `patching-backends-ptrace-on-syscall-path-audit-20260804.md` | the B-class site inventory |
| `patching-backends-zero-ptracer-three-bucket-classification-20260804.md` | removable-now / needs-shared-host / rare-fallback |
| `unified-in-guest-patching-backend-scope-20260804.md` | the shared in-guest host scope map |

This document does not restate them. It (1) **re-verifies every load-bearing premise against current
`main` `4c70658e7`**, because the earlier line numbers have all moved and a design resting on stale
citations is not a design; (2) states the **shared path** as one contract rather than three scattered
scopes; (3) **corrects the framing of the known instance** named in the task.

**Re-verification result: every premise still holds. Nothing has been fixed.** Current citations:

| Premise | Site at `4c70658e7` | State |
| --- | --- | --- |
| e9patch is downgraded to ptrace wholesale | `hermit-cli/src/bin/hermit/run.rs:1765-1771` `runtime_backend()` returns `Backend::Ptrace` when the selection is `E9patch` | **still shipped** |
| e9patch in-guest runtime refuses to install | `reverie-e9patch/src/runtime.rs:252` → `io::ErrorKind::Unsupported` | **still shipped** |
| the L1 lifecycle owner does not exist | `reverie-preload/src/lifecycle.rs:104` → `io::ErrorKind::Unsupported` | **still shipped** |
| LiteInst ships host-hybrid | `hermit-cli/src/lib.rs:1555` dispatches `run_host_with_preload::<Detcore>` | **still shipped** |
| SaBRe runs a persistent per-syscall ptrace supervisor | `hermit-cli/src/sabre_ptrace.rs`, three `ptrace::syscall` resume sites | **still shipped** |

## Correcting the known instance

The task names *"e9patch routes DETLOG via the ptrace host instead of in-guest"* as the known
instance to fix. That symptom is real, but it is **not a DETLOG routing defect and must not be fixed
as one.**

`runtime_backend()` rewrites the *entire backend selection* from E9patch to Ptrace before anything
runs. Detcore is then constructed host-side under `TracerBuilder::<Detcore>`, so **every** subscribed
syscall — not just DETLOG emission — takes a ptrace round trip. DETLOG is simply the most visible
consequence because it is the thing you can see in a log.

This matters for sequencing: a change that moved DETLOG emission in-guest while the downgrade
remained would produce a backend that *looks* converged in its logs while every syscall still traps
to the host. That is the fake-parity move in this area, and it would also destroy the one honest
signal we currently have that e9patch is not converged. **Fix the downgrade; DETLOG follows.**

## Where a ptrace round trip remains on the syscall path

Using the established discriminator (`reverie-liteinst/CLAUDE.md`, Supported Boundary):

- **A-class, allowed** — `reverie_ptrace::TracerBuilder<()>` with the *unit* tool: lifecycle only.
  Follow and reap the tree, one-time trap install, one-time RIP redirect into an in-guest handler.
  No syscall subscription, no `Tool` in the host.
- **B-class, violation** — `TracerBuilder<Detcore>` (the Tool runs in the host), or any per-syscall
  trap-to-host fallback. Each subscribed syscall costs one round trip.

### e9patch — 100% B-class, the largest lift

Everything routes through the wholesale downgrade above. There is no partial conformance to preserve.
The blocker is a **three-link chain**, and only the innermost link is load-bearing:

1. **L1 (root)** `reverie-preload/src/lifecycle.rs:104` — the `HybridPtrace` lifecycle owner is a
   skeleton returning `Unsupported`. This is the real work.
2. **L2** `reverie-e9patch/src/runtime.rs:252` — `install_hybrid_runtime` returns `Unsupported`
   *because* L1 does not exist.
3. **L3** `hermit-cli/src/bin/hermit/run.rs:1765` — the CLI downgrade exists *because* L2 fails.

L3 and L2 are consequences. Removing either without L1 yields a backend that fails to start rather
than one that works. Sequence strictly inward-out: L1 → L2 → L3.

One piece of good news, verified: the shared Level-1 driver substance (`drive_tool_syscall`,
`drive_ready`, `TailResult` in `reverie-preload/src/tool_host.rs`) is landed, and
`reverie-e9patch/src/tool_host.rs` already imports and calls it. So the ERESTARTSYS/errno-512 gap
(#362) is inherited in code the moment e9patch runs in-guest at all — it is not separate work.

### LiteInst — shipped host-hybrid, partially removable today

The in-guest host **exists and is tested** (`run_with_preload::<Detcore>`, `tests/rpc_tool.rs`); it
simply has no hermit caller. Two rows are removable now:

- the CLI dispatch flip at `hermit-cli/src/lib.rs:1555`;
- the dispatch-path counters recorded in the *ptrace* crate
  (`reverie-ptrace/src/liteinst_stats.rs`, `from_ptrace_host_hybrid`) — their location is itself
  proof the host observes syscalls.

**The CLI flip is necessary but not sufficient**, and this is the trap: flipping it while in-guest
gaps remain simply converts a visible host-hybrid into a silent per-syscall fallback. The gaps that
must close first are timer/PMU (in-guest RCB clock reads a hardcoded 0), CPUID/RDTSC/RDRAND/RDSEED,
and clone3/vfork/exec/vDSO.

### SaBRe — the Tool is already in-guest; one residual

SaBRe is the conforming reference at the Tool boundary: `run_sabre` builds `GlobalState` + `RpcServer`
+ plugin with no `TracerBuilder<Detcore>`, `set_regs` refuses RIP/RSP with `EOPNOTSUPP`, memory goes
through `process_vm_*`. B(Tool-in-host) = 0.

The residual is `hermit-cli/src/sabre_ptrace.rs`: a **persistent** `PTRACE_SYSCALL` supervisor that
resumes with `ptrace::syscall` rather than `PTRACE_CONT`, giving two ptrace stops — entry and exit —
for **every syscall, for the whole run**. It is lightweight and it is not Detcore, but "lightweight
per-syscall ptracer" is still a per-syscall ptracer on the hot path, and it is emphatically not
"rare". An earlier claim that SaBRe was lifecycle-only was wrong; this is the correction.

## The shared in-guest path all three should use

One contract, three engines. The convergence is **not a new interface** — the single in-guest Detcore
subscriber already exists (`detcore/src/lib.rs`, `Detcore` impls the abstract `reverie::Tool` and
names no backend). What is duplicated is the *driver*, not the subscriber.

```
guest thread hits a patched site
      │
      ▼
per-backend trap/rewrite engine        ← the ONLY per-backend part
  e9patch: binary rewrite + SIGSYS seam
  liteinst: direct hook + SIGSYS seam
  sabre:   native C-ABI plugin (no SIGSYS)
      │
      ▼
shared in-guest Tool host  (reverie-preload/src/tool_host.rs)
  one subscription filter · one ThreadState map · one first-poll async driver
  one ERESTARTSYS/wait4 restartable-poll retry · one CoordinatorRpc
      │
      ▼
Detcore::<Guest<T>>  — executes IN THE GUEST, in the guest's own thread
      │
      ▼  only when GLOBAL state is genuinely required
CoordinatorRpc  ──────────►  tracer-side GlobalState
      (an RPC, not a ptrace stop; no guest thread is ptrace-stopped to service it)
```

The load-bearing distinction is the last hop. **Reaching global state by RPC is not the same cost
class as a ptrace round trip**, and conflating them is what makes the current architecture look
acceptable. A ptrace stop requires the tracer to be scheduled, the tracee to be stopped and resumed,
and register state to be marshalled through the kernel — the Detcore-scheduler + ptrace-host +
tokio-reactor sequence. An RPC is a message to a peer while the guest thread keeps running, and it is
needed only for state that is genuinely shared.

The shared host must own, exactly once: the `reverie_preload` SIGSYS/seccomp seam; the subscription
filter; one `ThreadState` map; the first-poll async driver; the ERESTARTSYS/wait4 restartable-poll
retry; and one `CoordinatorRpc`. Per-backend, what legitimately remains is only the `Guest<T>` impl
and the trap/rewrite engine.

Today `ToolHost<T>` is duplicated near-identically in `reverie-liteinst/src/tool_host.rs` and
`reverie-e9patch/src/tool_host.rs`, each with its own `CoordinatorRpc<G>`. SaBRe keeps its adapter
(different engine, no SIGSYS) but presents the same `Guest<T>`, so "one subscriber" holds for all
three while "one driver" applies to the two SIGSYS backends.

## What must genuinely remain a fallback

A fallback is legitimate when the in-guest path *cannot* handle the case, it is **rare**, and it is
**counted**. Three qualify; one currently-claimed fallback does not.

1. **Unpatchable sites and cacheline straddlers (LiteInst).** Some instruction sequences cannot be
   rewritten. Genuinely rare and input-dependent. Target: in-guest SIGSYS, not a host trap.
2. **Raw, un-rewritten `0f 05` sites (SaBRe).** The *need* is legitimate — if rewriting missed a
   site, something must catch it. The *implementation* is the violation: a permanent per-syscall
   supervisor. Two ways to reduce it, both independent of the shared host: prove rewriting is
   exhaustive and drop the net; or bound it to a warm-up phase, patch, then detach. Note it cannot be
   made seccomp-lazy, because the raw site keys on RIP, not on syscall number.
3. **Process lifecycle** — spawn/attach/reap, one-time trap install, one-time RIP redirect. This is
   A-class by definition and should stay ptrace. It is not on the syscall path.

**Not a fallback:** the e9patch whole-backend downgrade and the LiteInst host-hybrid dispatch. Both
are the *default* path today, not an exception, and neither is counted as a fallback anywhere.
Describing them as fallbacks would be the category error this gate exists to prevent.

Every remaining fallback must emit an observable signal. A silent fastpath-to-fallback transition is
indistinguishable from the fastpath working, which is precisely how a backend can appear converged
while every syscall traps.

## Reverie API impact — reported loudly, per the task

**No core-abstraction change is required, and none is proposed.** The `Tool`, `Guest`, `Backend`, and
syscall-interception model are untouched by this design. That is the main API finding and it is a
positive one: convergence is collapsing duplicated drivers and flipping host wiring, not redesigning
the interface.

Two items the owner should nonetheless see:

- **L1, `HybridPtrace` lifecycle owner** (`reverie-preload/src/lifecycle.rs`) — filling in a skeleton
  that already exists and is already documented in place. Additive; no existing consumer changes
  shape. This is the single largest piece of work in the whole gate.
- **Hoisting `ToolHost<T>`/`CoordinatorRpc<G>` into the shared host** — a refactor *within* reverie
  affecting two backend crates. Additive at the `Tool`/`Guest` boundary. If it turns out to require a
  `Guest<T>` signature change, that crosses into core-abstraction territory and needs owner
  discussion before proceeding.

## Sequence

1. **LiteInst counters** (`from_ptrace_host_hybrid`) — removable now, independent, no dependency.
2. **SaBRe residual** — independent of the shared host; pick exhaustiveness-proof or warm-up-detach.
3. **Shared host hoist** — unblocks both remaining backends.
4. **LiteInst in-guest gaps** (RCB clock, CPUID/RDTSC/RDRAND/RDSEED, clone3/vfork/exec/vDSO) → then
   the CLI flip. Not before: flipping early converts a visible hybrid into a silent fallback.
5. **e9patch L1 → L2 → L3**, strictly inward-out. DETLOG corrects itself at L3.

The gate is met, per backend, when every B-class row is either eliminated or reduced to a counted,
justified A-class lifecycle use. Only then does hop-cost decomposition become a meaningful question.
