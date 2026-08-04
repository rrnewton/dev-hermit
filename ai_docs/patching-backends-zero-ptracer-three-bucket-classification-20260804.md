# Patching backends — zero-ptracer gate, THREE-BUCKET classification

**Task:** `unified-in-guest-patching-backend` (P0, OWNER ARCHITECTURE GATE). **Mode: enumeration only — no code.**
**Author:** impl agent, opus-4.8, 2026-08-04 ~20:45Z. **Repos/HEAD:** reverie `04a46b43`, hermit `f80b1c09` (both primaries on main).
**Method:** direct Read of every cited anchor at current HEAD; builds on the A/B site enumeration in
`ai_docs/patching-backends-ptrace-on-syscall-path-audit-20260804.md` (companion — the raw site list) and adds the
owner-requested three-way separation.

## Owner framing (this spawn)

> The final form of sabre/e9patch/liteinst = (1) SHARED CODE, (2) **ZERO PTRACER, or absolutely minimal if there
> is some rare fallback needed**. A backend that needs a ptrace round trip on the syscall path is NOT
> architecturally correct and NOT ready for perf work. Hammer THAT into shape, then measure overheads. ALL perf
> work on the three patching backends is SUSPENDED until then.

**The deliverable is the enumeration, not more code.** Per backend, every remaining place the ptracer sits on the
syscall path, each placed in exactly one of three buckets:

- **[REMOVABLE NOW]** — the ptracer can be taken off the syscall path with infrastructure that already exists; no
  new shared host needed. A wiring/CLI change, not new design.
- **[NEEDS SHARED HOST FIRST]** — removal is blocked on `shared_inguest_toolhost_family` converging (the one
  in-guest Tool host + its ERESTARTSYS/counter seam from PR #373) and/or the in-guest event routing that host
  enables. Cannot be done before the shared host lands.
- **[RARE FALLBACK]** — the owner-allowed residual: a fallback that fires *rarely* (NOT per-syscall) for cases
  that genuinely cannot be handled in-guest. Target form is an in-guest SIGSYS trap (or a bounded warm-up),
  keeping the ptracer off the hot path.

**Current state of the shared host:** PR #373 (`rrnewton/reverie`, branch `feat/shared-inguest-toolhost`, head
`b06e0972`) is **OPEN / not merged** — it hoists the Level-1 shared driver (`drive_tool_syscall` +
`classify_outcome` ERESTARTSYS loop) into `reverie-preload` and wires LiteInst onto it. Everything marked
[NEEDS SHARED HOST FIRST] is gated on #373 landing plus the per-backend wiring increment (4 = e9patch).

---

## e9patch — WORST: 100% of syscalls take a ptrace round trip

| # | Site | file:line | Bucket | Note |
|---|---|---|---|---|
| e9-1 | `runtime_backend()` hard-downgrades `E9patch → Ptrace`; Detcore then runs host-side under `TracerBuilder::<Detcore>` | `hermit-cli/src/bin/hermit/run.rs:1714-1720` | **NEEDS SHARED HOST FIRST** | Cannot delete the downgrade today — the in-guest path (e9-2) is `Unsupported`, so removal = a broken backend. Resolves only when the in-guest host exists and e9patch's `HybridPtrace` lifecycle owner is built. |
| e9-2 | `install_hybrid_runtime()` returns `io::ErrorKind::Unsupported`; in-guest fast path dormant (comment: *"ptrace performs all event handling"*) | `reverie-e9patch/src/runtime.rs:259-262` | **NEEDS SHARED HOST FIRST** | + e9patch-specific `HybridPtrace` lifecycle owner (A-class `TracerBuilder<()>`-shaped). The in-guest driver it must call IS the shared host from #373. Largest single lift (`e9patch_hybridptrace_inguest_converge`). |
| e9-3 | In-guest host maps `err → -errno` with no ERESTARTSYS/`wait4` re-dispatch ⇒ app **errno 512** once in-guest lands | `reverie-e9patch/src/tool_host.rs:347-349` (confirmed at 04a46b43: `Err(error) => -i64::from(errno.into_raw())`) | **NEEDS SHARED HOST FIRST** (zero extra design) | The fix is ALREADY WRITTEN ONCE in #373's shared `drive_tool_syscall`/`classify_outcome`. e9patch inherits it for free by being wired onto the shared driver in increment 4 — no new design, bundled into e9-2's convergence. This is the errno-512 seam the spawn flagged. |

