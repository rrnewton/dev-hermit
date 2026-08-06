# In-guest RCB read wiring (A2 + A3) — TURNKEY BUILD SPEC

**Task:** `shared_inguest_toolhost_family` (the (1) SHARED-CODE half), milestone
`unified-in-guest-patching-backend` (OWNER ARCHITECTURE GATE). **Mode: SPEC only — pure design +
code; no validate, no slot mutation.** Ptracer OUT first, measure AFTER.
**Author:** coordinator/architecture-gate, opus-4.8, 2026-08-05.
**Re-anchored live @ reverie `origin/main` = `55f6876a31fc396083ebe2266d8bd6c91075bcf9`** (fetched via
proxy; every file:line below verified with `git show origin/main:<path>` / `git grep origin/main`).

## Provenance — what this finishes

This is the **turnkey build spec** for the read-side of the RCB (retired-conditional-branch)
accounting the two prior design docs specified but did not reduce to a buildable, correctly-homed
increment:

- `ai_docs/in-guest-rcb-accounting-zero-ptracer-design-20260804.md` — the bracketing dance (DESIGN,
  anchored to `8688189a`). §3 = the two-read dance; §5 = "read via `ctr_value_rdpmc`, not `read()`";
  C2 = the reachability wall. **This doc turns §3/§5 into code and CORRECTS §5's proposed home.**
- `ai_docs/patching-backends-failclosed-inguest-sigsys-spec-20260805.md` — §4 encodes the rdpmc
  trap; §6.1 lists A2 (rdpmc visibility) + A3 (shared RCB dance) as "dispatchable now, no validate
  needed." This doc specifies A2/A3 concretely.
- `ai_docs/shared-inguest-toolhost-build-spec-20260804.md` — increments 1–4 (the shared driver hoist)
  **LANDED via reverie #373**; `drive_tool_syscall` lives at `reverie-preload/src/tool_host.rs:222`.
  This is the fifth architecture increment on top of that landed driver (independent of the M1/M2
  stats increment, which is measure-AFTER).

**Why this is the "ptracer-out" lever (not measurement):** the fail-closed SIGSYS spec removes the
ptracer from the *un-instrumented-syscall* path. RCB accounting removes it from the *preemption /
virtual-time* path: an in-guest tool that cannot read its own retired-branch counter **in-guest**
must call the ptrace host to read `perf_event` — the ptracer stays in the loop. Reading the counter
correctly in-guest via `rdpmc` is what lets timer/PMU preemption run with zero ptracer. This is
architecture/determinism, not measurement.

---

## 1. THE rdpmc TRAP (encode it; it is the whole reason A2 is delicate)

```
┌────────────────────────────────────────────────────────────────────────────┐
│  CORRECT:  count = pc->offset + sign_extend(rdpmc(pc->index - 1), pmc_width) │
│            read INSIDE the pc->lock seqlock (retry while seq is odd, and     │
│            re-check seq unchanged across the read).                          │
│                                                                              │
│  WRONG:    count = rdpmc(pc->index - 1)          // DROPS pc->offset        │
│            → ~2^47 garbage that MIMICS A DENIAL. A naive probe concludes     │
│            "rdpmc unsupported / not readable in-guest" and LEAVES THE        │
│            PTRACER IN THE SYSCALL/PREEMPTION PATH FOREVER — the offset bug   │
│            silently defeats the entire zero-ptracer goal.                    │
└────────────────────────────────────────────────────────────────────────────┘
```

The offset-correct, sign-extended, seqlock-guarded, descheduled-guarded read **already exists**
in-tree — do NOT hand-roll a bare `rdpmc()`. It is `PerfCounter::ctr_value_rdpmc` →
`ctr_value_rdpmc_loop` at `reverie-ptrace/src/perf.rs:420` / `:504` (reverie #363, MERGED,
re-verified at `55f6876a`). The loop body does exactly the correct thing:
`count = (*ptr).offset` (perf.rs:~532); iff `index != 0 && cap_user_rdpmc` then
`raw = rdpmc(index-1)` (~:558), `pmc = ((raw << (64-width)) as i64) >> (64-width)` (sign-extend, ~:560),
`count = count.wrapping_add(pmc)` (~:561); all inside the `seq & 1 == 0` seqlock (~:519/:566), with the
`running != enabled` descheduled-event panic guard (~:570). **A2 exists solely to make this exact code
reachable in-guest without duplicating (and mis-copying) it.**

