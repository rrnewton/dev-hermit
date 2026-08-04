# Patching backends — PTRACE-ON-THE-SYSCALL-PATH audit (the zero-ptracer gate)

**Task:** `unified-in-guest-patching-backend` (P0, OWNER ARCHITECTURE GATE). **Mode: SCOPE/AUDIT only — no code.**
**Author:** impl agent, opus-4.8, 2026-08-04 ~19:35Z. **Repos/HEAD:** reverie `04a46b43`, hermit `f80b1c09` (main).
**Method:** direct Read of every cited anchor at current HEAD (not code-search paraphrase) + one thorough
read-only cross-backend completeness sweep.

## Owner framing (this spawn)

> The FINAL FORM of the patching backends is (1) SHARED CODE, (2) ZERO PTRACER (or absolutely minimal
> rare fallback). If a backend depends on a PTRACE ROUND TRIP on the syscall path it is NOT
> architecturally correct yet and NOT ready for optimization at all. ALL perf work on
> sabre/e9patch/liteinst is SUSPENDED until the ptracer is out of the syscall path.

Cost of the hop being removed: the real det-mode hop measures **~67µs**, floor = Detcore scheduler +
**PTRACE HOST** + tokio reactor. That is what one ptrace round trip costs per intercepted syscall.

**This document is "the list": per backend, every remaining place the ptracer sits ON THE SYSCALL PATH.**

---

## 0. The discriminator (allowed vs. must-remove) — already named in-tree

The correct/allowed ptrace shape is a **lifecycle-only reaper**, and it already exists by name:

- **A-class (ALLOWED, lifecycle-only):** `reverie_ptrace::TracerBuilder<()>` — the UNIT tool. Per
  `reverie-liteinst/CLAUDE.md` (Supported Boundary): *"A lifecycle-only `TracerBuilder<()>` still
  follows and reaps the process tree **without subscribing to syscalls or instantiating the concrete
  Tool in the host**."* Process spawn/attach/detach/reap, one-time trap install, one-time RIP redirect
  into an in-guest handler, memory setup. NOT on the per-syscall hot path.
- **B-class (ARCHITECTURALLY INCORRECT, the thing to remove):** `TracerBuilder<Detcore>` — the **Tool
  runs in the host** — OR any per-syscall fallback that traps to a host-side handler. Every subscribed
  guest syscall becomes a ptrace round trip. This is the ~67µs hop.

The whole audit reduces to: **which backends drive `Detcore` (or a per-syscall handler) host-side.**

---

## 1. e9patch — 100% ptrace on the syscall path (WORST; canonical instance)

e9patch is not a backend today; it is binary-rewriting preprocessing that runs **under the ptrace
backend**. Detcore runs entirely host-side.

| # | Site | file:line | Class | Shipped/fallback |
|---|---|---|---|---|
| e9-1 | `runtime_backend()` hard-downgrades `E9patch → Ptrace`; Detcore then runs under `TracerBuilder::<Detcore>` (host) | `hermit-cli/src/bin/hermit/run.rs:1714-1720` | **B** | **shipped/default — 100% of syscalls** |
| e9-2 | `install_hybrid_runtime()` returns `io::ErrorKind::Unsupported`; in-guest fast path dormant. Comment states outright: *"ptrace performs all event handling"* | `reverie-e9patch/src/runtime.rs:259-262` (comment 249-254) | **B** | in-guest path not yet buildable |
| e9-3 | In-guest host lacks the ERESTARTSYS/`wait4` re-dispatch arm → maps `err → -errno` ⇒ app **errno 512** even once in-guest lands | `reverie-e9patch/src/tool_host.rs:347-349` | **B (latent)** | would corrupt in-guest path |

**Canonical instance (owner cited):** because the whole backend is downgraded, **DETLOG — and all
Detcore event handling — routes through the ptrace host**, not in-guest. It is a direct consequence of
e9-1, not a separate mechanism.

