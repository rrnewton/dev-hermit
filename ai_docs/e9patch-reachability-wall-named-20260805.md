# e9patch "reachability wall" — NAMED precisely (what/from-where/how-to-clear)

**Task:** `e9patch_hybridptrace_inguest_converge` (child of P0 `unified-in-guest-patching-backend`).
**Author:** impl agent, opus-4.8, 2026-08-05. **Mode: characterization — no code.**
**Live-verified HEADs:** reverie primary `8688189a` (main), hermit primary `fc0b76ad` (main). Every
anchor below Read at these SHAs, NOT inherited from the audit (`f80b1c09`/`04a46b43`) — line numbers moved.

The owner's ask: the predecessor cited a "reachability wall" as the sole remaining e9patch blocker but
never described it. This names it. **It is not one wall — it is a three-link chain, and the load-bearing
link is in reverie, not hermit.** (Distinct from, and NOT to be confused with, the liteinst *rdpmc
visibility wall* — `mod perf` private — which the owner assigned to hermit-liteinst. That is RCB read-side;
this is the e9patch in-guest *dispatch* path.)

---

## WHAT is unreachable

e9patch's **in-guest Detcore dispatch path**: the route where the guest process traps its own syscalls
in-process (SIGSYS) and services them through the shared driver `reverie_preload::tool_host::drive_tool_syscall`,
with Detcore living ONLY in the guest and ptrace off the syscall hot path.

Concretely unreachable = **no live path ever calls the in-guest handler.** e9patch is already *wired* to
the shared driver — `reverie-e9patch/src/tool_host.rs:33-36` imports and calls
`drive_tool_syscall`/`drive_ready`/`TailResult` (so ERESTARTSYS #362 / e9-3 errno-512 is inherited-in-code) —
but that code is **dormant**: nothing installs the trap that would drive it.

## FROM WHERE it is unreachable — the three-link chain (runtime → host)

| Link | Site (live SHA) | State | Role |
|---|---|---|---|
| **L1 — LOAD-BEARING (reverie)** | `reverie-preload/src/lifecycle.rs:82-108` — `HybridPtrace::install()` returns `io::ErrorKind::Unsupported` | **documented skeleton, intentionally not functional** (doc :82-95, test `hybrid_is_not_yet_installable` :121-126) | The actual missing mechanism: the ptrace-lifecycle owner that would launch the guest, install a pre-`exec` seccomp filter, and cover exec/clone/vfork/vDSO. |
| **L2 — consequence (reverie)** | `reverie-e9patch/src/runtime.rs:259-262` — `install_hybrid_runtime()` forwards to L1 | returns `Unsupported` | `RuntimeMode::HybridPtrace` is *selectable* via `RUNTIME_ENV` (runtime.rs:357) but non-functional because L1 is a skeleton. e9patch's declared "generic production mode" is dead. |
| **L3 — downstream guards (hermit)** | `run.rs:1714-1720` `runtime_backend()` downgrades `E9patch→Ptrace`; `lib.rs:975-979` `ensure_backend_dispatch()` rejects `e9patch` for direct dispatch | **CORRECT today** | With L1/L2 dead, these route `hermit run --backend e9patch` through `e9patch::prepare` + the **Ptrace backend under `TracerBuilder<Detcore>`** → Detcore host-side, DETLOG-via-ptrace = the owner's literal cited defect. They are guards against selecting a dead path, NOT the wall itself. |

**Load-bearing correction of the predecessor's fuzzy label:** the wall is **L1**, the reverie-side
`HybridPtrace` skeleton. L3 (the hermit downgrade the audit fixated on as e9-1) is a *downstream consequence* —
removing it before L1 is live yields a broken backend, not a converged one. Order is forced: **L1 → L2
auto-clears → L3 removed LAST.**

## WHAT would make it reachable

Implement **L1**: the `HybridPtrace` `LifecycleController` as an **A-class `TracerBuilder<()>` (UNIT tool)
lifecycle-only owner** — ptrace for lifecycle ONLY, never on the syscall hot path (owner's "zero ptracer,
rare fallback at most"). Duties, verbatim from the skeleton doc (`lifecycle.rs:86-93`):

1. launch the guest under a thin ptrace controller that installs a **pre-`exec` seccomp filter** (closes the
   ~40 loader/startup-syscall gap before the constructor; covers static / `exec`);
2. keep the in-process **SIGSYS trap on the hot path** for ordinary syscalls — **no ptrace stop per syscall**;
3. let the controller handle **exec / clone / vfork** stops and **vDSO** patching the in-process filter cannot;
4. follow + reap the tree — **NO syscall subscription, NO Tool in host.**

**Reference shape already in-tree (do not invent):** `reverie-liteinst/src/backend.rs:762`
`TracerBuilder::<()>::new(command).spawn()` (liteinst's live A-class lifecycle-only reaper) and
`reverie/experimental/reverie-host/src/tracer.rs:30` `impl TracerBuilder<()>`. The skeleton doc points to
"the SaBRe real-backend work" as the launcher model.

Then, in order:
- **L2 auto-clears:** `install_hybrid_runtime()` returns `Ok`; the in-guest path goes live already wired to
  `drive_tool_syscall` (inherits ERESTARTSYS → kills e9-3 errno-512 for free).
- **L3 removed LAST:** redefine `runtime_backend()` so `E9patch` no longer downgrades to `Ptrace`, and give
  e9patch a real in-guest dispatch in `ensure_backend_dispatch()` — only after L1/L2 are live + tested.
- **Fail-closed = in-guest SIGSYS** (reverie-preload), NEVER a ptrace trap (owner's fail-closed rule; C5).

## Acceptance (third-party-checkable, from in-guest-rcb-accounting design §9)

- **C4:** no `TracerBuilder<Detcore>` on e9patch's run path; no per-subscribed-syscall trap-to-host fallback.
- **C5:** un-instrumented-syscall fallback is an in-guest SIGSYS handler; no ptrace trap on the syscall path.
- Live check that the wall still stands today: `HybridPtrace.install()` → `Unsupported` (lifecycle.rs test
  `hybrid_is_not_yet_installable`); `runtime_backend()` returns `Ptrace` for `E9patch` (run.rs:1715-1716).

## Scope / ownership notes

- This is a **coordinated reverie+hermit** lift (one slot, report BOTH SHAs). L1 is the largest single piece.
- NOT this task: liteinst rdpmc visibility wall (hermit-liteinst); inc-5 non-Option counter seam
  (`unify_backend_stats_transport`, MEASURE-AFTER); SaBRe sb-3 net (independent).
- Prereqs already satisfied: shared host #373 LANDED (reverie main `8688189a`), rdpmc #363 LANDED, lint #1571 LANDED.