---

## 2. A2 — where the read primitive lives (CORRECTION to the design docs)

### 2.1 The refuted assumption
Both prior docs proposed "an additive `pub(crate)` hoist **or a re-export of
`PerfCounter::ctr_value_rdpmc`** [from `reverie-ptrace`]." **That home is wrong**, for a checkable
crate-graph reason verified @ `55f6876a`:

- `reverie-preload/Cargo.toml` (the in-guest `LD_PRELOAD` cdylib) depends on **only** `reverie-core`
  (optional, behind `coordinator-rpc`). It has **zero** dep on `reverie-ptrace`, and
  `git grep -E "reverie_ptrace|PerfCounter|rdpmc|perf_event" origin/main -- reverie-preload/` → **0 hits**.
- `reverie-ptrace/Cargo.toml` is the **host tracer**: `tokio` (full), `nix` (ptrace/…), `tokio-stream`,
  `perf-event-open-sys`, `reverie-core`. Its `perf` module (`reverie-ptrace/src/perf.rs:31-40`) further
  pulls `nix::sys::signal`, `crate::validation::check_for_pmu_bugs`, `tracing`.

Re-exporting `PerfCounter` from `reverie-ptrace` forces `reverie-preload` to take a **dependency on the
entire host tracer** (tokio + nix-ptrace + validation). That is a **layering inversion** — the in-guest
lib pulling in the host — bloating the preload `.so` and coupling the guest to the tracer it is meant to
replace. **REJECT A2-γ (re-export from reverie-ptrace).**

### 2.2 What the read actually needs (so we can home it correctly)
`PerfCounter { fd: c_int, mmap: Option<NonNull<perf_event_mmap_page>> }` (perf.rs:~84). The **read
path** (`ctr_value_rdpmc` / `_loop` / `rdpmc` / `read_once` / `smp_rmb`) needs only:
`perf-event-open-sys` (the `perf_event_mmap_page` bindgen struct + the `rdpmc` intrinsic constants),
`reverie::Errno` (or any Errno), `libc`, and the in-tree seqlock helpers. It does **not** need
tokio/nix-ptrace/validation. The **constructor** (`Builder`, perf.rs:~112+) additionally needs
`perf_event_open` + `mmap` (libc) and, optionally, `check_for_pmu_bugs` (validation) and the SIGIO
owner/`F_SETSIG` setup (only for the alarm, §4 of the RCB design doc).

### 2.3 The correct home (RECOMMENDED: A2-α, new shared crate)
Extract the counter primitive into a **new lightweight crate `reverie-perf`** that BOTH
`reverie-ptrace` and `reverie-preload` depend on (no cycle: `reverie-ptrace` does NOT depend on
`reverie-preload`, verified). Contents:

- `pub struct InGuestPerfCounter { fd, mmap }` (or keep the name `PerfCounter`);
- `pub fn ctr_value_rdpmc(&self) -> Result<u64, Errno>` + the `#[cfg(target_arch="x86_64")]`
  `ctr_value_rdpmc_loop` + `rdpmc` intrinsic + `read_once`/`smp_rmb` — **moved verbatim** from
  `perf.rs` (one copy, offset term intact);
- `pub fn open_rcb_pinned(pid, cpu) -> Result<Self, Errno>` — a minimal constructor doing
  `perf_event_open(Event::Raw(0x5100d1), pinned=1, disabled-then-ENABLE)` + `mmap` of the metadata
  page; PMU-bug validation behind a feature/param (not needed on the hot read path);
- deps: `perf-event-open-sys`, `libc`, and an `Errno` (re-export `reverie_core::Errno` or define a
  thin one). NO tokio, NO nix-ptrace, NO tracing on the read path.

`reverie-ptrace/src/perf.rs` then **re-uses** `reverie_perf` for the read + rdpmc (deleting its private
copies — enforces C3 "one copy" below). `reverie-preload` gains `reverie-perf` as a dependency and can
open + read the counter in-guest.

- **A2-β (fallback):** move the primitive into `reverie-preload` itself and have `reverie-ptrace`
  depend on `reverie-preload` for it. Works (no cycle) but inverts the more natural direction (host
  depending on the guest lib) and makes `reverie-preload` own a type the host also constructs. Prefer
  A2-α unless a new crate is disallowed.