**Correct target shape (already documented in the e9-2 comment):** the shared fallback ptracer owns
**lifecycle only** while the in-process **SIGSYS** trap serves residual un-rewritten sites in-guest —
i.e. the `HybridPtrace` controller must become `TracerBuilder<()>`-shaped (A-class) with the Tool
in-guest. That lifecycle owner is the separate `e9patch_hybridptrace_inguest_converge` task and is the
**largest lift**.

---

## 2. LiteInst — shipped path routes the Tool through the ptrace host

The in-guest host is complete, tested (`reverie-liteinst/tests/rpc_tool.rs`), and already carries the
#362 ERESTARTSYS fix — **but hermit does not call it.** hermit ships the host-hybrid.

| # | Site | file:line | Class | Shipped/fallback |
|---|---|---|---|---|
| li-1 | hermit-cli dispatches LiteInst via `run_host_with_preload::<Detcore>` — Tool driven from the ptrace **host** | `hermit-cli/src/lib.rs:1531-1546` | **B** | **shipped/default** |
| li-2 | The in-guest `run_with_preload::<Detcore>` path (backend.rs:362) has **no hermit caller** | `reverie-liteinst/src/backend.rs` | (target A) | in-guest, unused |
| li-3 | Host-side dispatch-path counters recorded in the **ptrace crate** and pulled via `from_ptrace_host_hybrid` — proof the host observes syscalls & installs patches | `reverie-ptrace/src/liteinst_stats.rs:113-129,305-310`; `reverie-liteinst/src/backend.rs:275,350`; `stats.rs:148` | **B** | shipped |

### Slowpath taxonomy — per-class routing (the counts that must survive unification)

From `reverie-liteinst/src/stats.rs:111` (`LiteinstDispatchPath`). Classify each by where it lands:

| Path | Meaning | Fast/slow | Routes to |
|---|---|---|---|
| `direct_hook` | patched site → trampoline → tool in guest context | **FASTPATH** | **in-guest** (A) |
| `first_site_seccomp` | first-execution discovery trap (SIGSYS) → patch + RIP redirect | slow (one-time/site) | **in-guest SIGSYS** in pure mode; **recorded host-side** in shipped host-hybrid |
| `in_guest_sigsys` / `in_guest_nested_sigsys` | SIGSYS handled in-process | slow | **in-guest** (A) |
| `ptrace_installation` | site handled via **ptrace-installed** trap | slow | **ptrace host (B)** — name is literal; recorded by `record_ptrace_installation` in the ptrace crate |
| `cacheline_straddler` | straddling site can't be atomically patched → fallback | slow | fallback trap (see sweep) |
| `unpatchable_or_other` | site cannot be patched → fallback | slow | fallback trap (see sweep); pure in-guest mode = **not implemented** |

**FASTPATH = `direct_hook` in-guest. The `ptrace_installation` class is a literal B-class per-syscall
ptrace route.** The vacuity guard `direct_hook == N+1 && ptrace_installation == 0` is the working
silent-fallthrough detector and must be preserved.

### In-guest event GAPS that still force the host (blocks "zero ptracer" even after the CLI flip)

Per `reverie-liteinst/CLAUDE.md` (Supported Boundary) — events NOT routed to the in-guest Tool today,
each a residual host dependency to close:

- **Timer / PMU preemption:** timer arming does not deliver events; **RCB clock fixed at zero**
  (`read_clock → 0`, `set_timer` no-op, `tool_host.rs:887-903`). Deterministic preemption currently
  cannot run in-guest.
- **CPUID, RDTSC/RDTSCP, RDRAND/RDSEED** not routed to the in-guest Tool as Reverie events.
- **Thread clone, clone3, vfork, exec bootstrap, vDSO interception, unpatchable-site fallback** not
  implemented in-guest.
- Descendant signal-death and root-exits-first lifecycle events not routed in-guest.

⇒ Flipping li-1 → li-2 is necessary but **not sufficient**; the timer/PMU + CPUID/RDTSC + clone3/vfork/
exec/vDSO gaps are the true "minimal rare fallback" surface the owner allows — each must be either
handled in-guest or explicitly justified as A-class lifecycle.

---

