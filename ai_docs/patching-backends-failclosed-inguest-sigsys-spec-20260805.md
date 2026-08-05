# Fail-closed path for un-instrumented syscalls = IN-GUEST SIGSYS, never ptrace — unified spec

**Task:** `unified-in-guest-patching-backend` (P0, OWNER ARCHITECTURE GATE). **Mode: SPEC only — no code, no slot mutation, no validate.**
**Author:** design agent, opus-4.8, 2026-08-05.
**Repo/HEAD read against:** reverie `origin/main` = `55f6876a31fc396083ebe2266d8bd6c91075bcf9` (verified live: `git -C reverie rev-parse origin/main`).
**Method:** direct `git show origin/main:<path>` / `git grep origin/main` of every cited anchor at `55f6876a`, plus inspection of the pushed draft branch `origin/feat/e9patch-hybridptrace-lifecycle-owner` (reverie PR #377, OPEN/draft, verified via `gh pr view 377`). File:line anchors are at `55f6876a` unless explicitly tagged `[#377 branch]` or `[hermit]`.

## Provenance vs. prior design docs (build-on, with SHA correction)

This doc **extends** and re-anchors the fail-closed sub-spec that the following docs sketched but did not fully specify as one unified rule. It does not re-derive the architecture; it specifies the *one fallback path* the owner asked to "specify it."

- `ai_docs/patching-backends-ptrace-on-syscall-path-audit-20260804.md` — per-backend A/B audit; the discriminator (A = `TracerBuilder<()>` lifecycle-only; B = `TracerBuilder<Detcore>` or any per-syscall trap-to-host). §5 = stats.
- `ai_docs/patching-backends-zero-ptracer-three-bucket-classification-20260804.md` — REMOVABLE-NOW / NEEDS-SHARED-HOST-FIRST / RARE-FALLBACK buckets. The RARE-FALLBACK target form named there = in-guest SIGSYS.
- `ai_docs/in-guest-rcb-accounting-zero-ptracer-design-20260804.md` — RCB bracketing dance (SETTLED); its C5 acceptance condition ("fail-closed = in-guest SIGSYS, not ptrace") is what this doc turns into a full spec.
- `ai_docs/shared-inguest-toolhost-build-spec-20260804.md` — the SHARED-CODE build spec (Path A; 5 increments; `reverie-preload::tool_host` as the one driver).
- `ai_docs/inguest-stats-transport-map-post373-20260804.md` — §7 already recorded "fail-closed = in-guest SIGSYS, NOT ptrace (GOOD)" against `8688189a`; this doc re-verifies it at `55f6876a` and unifies it across all three backends.

**SHA-correction against the prior docs (do not trust their SHAs blindly — owner directive):** those docs were written against reverie `8688189a` / `04a46b43`. Main has since advanced to `55f6876a`. The intervening commits (`git log 8688189a..55f6876a`) are **almost entirely source-vendoring** (SaBRe libelf/zydis, e9patch, DynamoRIO packaged for Cargo; CI cache/ratchet) plus **two** liteinst behavior commits: `718686c` "detect fork at the interception point, not via pthread_atfork" and `ed5ef12` "trim per-hop coordinator RPC syscalls." **None of them invalidate the SIGSYS / seccomp / dispatch / perf anchors** — every anchor below was re-read at `55f6876a`. One prior claim is CORRECTED in §2 (the shared `install_in_process_trap` helper is a #377-branch item, not yet on main).

---

## 1. THE RULE (stated crisply)

> For any syscall a patching backend has **NOT** rewritten / subscribed / patched, the fallback path
> **MUST** be an **in-process SIGSYS signal handler** (seccomp `SECCOMP_RET_TRAP`-style, delivering a
> thread-directed `SIGSYS`, serviced **in-guest** by `reverie_preload`), routing dispatch through the
> **one shared in-guest driver** `reverie_preload::tool_host::drive_tool_syscall`.
>
> A **per-syscall ptrace trap-to-host is a VIOLATION.** It is exactly the ~67 µs det-mode hop
> (Detcore scheduler + PTRACE host + tokio reactor) the owner calls **"evidence of the defect, not a
> budget to decompose."** Ptracer OUT of the syscall path first; measure AFTER.

Corollary (A/B discriminator, in-tree at `reverie-liteinst/CLAUDE.md` Supported Boundary): the only
ptrace shape allowed anywhere near this path is the **A-class lifecycle-only** `TracerBuilder<()>`
reaper — follow/attach/reap the process tree, one-time pre-`exec` seccomp + handler install, one-time
RIP redirect — **never** a per-syscall stop and **never** a `Tool` in the host.

This rule is **unified across all three patching backends** (sabre / e9patch / liteinst): each backend's
un-instrumented-syscall escape must terminate in the *same* in-guest SIGSYS mechanism, not a per-backend
ptrace net.

---

## 2. THE SHARED MECHANISM (the in-guest SIGSYS install + handler, file:line @ `55f6876a`)

The mechanism already lives in `reverie-preload` and is backend-agnostic. Four cooperating pieces:

### 2.1 The seccomp filter (`SECCOMP_RET_TRAP` for everything but the trusted gate)
`reverie-preload/src/seccomp.rs`:
- Module contract (`:11`): *"The filter traps every real syscall entry with `SECCOMP_RET_TRAP`
  (delivering a thread-directed `SIGSYS`) **except** … the runtime's own trusted syscall gate."*
- `SECCOMP_RET_TRAP = 0x0003_0000` (`:38`); `SECCOMP_RET_ALLOW = 0x7fff_0000` (`:39`);
  `SECCOMP_RET_KILL_PROCESS = 0x8000_0000` (`:37`).
- `SeccompFilter::for_trusted_gate(gate)` (`:78`) builds a program that compares the calling RIP against
  the trusted-gate range and returns **TRAP for untrusted entry** (`:96-97`), **ALLOW for the gate**
  (`:99`). `install()` (`:126`) applies it before untrusted app threads start, after the SIGSYS handler
  is installed. (Unit tests `:193-208` bracket that both TRAP and ALLOW terminals are present.)

This is the **fail-closed primitive**: *every* real syscall that is not the single trusted-gate
instruction traps to SIGSYS. There is no per-syscall-number allowlist to fall through — un-instrumented
syscalls are caught by construction.

### 2.2 The SIGSYS handler install (signal)
`reverie-preload/src/signal.rs`:
- `RESERVED_SIGNALS = &[SIGSYS]` (`:25`), `is_reserved()` (`:28`) — the runtime reserves SIGSYS.
- `install_sigsys_handler(handler, on_alt_stack)` (`:42`): installs `SA_SIGINFO` (+ optional `SA_ONSTACK`)
  for SIGSYS (`:46-52`).
- `install_alt_stack()` (`:68`): the alternate signal stack the handler runs on.

### 2.3 The handler → dispatcher routing (the guest-half trap)
`reverie-preload/src/trap.rs`:
- Module doc (`:9-20`) states the whole path: kernel delivers thread-directed SIGSYS → `sigsys_handler`
  runs → reconstructs a `SyscallEvent` from the ucontext registers → dispatcher may forward through the
  **trusted gate** → writes the result into `RAX` and returns, resuming the guest.
- `sigsys_handler` (`:176`, `unsafe extern "C"`, `SA_SIGINFO`): validates it is a real SIGSYS
  (`:183`), has a reentrancy guard that **fails closed rather than recurse** if a trapped syscall occurs
  inside the handler (`:191-199`), reads/writes guest regs via `uc_mcontext.gregs` incl. `REG_RIP`
  (`:199-219`).
- `set_dispatcher(Box<dyn SyscallDispatcher>)` (`:116`) / `dispatcher()` (`:122`): one process-wide
  dispatcher, leaked for process lifetime, **no lazy alloc in the handler** (`:78`).
- **Fail-closed default when no dispatcher is registered: `ENOSYS`** (`:133-136`) — never a ptrace hop.
- Audit tag present: `TODO-HUMAN-REVIEW(PR-133)` at `trap.rs:182` (fail-closed SIGSYS provenance
  validation) — this path is under post-facto human review, do not strip.

### 2.4 The fail-closed dispatch policy (`apply_guards`) and the source discriminator
`reverie-preload/src/dispatch.rs`:
- `enum SyscallEventSource { SignalTrap (:27), DirectInstrumentation (:29) }` — the event carries
  **how it arrived**: `SignalTrap` = caught by the SIGSYS fallback (un-instrumented); `DirectInstrumentation`
  = reached the driver through a patched trampoline (fast path). `source()` accessor `:101`.
- `SyscallEvent` read-surface: `number()` `:83`, `args()` `:88`, `set_result()` `:111`, `source()` `:101`
  — the exact read surface the shared driver's seam trait (`HostSyscallEvent`, build-spec §3) needs.
- `PassthroughDispatcher::apply_guards(event) -> bool` (`:208`): the **fail-closed policy both hosts
  re-implement** and the dedup target. It fails-closed (returns a definite errno, never a ptrace hop) on
  cases an inherited TRAP filter cannot safely cross:
  - `execve`/`execveat` → `ENOTSUP` (`:216-219`; filter survives exec but handler/altstack/mappings do not).
  - `rt_sigaction` on a reserved signal → `EPERM` (`:223-226`; keep SIGSYS reserved).
  - `sigaltstack` set → `EPERM`; `rt_sigprocmask` non-UNBLOCK mutation → `EPERM` (`:230-238`; the trap
    depends on the runtime-owned altstack and on SIGSYS staying unblocked).
  - `clone` with non-null child stack, `clone3`, `vfork` → `ENOTSUP` (`:242-250`; a controller-owned
    child bootstrap is required — this is the clone3/vfork **in-guest event gap**, correctly failed
    closed, NOT routed to a ptracer).
  - Carries `AUTONOMOUS-BOT-IMPLEMENTED` audit tags at each new-classification arm.

**Where dispatch terminates in the Tool:** the registered `SyscallDispatcher` (Detcore's) routes the
reconstructed event through the **one shared driver** `reverie_preload::tool_host::drive_tool_syscall<T,G>`
(`reverie-preload/src/tool_host.rs:222`), which owns `classify_outcome` (`:249`) and the **ERESTARTSYS /
`wait4` re-dispatch loop** (`classify_outcome` returns `None` = restart; test `:418`
`classify_outcome_restarts_wait4_on_erestartsys`). Placing the fail-closed dispatch here is what makes it
*one reviewed implementation* every Family-A backend inherits.

### 2.5 The controller that installs the guest half (lifecycle)
`reverie-preload/src/lifecycle.rs`:
- `trait LifecycleController { name() :54; unsafe install(&self, &RuntimeConfig) :61 }` — installs the
  SIGSYS handler then the trusted-gate seccomp filter, exactly once, after the dispatcher is registered.
- `InProcessSeccomp` (`:66`) — the DEFAULT controller; `install()` (`:73-78`) calls
  `trap::install_handler(config.use_alt_stack)` then `SeccompFilter::for_trusted_gate(...).install()`.
- `HybridPtrace` (`:95`) — on main a **skeleton**: `install()` returns unsupported (test
  `hybrid_is_not_yet_installable` `:122-124`).

> **CORRECTION to the task prompt / prior docs:** the *shared* helper
> `install_in_process_trap(config)` — a single function both `InProcessSeccomp` and `HybridPtrace` call
> to install the identical guest-half trap — **does NOT yet exist on `55f6876a`.** On main the guest-half
> install is written **inline** inside `InProcessSeccomp::install` (`lifecycle.rs:73-78`). The shared
> `install_in_process_trap` helper is introduced by **reverie PR #377** (`[#377 branch]`
> `reverie-preload/src/lifecycle.rs:106`), which factors it out and makes `HybridPtrace::install` call it
> too. Until #377 lands, "both controllers install the identical trap via one helper" is a **branch
> claim, not a main fact.** The mechanism (handler + trusted-gate filter) is identical on main; only the
> shared *factoring* is pending.

---

## 3. PER-BACKEND CONFORMANCE AUDIT + GAPS (file:line @ `55f6876a`)

### 3.1 liteinst — CONFORMS (fail-closed IS in-guest SIGSYS today), with one counted residual class
- Dispatch taxonomy `enum LiteinstDispatchPath` (`reverie-liteinst/src/stats.rs:48-62`) already names
  the in-guest SIGSYS classes: `InGuestSigsys` (`:54`), `InGuestNestedSigsys` (`:56`), alongside
  `FirstSiteSeccomp` (`:50`), `PtraceInstallation` (`:52`), `CachelineStraddlerFallback` (`:58`),
  `UnpatchableOrOtherFallback` (`:60`), `DirectHook` (`:62` = FASTPATH).
- The un-instrumented **escape surface fails closed in-guest with `EOPNOTSUPP`**, NOT a ptrace trap:
  `reverie-liteinst/src/runtime.rs` — escape-surface doc `:863-907`, records the escape then returns
  `-EOPNOTSUPP` (`:1295`, `:1794`). This is the SIGSYS handler's terminal, an in-guest definite errno.
- **VERDICT: CONFORMS for the fail-closed path.** The un-instrumented/unpatchable fallback is in-guest
  SIGSYS (`InGuestSigsys` class) or a fail-closed in-guest errno, not ptrace.
- **Residual (counted, not on the fail-closed path):** `PtraceInstallation` (`stats.rs:52`) is a
  literal B-class per-syscall ptrace route that exists only because hermit still ships liteinst under the
  host-hybrid CLI wiring (`li-1`, `[hermit]` `hermit-cli/src/lib.rs`, per the audit doc). That is the
  CLI-flip gap (`liteinst_flip_cli_to`), separate from the fail-closed path. `CachelineStraddler` /
  `UnpatchableOrOther` are the genuinely-rare unpatchable-site classes; their target form is
  `InGuestSigsys` (already the in-tree class) — a ptrace round trip is permissible only if in-guest
  SIGSYS is *provably impossible* for a specific site, and must then be counted, not silent.

### 3.2 e9patch — GAP today (100% ptrace on main); CONFORMS once #377 lands (L0-only)
- On main e9patch is not an in-guest backend: `install_hybrid_runtime` gated
  `Unsupported`; production run drives `TracerBuilder::<Detcore>` (Tool in host) — the owner's cited
  worst case (per audit doc e9-1/e9-2/e9-3). `HybridPtrace::install` on main is the unsupported skeleton
  (`lifecycle.rs:122`). **VERDICT on main: VIOLATION (100% ptrace).**
- **reverie PR #377** (`feat/e9patch-hybridptrace-lifecycle-owner`, OPEN/draft) builds the A-class
  lifecycle owner and makes e9patch's fail-closed conform:
  - Shared guest-half trap factored into `install_in_process_trap` and installed by BOTH controllers
    (`[#377 branch]` `reverie-preload/src/lifecycle.rs:106`; `InProcessSeccomp` `:79`, `HybridPtrace`
    `:142`). "Both controllers install the identical guest-half in-process trap … they differ only in
    the *launcher*" (`lifecycle.rs:26-27,92-135`).
  - Launcher is **A-class `TracerBuilder::<()>`** (`[#377 branch]` `reverie-e9patch/src/backend.rs:939`),
    with the explicit contract comment (`:930-937`): *"the unit tool `()` declares no syscall
    subscriptions and hosts no `Tool` … Detcore runs entirely in-guest over the shared reverie-preload
    SIGSYS/seccomp seam. ptrace is used only to follow and reap the guest process tree … never on the
    syscall hot path; un-instrumented syscalls fail closed through the in-guest SIGSYS handler, not a
    ptrace trap."*
  - `RuntimeMode::HybridPtrace` selects the shared controller (`[#377 branch]`
    `reverie-e9patch/src/runtime.rs:195,263,359`); it "services un-rewritten `SIGSYS` sites only … does
    **not** … on the syscall hot path" (`runtime.rs:230,250-253`).
- **VERDICT: GAP now → CONFORMS on #377 merge.** Note it is **L0-only** (reverie-level) until the
  hermit CLI is flipped to select the e9patch in-guest runtime (the L3 hermit CLI flip, `[hermit]`
  `hermit-cli/src/bin/hermit/run.rs` runtime selection — the `E9patch→Ptrace` downgrade must be removed
  there). Until that flip, #377's conforming path is present in the crate but not exercised by
  `hermit run`.

### 3.3 sabre — RESIDUAL VIOLATION (sb-3); fail-closed is NOT yet in-guest SIGSYS
- The Detcore **Tool** runs in-guest (`[hermit]` `hermit-cli/src/lib.rs:994-1069`; `reverie_adapter.rs`
  in-process `set_regs`) — B(Tool-in-host) = 0. But the un-rewritten-site catch is a **persistent
  `PTRACE_SYSCALL` supervisor** (`[hermit]` `hermit-cli/src/sabre_ptrace.rs`, docstring "Ptrace safety
  net for syscall instructions missed by SaBRe rewriting"; 2 ptrace stops per syscall for the whole run,
  resume `ptrace::syscall` never `PTRACE_CONT`). **This IS the fail-closed path for sabre today, and it
  is a ptrace trap — the exact VIOLATION this spec forbids.**
- **TARGET (sb-3, INDEPENDENT of the shared host):** replace the persistent per-syscall ptracer with an
  **in-guest SIGSYS catch of raw un-rewritten `0f 05` sites** — the same seccomp-`RET_TRAP` +
  `sigsys_handler` mechanism (§2), so a raw syscall instruction at an untrusted RIP traps to the in-guest
  handler instead of a ptrace stop. Because a raw `0f 05` is distinguishable only by RIP/mapping (not by
  syscall number/args), the seccomp trusted-gate filter (which keys on RIP, `seccomp.rs:90-97`) is the
  natural fit: untrusted RIP → TRAP → in-guest handler rewrites the site (SaBRe marker) and redirects,
  exactly as the current supervisor does but **without ptrace**. Acceptable alternative per owner's
  "minimal rare fallback": a **bounded warm-up** ptracer that patches all reachable raw sites then
  **detaches** (A-class-ish lifecycle), converting a persistent per-syscall net into a one-time cost.
- **VERDICT: VIOLATION.** sb-3 is the residual; it does not depend on `shared_inguest_toolhost_family`
  and can be worked independently. Owner decision still open on whether bounded-warm-up counts as the
  allowed rare fallback vs. requiring the full in-guest-SIGSYS raw-site catch.

---

## 4. ⚠ THE rdpmc TRAP — READ THIS BEFORE TOUCHING IN-GUEST COUNTER READS

> ```
> ┌────────────────────────────────────────────────────────────────────────────┐
> │  TRAP: the in-guest RCB/PMU read MUST include the pc->offset term.          │
> │                                                                              │
> │  CORRECT:   count = pc->offset + sign_extend(rdpmc(pc->index - 1), width)   │
> │             read INSIDE the pc->lock seqlock (retry while seq is odd /       │
> │             while seq changed across the read).                             │
> │                                                                              │
> │  WRONG:     count = rdpmc(pc->index - 1)      // DROPS pc->offset           │
> │             → yields a ~2^47 garbage value that MIMICS A DENIAL.            │
> │             A naive probe then wrongly concludes "rdpmc unsupported /        │
> │             not readable in-guest" and LEAVES THE PTRACER IN THE SYSCALL     │
> │             PATH FOREVER — i.e. the offset bug silently defeats the entire   │
> │             zero-ptracer goal.                                              │
> └────────────────────────────────────────────────────────────────────────────┘
> ```

The offset-correct, sign-extended, seqlock-guarded read is **already implemented in-tree** — do NOT
hand-roll a bare `rdpmc()`:

- Primitive: `PerfCounter::ctr_value_rdpmc()` — `reverie-ptrace/src/perf.rs:420` (reverie PR #363,
  **MERGED**, verified live at `55f6876a`). Its loop body (`perf.rs:540-564`) does exactly the correct
  thing: reads `pc->offset` into `count` (`:542`), and iff `index != 0` and `cap_user_rdpmc`
  (`:545-550`) computes `raw = rdpmc(index - 1)` (`:560`), sign-extends from `pmc_width`
  (`let pmc = ((raw << (64 - width)) as i64) >> (64 - width);` `:561`), and `count =
  count.wrapping_add(pmc)` (`:562`) — the `offset + sign_extend(rdpmc)` form — all inside the seqlock
  (`seq & 1 == 0` acquire `:526-529`, `smp_rmb` + unchanged-seq re-check `:565-568`), with a
  descheduled-event panic guard (`running != enabled`, `:571-575`).
- **SPEC REQUIREMENT:** any in-guest RCB read in the shared driver
  (`reverie-preload::tool_host::drive_tool_syscall`, the RCB bracketing dance of the RCB design doc §3)
  **MUST route through `PerfCounter::ctr_value_rdpmc`** — which includes the offset term — and **NEVER**
  through a hand-rolled `rdpmc()` that drops it. This is the single mechanism that both (a) avoids the
  garbage-mimics-denial trap and (b) preserves the perf win.
- **Why it is load-bearing:** measured `rdpmc` ~9.8 ns vs `read()` ~264 ns median (devbig014 EPYC,
  release, `taskset -c 3`) ≈ **27× cheaper**. With 2 reads/handler at rdpmc the in-guest trap win holds
  at ~30× (vs ~19–20× if it fell to `read()`). The fail-closed/RCB scheme's whole point survives ONLY if
  reads go through the offset-correct rdpmc path.
- **Reachability blocker (unchanged at `55f6876a`, re-verified):** `mod perf` is **private**
  (`reverie-ptrace/src/lib.rs:45`; only `pub use perf::is_perf_supported` at `:59`), and
  `git grep -E "reverie_ptrace|PerfCounter|rdpmc|ctr_value" origin/main -- reverie-preload/` returns
  **zero** hits. So `ctr_value_rdpmc` is **unreachable from the shared host today.** An **additive**
  visibility change (a `pub(crate)`/re-export of `PerfCounter::ctr_value_rdpmc`) is the first commit of
  any RCB-wiring PR — it changes HOW a counter is read, not the Tool/Guest/Backend time-or-ordering
  contract, so it stays within additive Reverie API policy (RCB design doc §5/C2).

---

## 5. CONFORMANCE TEST PREDICATE (how a reviewer VERIFIES zero-ptracer fail-closed — not infers it)

The Proxy-Binding rule: bind the "fail-closed is in-guest" claim to observable dispatch/stop counts at an
exact SHA, and **bracket both sides**. A green here is a two-sided counted observation, not a label.

**Fixture (plant the un-instrumented case):** run a guest that executes a syscall the backend has NOT
rewritten/patched — e.g. a raw `0f 05` at an untrusted RIP for sabre, or a syscall whose site is on the
`UnpatchableOrOther`/`CachelineStraddler` class for liteinst, or (post-#377) any syscall on e9patch's
in-guest path from an un-rewritten site. Prefer an inert fixture that cannot itself authorize anything.

**Positive bracket (SIGSYS fires, in-guest):** the backend's dispatch counter shows an **in-guest SIGSYS
dispatch count > 0** for that run — e.g. liteinst `InGuestSigsys`/`InGuestNestedSigsys` count > 0
(`stats.rs:54,56`), or the e9patch fallback recorder incrementing on `source()==SignalTrap`
(`reverie-e9patch/src/dispatch.rs` FALLBACK_* on SignalTrap). Confirm the count is **not inert** (it
moved from a pre-run baseline).

**Negative bracket (no per-syscall ptrace stop):** the backend's **per-syscall ptrace-stop counter == 0**
on its slowpath taxonomy — liteinst `PtraceInstallation == 0` (`stats.rs:52`), and for sabre the
`SabreSlowPath` per-syscall ptrace variants (`PtraceSyscallEntry`/`Exit`/`RawSyscallRedirect`,
`reverie-sabre-stats/src/lib.rs:62-83`) == 0 for the flipped/target build. Equivalently: assert **no
`TracerBuilder<Detcore>`** on the run path and **no `ptrace::syscall`/`PTRACE_SYSCALL` resume** on the
syscall path (a run with a persistent net will show a nonzero entry+exit stop count).

**Vacuity guard (do not let a silent fastpath fake a green):** a run that never executes the
un-instrumented syscall proves nothing. Require the fixture to *demonstrably* hit the un-patched site
(the positive bracket count > 0); a `direct_hook`-only run with `in_guest_sigsys == 0` is NOT a
conformance pass, it is a no-result. (This is the `silent-fastpath-fallback-needs-observable-signal`
lesson.) Note the vacuity compound guard `direct_hook==N+1 && ptrace_installation==0` from the older
build spec is **not present in-tree** at `55f6876a` (confirmed absent per the stats-transport map §6) —
the reviewer must assert the two-sided counts explicitly.

**Bind to the SHA:** report the reverie SHA (and hermit SHA for the CLI path), the exact command, and the
before/after counts on both sides. A `pass` that does not carry the executed un-instrumented-syscall count
and both stop counts is a proxy, not a verification.

---

## 6. REMAINING SHARED-CODE (property 1) INCREMENTS — architecture vs. measurement split

Owner directive: "ptracer OUT first, measure AFTER." This section separates work that is **pure
architecture/code (dispatchable now, no validate needed to justify)** from work that is
**measurement/stats-transport (correctly deferred)**. Baseline: the shared driver hoist (build-spec
increments 1–4: `SpinMutex` hoist, `reverie-preload::tool_host` driver + ERESTARTSYS loop, liteinst
wired on) landed via reverie #373 and is present at `55f6876a` (`tool_host.rs` `drive_ready`/
`drive_syscall`/`drive_tool_syscall`/`classify_outcome`). What remains:

### 6.1 ARCHITECTURE / CODE — dispatchable now (ptracer-OUT / SHARED-CODE; NOT measurement)
| # | Work | Where | Task / status |
|---|---|---|---|
| A1 | **Land reverie #377** — e9patch A-class `TracerBuilder<()>` lifecycle owner + shared `install_in_process_trap` factored so both controllers install the identical guest-half trap. Removes e9patch's 100%-ptrace fail-closed. | `[#377 branch]` `reverie-preload/src/lifecycle.rs`, `reverie-e9patch/src/{backend.rs,runtime.rs}` | `e9patch_hybridptrace_inguest_converge` — OPEN draft PR #377 |
| A2 | **rdpmc visibility change** — additive `pub(crate)`/re-export of `PerfCounter::ctr_value_rdpmc` so the shared host can call the offset-correct read (§4). Additive API, no contract change. | `reverie-ptrace/src/lib.rs:45,59` → reachable from `reverie-preload` | first commit of the RCB-wiring PR; unblocked now (C2 open half) |
| A3 | **Wire the RCB bracketing dance** (two offset-correct rdpmc reads + `tool_debt` deduction) into the SHARED driver only, per RCB design §3, so every Family-A backend inherits one copy. | `reverie-preload/src/tool_host.rs` (around the `drive_tool_syscall` entry `:222`) | unblocked (C1 satisfied); gated only on A2 for the read primitive |
| A4 | **hermit CLI flip for e9patch (L3)** — remove the `E9patch→Ptrace` downgrade and select the in-guest runtime, exercising #377's conforming path end-to-end. | `[hermit]` `hermit-cli/src/bin/hermit/run.rs` | follows A1 |
| A5 | **hermit CLI flip for liteinst (li-1→li-2)** — dispatch liteinst via the in-guest `run_with_preload::<Detcore>` instead of `run_host_with_preload::<Detcore>`; retires the `PtraceInstallation` route. | `[hermit]` `hermit-cli/src/lib.rs` | `liteinst_flip_cli_to` — REMOVABLE-NOW (in-guest host exists+tested) |
| A6 | **sabre sb-3** — replace the persistent `PTRACE_SYSCALL` net with in-guest SIGSYS raw-site catch OR bounded warm-up-then-detach (§3.3). Independent of the shared host. | `[hermit]` `hermit-cli/src/sabre_ptrace.rs` | open; owner decision on rare-fallback shape |
| A7 | **liteinst in-guest event gaps** that still force host after the CLI flip — timer/PMU preemption (RCB clock fixed at 0, `reverie-liteinst/src/tool_host.rs` `read_clock→0`/`set_timer` no-op), CPUID/RDTSC(P)/RDRAND/RDSEED routing, thread clone3/vfork/exec bootstrap/vDSO. These are core determinism (in-guest events), not measurement; they depend on the shared host's event routing. | `reverie-liteinst` per its `CLAUDE.md` Supported Boundary | NEEDS-SHARED-HOST-FIRST |

### 6.2 MEASUREMENT / STATS-TRANSPORT — correctly deferred ("measure AFTER")
| # | Work | Where | Why deferred |
|---|---|---|---|
| M1 | **Non-Option `slowpath_counter()` seam on `HostBackend`** — the mandatory counter accessor so no converging backend can silently drop per-path counts. Documented-but-unbuilt (`tool_host.rs:48-52` names it as future work; no `HostBackend`/`HostSyscallEvent` trait exists yet at `55f6876a`). | `reverie-preload/src/tool_host.rs` | it is the measurement seam; per owner it is the "measure AFTER" half and must not jump ahead of ptracer-OUT (A1–A6). Build it co-designed with the first converging backend. |
| M2 | **`unify_backend_stats_transport`** — fold slowpath-counter submission into the shared exit path; Family A = RPC `GlobalTool` producer, Family B (sabre) keeps its shmem-memfd engine, both conforming to the ONE `BackendStatsSource` contract (NOT one wire, and NOT one taxonomy — sabre keeps `SabrePatchRoute`/`SabreSlowPath`; unified only at `CounterSnapshot<K>`). | `reverie/src/backend_stats.rs`, `reverie-liteinst/src/stats.rs`, `reverie-sabre-stats/src/lib.rs`, `reverie-e9patch/src/dispatch.rs` | pure measurement transport; deferred. e9patch's RPC exit wire is unvalidatable E2E until its in-guest path is exercised (post-A4). |

**The clean tee-up for the coordinator's next dispatch:** the *architecture* frontier that removes the
ptracer from the fail-closed/syscall path is **A1 (land #377) → A4/A5 (CLI flips) → A6 (sabre sb-3)**,
with **A2+A3** (rdpmc visibility + shared RCB dance) as the determinism-not-measurement companion,
unblocked now. Everything in §6.2 (M1/M2) is measurement and stays behind that frontier per the owner's
"measure AFTER."

---

## 7. Acceptance summary (one line per backend, bound to `55f6876a`)

| Backend | Fail-closed path today | Verdict | To conform |
|---|---|---|---|
| **liteinst** | in-guest SIGSYS / in-guest `EOPNOTSUPP` escape (`InGuestSigsys` class; `runtime.rs:1295,1794`) | **CONFORMS** (fail-closed). Residual `PtraceInstallation` is the CLI-flip gap, not the fail-closed path. | A5 CLI flip + A7 event gaps |
| **e9patch** | 100% ptrace (`TracerBuilder<Detcore>`; `install_hybrid_runtime`→Unsupported on main) | **GAP now → CONFORMS on #377** (A-class `TracerBuilder<()>` + shared in-guest SIGSYS trap); **L0-only** until A4 | A1 land #377, then A4 CLI flip |
| **sabre** | persistent `PTRACE_SYSCALL` net catching raw `0f 05` sites (`hermit-cli/src/sabre_ptrace.rs`) | **VIOLATION** (fail-closed is a per-syscall ptrace trap) | A6: in-guest SIGSYS raw-site catch OR bounded warm-up-then-detach |