- **A2-γ (re-export from reverie-ptrace): REJECTED** (§2.1).

**Additive-API check:** extracting/relocating an existing read primitive changes **HOW a counter is
read**, not the `Tool`/`Guest`/`Backend` time-or-ordering contract → within additive Reverie API
policy (same argument the RCB design doc §5 made for the visibility change; this doc corrects the
mechanism to a crate extraction so the "one shared copy" property (1) actually holds).

---

## 3. A3 — wire the bracketing dance into the SHARED driver (exact placement)

The two reads + deduction live in the **one** shared entry every Family-A backend routes through:
`reverie-preload::tool_host::drive_tool_syscall<T,G>` (`tool_host.rs:222`). Its body @ `55f6876a`:

```rust
pub fn drive_tool_syscall<T, G>(tool: &T, guest: &mut G, syscall: Syscall,
                                number: Sysno, tail: &TailResult) -> DrivenSyscall
where T: Tool, G: Guest<T> {
    loop {                                                    // ERESTARTSYS restart loop (#362)
        let outcome = drive_syscall(tool.handle_syscall_event(guest, syscall), tail);
        if let Some(driven) = classify_outcome(number, outcome) { return driven; }
    }
}
```

**The loop is the ideal bracket:** `handle_syscall_event` (and any injected syscalls) run *inside* the
loop; a `wait4` `ERESTARTSYS` re-runs it. So one baseline **before** the loop and one end **after** it
brackets the **whole** dance across restarts — no per-iteration accounting needed.

- **Read #1 = baseline `B`** — first statement of the function, before `loop`. Clean by construction:
  the installed patch is an unconditional `jmp/call` (does not increment RCB), so the driver prologue up
  to here contributes ~0 guest-attributable branches (RCB design §3 "clean-baseline property").
- **Read #2 = end `E`** — capture `driven` from `classify_outcome`, read the counter, deduct, then
  return `driven`. (Restructure the early `return driven` into a `break`+trailing block so `E` runs on
  every terminal path, including `Fatal`/`Exit`/`ForkChild`.)
- **Deduct:** `tool_debt += (E - B) - C_read`, `C_read` = the two reads' own counter cost (measure once
  at counter open, store on the handle) so the dance is self-neutral.
- **Guest clock:** `guest_clock() = raw_rcb_now() - tool_debt`, monotone, never rebased (RCB design §6).

### 3.1 The counter handle is NOT threaded in today — this is the real code work
`drive_tool_syscall` takes `(tool, guest, syscall, number, tail)`; there is **no** RCB counter handle.
A3 must supply one **without** re-implementing per backend. Two options; pick per review:

- **A3-i (RECOMMENDED): thread-local in `reverie-preload`.** A `thread_local!` holding
  `Option<reverie_perf::InGuestPerfCounter>`, lazily opened (pinned RCB, own tid/core) on first
  subscribed syscall on that thread. `drive_tool_syscall` reads it at entry/exit. Keeps the shared
  signature stable and matches the async-signal-safe, per-thread nature of the in-guest host; the
  handler already runs on the trapped thread. Open failure ⇒ record a slowpath class and fall back to
  the `read()` path once (never silently to ptrace — see the trap, §1).
- **A3-ii: explicit parameter.** Add `rcb: Option<&InGuestRcb>` to `drive_tool_syscall` + a sibling
  per-thread `tool_debt` cell. More testable (the existing `classify_outcome` unit tests stay pure),
  but touches every call site (liteinst `tool_host.rs`, e9patch `tool_host.rs`) — still ONE
  implementation, just an extra arg.

Either way the deduction + `guest_clock()` live **only** in `reverie-preload` (C3).

### 3.2 Gating (do not conflate with the mandatory stats counter)
The RCB dance runs only when the run needs RCB-based virtual time / preemption (Detcore scheduling with
RCB on), release build (RCB event `0x5100d1`, `pinned=1`; a debug build adds one syscall read for a
`debug_assert_eq!`). It is legitimately **conditional per run-config** — a `strace`/compat tool with no
RCB scheduling opens no counter. This is **distinct** from the §5 *slowpath-stats* counter of the
shared-toolhost build spec, which is `NON-Option` (a converging backend must never silently drop
per-path counts). Do not merge the two: RCB-read = determinism, conditional; slowpath-stats =
measurement, mandatory.