## 3. SaBRe — Detcore Tool IS in-guest, BUT a persistent host `PTRACE_SYSCALL` safety-net supervisor sits on EVERY syscall

**CORRECTION (2026-08-04, direct full read of `hermit-cli/src/sabre_ptrace.rs`):** the prior
"SaBRe CONFORMS / residual = 0" verdict was WRONG. Two separable facts:

**(a) The Detcore TOOL runs in-guest — this part conforms.** `run_sabre` builds Detcore
`GlobalState` behind a `reverie_rpc_transport::RpcServer` and launches the guest through the SaBRe
C-ABI plugin; it does **not** construct `TracerBuilder<Detcore>` (`hermit-cli/src/lib.rs:994-1069`).
The in-process handler's `set_regs` operates on `current_syscall_frame()` and rejects RIP/RSP with
`EOPNOTSUPP` (`reverie_adapter.rs:1171`); guest memory via `process_vm_*`. So SaBRe is NOT B-class in
the "Tool in host" sense: **B(Tool-in-host) = 0.**

**(b) BUT a persistent `PTRACE_SYSCALL` "safety net" supervisor stops on every syscall entry+exit for
the whole run.** `hermit-cli/src/sabre_ptrace.rs` (module docstring: *"Ptrace safety net for syscall
instructions missed by SaBRe rewriting"*) attaches ptrace to the root, sets `PTRACE_O_TRACESYSGOOD`,
and resumes with `ptrace::syscall` (= `PTRACE_SYSCALL`) — **never `PTRACE_CONT`** — at `:165`, `:406`,
`:428`. Every tracee therefore takes TWO ptrace stops (entry + exit) per syscall for its entire
lifetime (`Supervisor::run` loop `:169-331`; `handle_syscall_stop` `:351-408`). On each stop it does
`getregs` + reads 2 bytes at the site + a cached `is_trusted_mapping` check; for a raw `0f 05` syscall
at an untrusted site it rewrites the site to the SaBRe marker `0f ff` and redirects into the in-guest
handler (`:379-393`). For trusted mappings (sabre, plugin, any `.so` incl. libc — `mapping_is_trusted`
`:525-538`) it does nothing but resume. It does **not** run Detcore host-side.

| # | Site | file:line | Class |
|---|---|---|---|
| sb-1 | Detcore Tool in-guest: `run_sabre` builds `GlobalState`+`RpcServer` + SaBRe plugin; no `TracerBuilder<Detcore>` | `hermit-cli/src/lib.rs:994-1069` | **A (Tool in guest)** |
| sb-2 | in-process `set_regs` rejects RIP/RSP `EOPNOTSUPP`; memory via `process_vm_*` | `reverie_adapter.rs:1171` | **A** |
| sb-3 | **Persistent `PTRACE_SYSCALL` safety-net supervisor — 2 ptrace stops per syscall, whole run** (resume `:165`/`:406`/`:428`) | `hermit-cli/src/sabre_ptrace.rs:148-441` | **residual ptracer ON the syscall path** (per-syscall; lightweight; NOT Detcore) |

Corroborating: `SabreSlowPath` explicitly enumerates `PtraceSyscallEntry`, `PtraceSyscallExit`,
`PtraceRawSyscallRedirect`, `PtraceInstalledSigillDispatch` as counted slow paths
(`reverie/experimental/reverie-sabre-stats/src/lib.rs:62-83`) — the design knows these are per-syscall
ptrace routes.

**Counted verdict:** B(Tool-in-host) residual = **0**; **zero-ptracer-on-syscall-path = FALSE** —
exactly **one** persistent per-syscall ptrace mechanism remains (**sb-3**), stopping on every syscall
entry+exit, including the real syscalls the in-guest handler issues from the trusted plugin mapping.
sb-3 is materially lighter than e9patch/LiteInst B (no Detcore in host, only lightweight site-patching)
but it is **not "rare"** — it fires on every syscall, so it does not yet meet the owner's
"absolutely minimal rare fallback" bar. It can't trivially become seccomp-lazy: a raw un-rewritten
`0f 05` site is not distinguishable by syscall number/args, only by RIP/mapping, so catching it needs
a per-syscall stop (`PTRACE_SYSCALL`, or seccomp `RET_TRACE`-on-all + RIP filter — still per-syscall).
Reducing sb-3 to A-class requires either (i) proving SaBRe static+load-time rewriting is exhaustive so
the net can be dropped, or (ii) a bounded warm-up that patches all reachable raw sites then detaches.
Both are open questions — so SaBRe, the supposed conforming reference, is **not** zero-ptracer today.

---

## 4. Shared in-guest tool host — scope (unchanged; pointer)

The single subscriber ALREADY EXISTS and is backend-agnostic: `impl Tool for Detcore`
(`hermit/detcore/src/lib.rs:780`). Convergence = collapse the duplicated Family-A in-guest driver into
one shared host + flip two backends' wiring. Design LOCKED = **Path A** (event-abstraction trait; regs
stay per-backend in `Guest<T>`; no shared reverie public-type change). Turnkey build spec:
`ai_docs/shared-inguest-toolhost-build-spec-20260804.md`. Full shared-vs-duplicated map:
`ai_docs/unified-in-guest-patching-backend-scope-20260804.md`.