**None of e9patch's sites are REMOVABLE NOW and none is a legitimate RARE FALLBACK** — the per-syscall ptrace is
100% of syscalls, a pure infrastructure gap. e9-1/e9-2/e9-3 all clear together via the shared host + HybridPtrace
owner. DETLOG-via-ptrace (owner's cited instance) is a direct consequence of e9-1, not a separate mechanism.

---

## LiteInst — shipped host-hybrid; in-guest host exists but hermit does not call it

### The shipped Tool-in-host route (clears with the CLI flip)

| # | Site | file:line | Bucket | Note |
|---|---|---|---|---|
| li-1 | hermit-cli dispatches LiteInst via `run_host_with_preload::<Detcore>` — Tool driven from the ptrace HOST | `hermit-cli/src/lib.rs:1531-1546` | **REMOVABLE NOW** | The in-guest `run_with_preload::<Detcore>` target (li-2) already EXISTS, is tested (`reverie-liteinst/tests/rpc_tool.rs`), and already carries the #362 ERESTARTSYS fix. The flip is a hermit-cli change (`liteinst_flip_cli_to`) that does NOT require the shared-host hoist — liteinst's own in-guest host works today. **Necessary but NOT sufficient** — the gaps below still force host after the flip. |
| li-2 | in-guest `run_with_preload::<Detcore>` path — no hermit caller | `reverie-liteinst/src/backend.rs:362` | (target A) | Destination of li-1. |
| li-3 | Host-side dispatch-path counters recorded in the PTRACE crate via `from_ptrace_host_hybrid` — proof the host observes syscalls | `reverie-ptrace/src/liteinst_stats.rs:113-129`; `reverie-liteinst/src/backend.rs:275,350` | **REMOVABLE NOW** | Consequence of li-1: these counts only exist because the host observes syscalls in host-hybrid mode. Falls away with the flip; the `ptrace_installation` slowpath class is the literal B route. |

### In-guest event GAPS that still force the host EVEN AFTER the CLI flip

Per `reverie-liteinst/CLAUDE.md` (Supported Boundary) + `tool_host.rs`:

| Gap | file:line | Bucket | Note |
|---|---|---|---|
| Timer/PMU preemption; **RCB clock fixed at 0** (`read_clock → 0`, `set_timer` no-op) | `reverie-liteinst/src/tool_host.rs:887-903` | **NEEDS SHARED HOST FIRST** | Core determinism (deterministic preemption), NOT a rare fallback. In-guest RCB read needs `rdpmc` (reverie #363 primitive). **This work DEPENDS on the shared host** — do not schedule in-guest RCB/timer before `shared_inguest_toolhost_family`. |
| CPUID, RDTSC/RDTSCP, RDRAND/RDSEED not routed as in-guest events | `reverie-liteinst/CLAUDE.md` (boundary) | **NEEDS SHARED HOST FIRST** | Frequent instructions (esp. CPUID/RDTSC) — must be in-guest events, not rare. Routed via in-guest trap once the shared host's event routing exists. |
| Thread clone/clone3, vfork, exec bootstrap, vDSO interception | `reverie-liteinst/CLAUDE.md` (boundary) | **NEEDS SHARED HOST FIRST** | Lifecycle/bootstrap events the shared in-guest host must route. Essential, not rare-fallback. |
| Unpatchable-site fallback; `cacheline_straddler` (site cannot be atomically patched) | `stats.rs:111` classes `unpatchable_or_other` / `cacheline_straddler` | **RARE FALLBACK** | A site that genuinely cannot be patched is the owner-allowed rare case. **Target form = in-guest SIGSYS trap** (`in_guest_sigsys` class), NOT ptrace — so the ideal steady state keeps the ptracer off entirely. Only if in-guest SIGSYS is impossible for a site does a ptrace round trip become the minimal rare fallback. |

⇒ Flipping li-1 is REMOVABLE NOW, but "zero ptracer for LiteInst" is gated on the [NEEDS SHARED HOST FIRST] gaps
(timer/PMU + CPUID/RDTSC + clone3/vfork/exec/vDSO) plus reducing the unpatchable fallback to in-guest SIGSYS.

---

## SaBRe — Detcore Tool IS in-guest (conforms), but a persistent per-syscall ptrace net remains

| # | Site | file:line | Bucket | Note |
|---|---|---|---|---|
| sb-1 | `run_sabre` builds `GlobalState`+`RpcServer`+SaBRe plugin; **no `TracerBuilder<Detcore>`** | `hermit-cli/src/lib.rs:994-1069` | (already A) | Detcore Tool runs in-guest. B(Tool-in-host) = 0. |
| sb-2 | in-process `set_regs` rejects RIP/RSP `EOPNOTSUPP`; memory via `process_vm_*` | `reverie_adapter.rs:1171` | (already A) | Conforms. |
| sb-3 | **Persistent `PTRACE_SYSCALL` "safety net" supervisor — 2 ptrace stops (entry+exit) per syscall, whole run** | `hermit-cli/src/sabre_ptrace.rs:148-441` (docstring :9 *"Ptrace safety net for syscall instructions missed by SaBRe rewriting"*; `PTRACE_O_TRACESYSGOOD` :341; resume `ptrace::syscall` never `PTRACE_CONT` at :165/:406/:428) | **RARE FALLBACK (candidate) — current form is a per-syscall VIOLATION** | See below. |

**sb-3 is the one genuinely-undecided site, and the analytical crux of this deliverable: separate the fallback
NEED from the over-broad IMPLEMENTATION.**

- **The need is legitimately a RARE FALLBACK:** its job is to catch raw un-rewritten `0f 05` syscall sites that
  SaBRe's static + load-time rewriting missed. That is exactly the "site that could not be patched" rare case the
  owner allows. It does NOT run Detcore host-side and does NOT depend on the shared tool host (it is an
  independent hermit-cli supervisor).
- **But its current implementation is NOT rare:** it stops on *every* syscall entry+exit for the whole run
  (lightweight per stop — `getregs` + 2-byte read + cached `is_trusted_mapping`), so it does not meet the "minimal
  rare fallback" bar. It cannot become seccomp-lazy: a raw un-rewritten `0f 05` is distinguishable only by
  RIP/mapping, not syscall number/args, so catching it needs a per-syscall stop.

Two reduction paths (OPEN owner decision — not removable now, not shared-host-gated):
  1. **Prove SaBRe static+load-time rewriting is exhaustive** ⇒ drop the net entirely → becomes **REMOVABLE**
     (needs an exhaustiveness proof / audit).
  2. **Bounded warm-up:** patch all reachable raw sites, then **detach** the supervisor ⇒ converts the persistent
     per-syscall net into a genuine bounded/**RARE FALLBACK** (A-class lifecycle-ish).

Until one path lands, SaBRe — the supposed conforming reference — is **not** zero-ptracer.
(`SabreSlowPath` already enumerates `PtraceSyscallEntry/Exit/RawSyscallRedirect/InstalledSigillDispatch` as counted
slow paths at `reverie-sabre-stats/src/lib.rs:62-83` — the design knows these are per-syscall ptrace routes.)

---

## Summary table (per backend, per bucket)

| Backend | REMOVABLE NOW | NEEDS SHARED HOST FIRST | RARE FALLBACK (allowed residual) |
|---|---|---|---|
| **e9patch** | — | e9-1 downgrade, e9-2 dormant in-guest (+HybridPtrace owner), e9-3 errno-512 (free via #373) | — |
| **LiteInst** | li-1 CLI flip, li-3 host counters | timer/PMU (RCB=0), CPUID/RDTSC, clone3/vfork/exec/vDSO | unpatchable / cacheline_straddler → **in-guest SIGSYS** (ptrace only if SIGSYS impossible) |
| **SaBRe** | — (candidate: drop net if rewriting proven exhaustive) | — (independent of shared host) | sb-3 net — MUST be reduced from persistent per-syscall to bounded warm-up/rare; currently a violation |

## Ordering (dependency the spawn asked to be recorded on the task)

1. **`shared_inguest_toolhost_family`** (PR #373 open) — the one in-guest host + ERESTARTSYS + mandatory non-Option
   counter seam. Gates everything marked [NEEDS SHARED HOST FIRST].
2. **`liteinst_flip_cli_to`** — li-1 flip is [REMOVABLE NOW] and can proceed once #373 lands; then the LiteInst
   [NEEDS SHARED HOST FIRST] gaps (timer/PMU, CPUID/RDTSC, clone3/vfork/exec/vDSO). **In-guest RCB/timer work
   DEPENDS on the shared host — must not start before #373.**
3. **`e9patch_hybridptrace_inguest_converge`** — largest lift: build the `HybridPtrace` lifecycle owner, inherit
   ERESTARTSYS from the shared driver (clears e9-3 for free), then remove the e9-1 downgrade.
4. **SaBRe sb-3** — independent of 1-3; needs a SaBRe-specific exhaustiveness proof OR bounded warm-up. Owner
   decision required on whether a bounded warm-up counts as the allowed "minimal rare fallback."
5. **`unify_backend_stats_transport`** — fold slowpath-counter submission into the shared exit path; SaBRe keeps
   its shmem-memfd engine conforming to the one `BackendStatsSource` contract.

**Gate for "ready for perf work" per backend = every non-RARE-FALLBACK row above eliminated (or a RARE FALLBACK
row reduced to a counted, justified, genuinely-rare in-guest/bounded form). Until then perf work stays SUSPENDED.**