### 3.3 The single in-guest Detcore subscriber (#1095 tripwire)
The one in-guest `Detcore` subscriber reads `guest_clock()` (monotone). This changes **WHERE DETLOG is
emitted**, not the time model. Validate clock parity on the **EVOLUTION** of the clock across the run,
never a single post-exec sample (PR #1095 fake-determinism lesson).

---

## 4. Ordered build increments (each independently compilable)

1. **A2-α: create `reverie-perf`** with `InGuestPerfCounter` + `ctr_value_rdpmc`/`_loop`/`rdpmc` +
   `open_rcb_pinned`. Point `reverie-ptrace/src/perf.rs` at it (delete its private read/rdpmc copies).
   Zero behavior change for the host; validates the crate plumbing. (reverie-ptrace perf tests
   `perf.rs:~857,:920` keep passing.)
2. **A2 wire-up:** add `reverie-perf` dep to `reverie-preload`; confirm the in-guest lib links the read
   primitive (unit test that `ctr_value_rdpmc` returns the offset-correct value on a pinned counter, or
   `#[ignore]` where no PMU).
3. **A3 counter handle:** add the per-thread counter (A3-i) or the param (A3-ii) to
   `reverie-preload::tool_host`; open lazily; measure `C_read` at open.
4. **A3 bracket:** insert Read #1 (entry) + Read #2 (all terminal paths) + `tool_debt` deduction +
   `guest_clock()` in `drive_tool_syscall`; keep `classify_outcome` pure/tested.
5. **Expose `guest_clock()`** to the single in-guest Detcore subscriber; parity-check on clock
   evolution (§3.3), not a single sample.

**Landing:** A1 (reverie #377, e9patch A-class lifecycle owner) and the CLI flips (A4/A5) are the
LAST steps of the wider zero-ptracer arc and are **frozen/parked** per the owner (patching cluster
lands LAST). A2/A3 land as a reverie PR on their own merit (read-mechanism refactor + shared RCB wiring);
they are the determinism companion, unblocked now, and do NOT wait on the CLI flips. When the freeze
lifts, this is the buildable increment.

---

## 5. Third-party-checkable acceptance conditions (extends the RCB design §9)

| id | condition | check |
| --- | --- | --- |
| C1 | shared host exists | `git -C reverie merge-base --is-ancestor 9a7c0aa7 origin/main` → rc 0 (PASSES). |
| C2 | rdpmc primitive landed | `perf.rs:420` `ctr_value_rdpmc` (#363, MERGED @ `55f6876a`). |
| **C2′** | **read primitive reachable in-guest via the CORRECT home** | `reverie-preload` depends on `reverie-perf` (NOT `reverie-ptrace`); `git grep reverie_ptrace -- reverie-preload/` → 0 hits stays true. **Fails today** (no `reverie-perf` yet) — A2-α is the sole open half. |
| C3 | read+deduct is SHARED, one copy | the two reads + `tool_debt` deduction appear in `reverie-preload/src/tool_host.rs` only; the rdpmc read+offset term appears in `reverie-perf` only; `git grep -E "rdpmc|offset.*wrapping_add" -- reverie-ptrace/src/perf.rs reverie-liteinst reverie-e9patch` → 0 private copies. |
| C4 | offset term present (anti-trap) | the moved read still computes `offset + sign_extend(rdpmc(index-1))` inside the seqlock — grep the moved code for `.offset` and `wrapping_add`; a bare `rdpmc()` result returned directly is the bug (§1). |
| C5 | no ptrace on the preemption path | the flipped backend reads RCB via `reverie_perf::…::ctr_value_rdpmc` in-guest; no `read()`-to-ptrace-host fallback silently taken (record a slowpath class if the counter can't open). |
| C6 | `guest_clock()` monotone | parity validated on clock EVOLUTION across the run, not a single sample (#1095). |

---

## 6. One-line status for the coordinator

The (1) SHARED-CODE half's fail-closed path (in-guest SIGSYS) is **specified + landed** (`5ca88b6`);
the shared driver is **landed** (#373). The remaining pure-code, no-validate architecture increment is
**A2+A3 (this doc)** — with the corrected A2 home (`reverie-perf` shared crate, NOT a reverie-ptrace
re-export) so the offset-correct rdpmc read is shared in exactly one copy and the in-guest lib never
drags in the host tracer. Measurement (M1 non-Option stats seam / M2 transport) stays behind this per
the owner's "measure AFTER."