The shared host must own the ERESTARTSYS/`wait4` re-dispatch loop (today only in liteinst
`tool_host.rs:319-330`) so e9patch (e9-3) and any future in-guest backend inherit it.

---

## 5. Stats/counter survival (answers `unify_backend_stats_transport`)

Slowpath/fallthrough counts MUST survive unification (owner). Resolution (build-spec §5/§6):

- Shared Family-A host carries a **MANDATORY (non-Option)** per-backend counter seam
  (`HostBackend::slowpath_counter() -> &dyn SlowpathCounter`) so a converging backend **cannot compile
  without it** ⇒ cannot silently drop per-path counts (the exact failure that hid LiteInst's retired
  14.5x path). Taxonomy to expose = the §2 table.
- **Transport is unified behind the ONE contract `BackendStatsSource`, not one wire.** Family A =
  RPC `GlobalTool` producer (`LiteinstStatsGlobal`/stats.sock, `stats.rs:396`), submitted by the shared
  host at process exit. Family B (SaBRe) keeps its shmem-memfd engine
  (`reverie-sabre-stats/src/lib.rs:121,221`) — a different engine, not a fourth path — conforming to the
  same `BackendStatsSource` + same taxonomy. No second transport built in the shared host.

---

## 6. Ordered lift (STOP-ORDER honored — compat lifts only as each backend ACTUALLY converges)

1. **Hoist Family A → one shared in-guest host** (Path A) incl. ERESTARTSYS + mandatory counter seam.
   [`shared_inguest_toolhost_family`, med]
2. **LiteInst: flip CLI** li-1 → li-2 (host exists+tested), then close the in-guest event gaps (§2:
   timer/PMU, CPUID/RDTSC, clone3/vfork/exec/vDSO). [`liteinst_flip_cli_to`, small-med + gap work]
3. **e9patch: implement `HybridPtrace` lifecycle owner** (A-class, `TracerBuilder<()>`-shaped), inherit
   ERESTARTSYS, then remove the e9-1 downgrade. [`e9patch_hybridptrace_inguest_converge`, large]
4. **SaBRe:** B(Tool-in-host) = 0, but sb-3 (persistent `PTRACE_SYSCALL` safety-net supervisor,
   `hermit-cli/src/sabre_ptrace.rs`) is a per-syscall ptracer on the hot path — NOT zero-ptracer.
   Decide with the owner whether sb-3 counts as allowed "minimal rare fallback"; to reach true
   zero-ptracer, either prove SaBRe rewriting is exhaustive (drop the net) or add a bounded warm-up
   that patches all reachable raw sites then detaches. Open.
5. **Stats:** fold submission into the shared exit path (§5). [`unify_backend_stats_transport`]

**Gate for "ready for perf work" per backend = every B-class row above is eliminated (or reduced to a
counted, justified A-class lifecycle use). Until then, perf work on that backend stays SUSPENDED.**
