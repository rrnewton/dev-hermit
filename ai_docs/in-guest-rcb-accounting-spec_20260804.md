# In-guest RCB accounting spec (the bracketing dance) for a patching backend

**Task:** `in-guest-rcb-accounting-spec` (P1, design-only). **Author:**
hermit-rdpmc (opus-4.8), 2026-08-04. **Status:** SPEC — no code. Instrumentation
and lifecycle changes need owner discussion BEFORE coding.

**Grounded in** the reverie worktree `worktrees/rdpmc/reverie` (branch
`codex/reverie-perf-rdpmc-read-primitive`, base `origin/main`
`bfea4d5aa7d662cacf21f41ff2df5b60925dff2d`): `reverie-ptrace/src/{timer.rs,perf.rs}`,
`reverie-liteinst/src/{runtime.rs,tool_host.rs}`. Every mechanism carries a
`file:line`.

## 0. What is settled, and what this spec is actually about

**SETTLED — do not re-litigate.** RCB-overflow preemption plus skid-aware
single-stepping reaches a deterministic conditional-branch count in the guest.
Two shipping reference implementations: our own `reverie-ptrace/src/timer.rs`
(fire the PMI early at `target − skid_margin`, then drive
`single_step_with_clock`, timer.rs:585, to the exact count;
`AMD_EPYC_9D85_SKID_MARGIN = 1_000`, timer.rs:68; `AMD_RCB_EVENT = 0x5100d1`,
timer.rs:64) and mozilla rr. Determinism *achievability* was never the open
question.

**THE NARROW OPEN QUESTION.** In a *patching* backend the tool's syscall handler
runs **inside the guest address space, in guest user mode, on the guest's own
core and thread**. Its conditional branches are therefore counted by the guest's
own RCB counter — the exact counter Detcore treats as the thread's virtual
clock. In the ptrace backend this never happens: the tool runs in a *separate
tracer process*, and the tracee's clock counter is opened `exclude_kernel` /
`exclude_guest` / `exclude_hv`, `pinned=1`, per-tid, `cpu=-1` (perf.rs:200-213),
so the tracer's branches are structurally invisible to it. In-guest we lose that
free isolation and must **subtract the tool's branch consumption by hand**. That
manual subtraction is the *bracketing dance*.

This spec says: (§1) where the two reads go, (§2) what the rdpmc measurement
means for the budget, (§3) nested entries and a handler that itself syscalls,
(§4) how it composes with the one-in-guest-Detcore-subscriber decision without
blunting continuous virtual time (#1095), (§5) the pinned=1 / deschedule
hardening the in-guest reader needs, and (§6) the open design gaps that require
owner discussion before any code.

## 1. Where exactly the two reads go

### 1.1 The steady-state trampoline (the common path)

After a syscall site is discovered and patched, the steady-state control flow in
LiteInst is (source map: `reverie-liteinst/CLAUDE.md` "How LiteInst Works" step
5-6):

```
patched syscall site
   └─(installed patch: unconditional jmp/call — does NOT increment the
      retired-*conditional*-branch counter, event 0x5100d1)
      └─> tool_trampoline()                 runtime.rs:1704  [runs in guest ctx]
             └─> process_syscall(event)      runtime.rs:1740  [routing branches]
                    └─> tool_host::dispatch(event)  runtime.rs:1759
                           └─> tool.handle_syscall_event(&mut guest, syscall)
                                                     tool_host.rs:237
             (returns up the stack; runtime restores guest regs; resume guest)
```

**Read #1 (clean baseline)** — the *first* action inside `tool_trampoline()`
(runtime.rs:1704), before the `CURRENT_EVENT` null check and before
`process_syscall`. This is the clean baseline the owner's algorithm requires: the
patch delivered control by an **unconditional** `jmp`/`call`, which does not
increment the conditional-branch counter, so the counter value here equals the
guest's true RCB count at the syscall site. Call it `B`.

**Read #2 (before returning control)** — the *last* action inside
`tool_trampoline()` (runtime.rs:1713), after `process_syscall` returns and before
the trampoline returns and the runtime restores guest registers / resumes the
original site. Call it `E`.

The single bracket therefore spans the **entire** `tool_trampoline` body —
crucially including all of `process_syscall`'s routing (runtime.rs:1740-1768:
`protect_runtime_control`, `protect_coordinator_channel`, mode checks) and the
whole `tool_host::dispatch` chain (subscription check tool_host.rs:211, typed
decode tool_host.rs:232, `drive_syscall(handle_syscall_event)` tool_host.rs:237).
Every one of those conditional branches is tool consumption and every one is
inside `[B, E]`.

### 1.2 The accounting state and the clock read

Per thread, maintain a monotonic accumulator:

```
tool_debt : u64   // total tool-consumed RCBs on this thread, initialized 0
```

On each trampoline exit: `tool_debt += (E − B) − C_read`, where `C_read` is a
small **calibrated constant** for the reads' own conditional-branch self-cost
(the seqlock retry test in the rdpmc loop; see §2). The self-cost is a constant
and deterministic — this is exactly the memory result "RCB self-cost is a small
constant → trivially deductible" — so it can be folded into `C_read` or simply
tolerated as a fixed bias identical in record and replay.

**The virtual clock every Detcore reader must use** becomes:

```
guest_clock() = raw_rcb_now() − tool_debt
```

This *replaces* the meaning of `Timer::read_clock` (timer.rs:343) for the
in-guest backend. Today `read_clock` in the LiteInst host is a hard
`Unsupported` stub (tool_host.rs:632-638; likewise `set_timer` /
`set_timer_precise` at 616-630). Wiring `guest_clock()` here is the additive
change the in-guest backend needs — and it depends on an in-guest RCB read
primitive, which reverie's `perf` module does not currently expose to LiteInst:
`mod perf;` is private to `reverie-ptrace` (reverie-ptrace/src/lib.rs:45), so
`PerfCounter` (incl. the new `ctr_value_rdpmc`) is unreachable from
`reverie-liteinst` as written. Exposing it is a scoping decision for the owner
(§6), not part of this accounting spec.

### 1.3 The discovery trap is a separate, one-time bracket

The steady-state path above is `hooks=N`. The *first* execution of each site is
the `traps=1` discovery path: a `SIGSYS` trap → identify site → install hook →
`sigreturn` → trampoline (CLAUDE.md step 4-5). Per LiteInst safety invariant the
tool is **never** run inside the SIGSYS handler; tool dispatch still funnels
through `tool_trampoline` after `sigreturn`, so §1.1's bracket already covers the
tool portion. But the SIGSYS handler + `install_site_hook` (runtime.rs:1000)
branches run in guest user mode once per site and contaminate the counter by a
**bounded, per-site, deterministic** amount. Because it is deterministic and
identical across record/replay it does not break determinism, but for an accurate
clock it should be bracketed too (read at SIGSYS entry, read before `sigreturn`)
or subtracted as a calibrated per-site constant. Flag: completeness item, not a
correctness blocker.

## 2. What the rdpmc measurement means for the budget

The bracket's *marginal* cost is exactly **two counter reads per handler entry**
(the debt subtraction `E − B` is free arithmetic). So the read primitive is the
whole budget question. Measured on this host (independently re-verified
2026-08-04, release build, devbig014 AMD EPYC 9D85 x86_64, `taskset -c 3`, 3pai,
`cap_user_rdpmc=1`, `pmc_width=48`, LOOP=100000 reads/sample, 25 reps + 5 warmup,
`index!=0` live case; consistent with the standalone C anchor 13.9 ns / 239 ns):

| primitive | per read | 2 reads / handler |
| --- | --- | --- |
| `rdpmc` (in-guest-native, `PerfCounter::ctr_value_rdpmc`) | ~10 ns | **~20 ns** |
| `read()` syscall fallback (reverie's current `index!=0` path, perf.rs) | ~264 ns | **~528 ns** |

**Both anchors, not the ratio alone (≈26.8×).** The ~500 ns swing per handler is
what decides whether the ~31× trap-mechanism win survives accounting. Projecting
onto the landed S1(b) microbench baseline (Mode A in-guest trap **845.7 ns**,
ptrace **26393.7 ns**, **31.2×**):

- `+ rdpmc`  → 845.7 + 20 = 865.7 ns → **~30.5×** (win essentially intact)
- `+ read()` → 845.7 + 528 = 1373.7 ns → **~19.2×** (win roughly halved)

So the design is viable at **~30×** iff the in-guest read is `rdpmc`, and
degrades to **~20×** if it falls to the `read()` syscall. This is *the* number
that gates the in-guest perf case — and it is why the rdpmc primitive
(reverie PR #363) was split out and measured first. `C_read` (§1.2) is the
conditional-branch self-cost of these reads; for `rdpmc` it is a single
instruction plus one seqlock retry-test branch — a tiny constant, trivially
deductible.

## 3. Nested entries, and a handler that itself syscalls

### 3.1 Current LiteInst: nesting cannot occur (by the trusted-syscall bypass)

A handler that itself makes a syscall does so through the **trusted-syscall
gate**: tool/RPC/allocator/injected syscalls go via `raw_syscall6`
(tool_host.rs:228, 610) guarded by `injected_syscall_guard` (tool_host.rs:416,
also 224/574/605). Those execute the raw syscall instruction directly and are
**not** routed back through the patch/trampoline — the LiteInst safety invariant
"syscalls made by LiteInst, RPC, allocation, or the tool must not recursively
enter the tool" (reverie-liteinst/CLAUDE.md). Therefore, in current LiteInst,
`tool_trampoline` never nests: bracket depth is always exactly 1.

Consequence for accounting: the single outer bracket `[B, E]` **already**
captures the user-mode glue branches of any syscalls the handler injects, and
does so **without double counting**, because there is no inner trampoline entry
to also account. The syscall *kernel* work adds nothing to the counter
(`exclude_kernel=1`, perf.rs:200-213) — only the user-mode setup/teardown
branches count, and those are legitimate tool debt.

### 3.2 The general rule (re-entrancy-safe by construction)

Specify the accounting to be depth-gated so it stays correct if a future design
ever does allow a tool-triggered patched site to re-enter:

```
on trampoline entry:
    if depth == 0: B = raw_rcb_now()      // read baseline only at the outermost
    depth += 1
on trampoline exit:
    depth -= 1
    if depth == 0: tool_debt += (raw_rcb_now() − B) − C_read   // commit once
```

Read the baseline only at `depth 0→1` and commit debt only at `depth 1→0`. Inner
entries do nothing to the accounting — they are already inside the outer bracket,
so their branches are counted exactly once. This is the *accounting*
re-entrancy-safety property. It is **orthogonal to** execution-level re-entrancy
safety (the JIT clean-call re-entrancy wall documented for DBI): the depth gate
makes the *numbers* correct; whether the backend may safely re-enter at all is a
separate owner-level question. In LiteInst today the trusted gate keeps depth ≡ 1
and the depth counter is a guard, not a live path.

### 3.3 A PMI that fires *inside* a handler

The timer counter is a **separate** `PerfCounter` from the clock (timer.rs:465
`clock`, timer.rs:468 `timer`) and keeps counting during the handler. If it
overflows and delivers the marker signal (`PERF_EVENT_SIGNAL`, timer.rs:56) while
control is inside `[B, E]`, that overflow is *tool* branches, not a guest
preemption point, and must not be taken as one. Two acceptable policies:

1. **Defer-and-re-arm (preferred).** On handler exit, always re-arm the timer for
   the remaining *guest* budget `target − guest_clock()` (the in-guest analogue
   of ptrace's `finalize_requests`, timer.rs:425). A marker signal that arrived
   mid-bracket is simply swallowed; the exit re-arm reschedules against the
   debt-adjusted clock, so no guest preemption is lost or misplaced.
2. **Mask during the bracket.** Block `Timer::signal_type()` for `[B, E]` and
   unblock at exit. Simpler to reason about; costs one `sigprocmask`-class
   operation per handler unless done with a cheap userspace pending-flag.

Either way the invariant is: **RCB-overflow is the rare fallback; syscall entry
is the fast primary preemption path.** Each handler exit re-arms for the
remaining guest budget, so syscall-dense guests barely exercise the PMI at all —
which is exactly why two cheap `rdpmc` reads per entry, not the PMI, dominate the
accounting cost.

## 4. Composition with the ONE in-guest Detcore subscriber, and #1095

**The subscriber decision changes WHERE DETLOG is emitted, not the time model.**
The "one in-guest Detcore subscriber" question is about consolidating DETLOG
emission into a single in-guest subscriber rather than scattering it. The
accounting in §1-§3 produces exactly one quantity that matters to time:

```
guest_clock() = raw_rcb_now() − tool_debt      // the single source of virtual time
```

The composition rule is a single sentence: **wherever the shared subscriber
lives, it reads `guest_clock()` — the debt-adjusted clock — and never a raw
counter, a re-based origin, or a per-emission recomputation.** Moving emission to
one subscriber must not move, reset, or re-derive time.

**Why this is the #1095 tripwire.** PR #1095 ("Normalize guest clock startup
across backends") reset the clock origin on every exec (`handle_post_exec` sets
origin=None/elapsed=0), so the first post-exec read was always exactly `epoch` on
every backend — *fake* first-sample parity that proved origin alignment, not
equal *evolution* (`GuestClock::observe`, detcore/src/tool_local.rs; retro-review
memory `pr1095-fake-determinism-clock-review-lesson`). The lesson: **continuous
virtual time must EVOLVE monotonically and must never be rebased.** Applied here:

- `tool_debt` is monotonically non-decreasing; `raw_rcb_now()` is monotonically
  non-decreasing; hence `guest_clock()` is monotone and continuous — the property
  #1095 must preserve.
- The accounting must therefore **never** reset `tool_debt` to 0 mid-thread, and
  the one-subscriber refactor must **never** rebase the clock origin (e.g. on
  exec/fork) to make DETLOG "line up." Doing either recreates the #1095
  discontinuity — a blunted continuous virtual time — under a new name.
- Acceptance bar inherited from the #1095 review: prove parity on *evolution*
  (repeated-read / cross-exec traces agree on the 2nd..Nth reads), never on a
  single post-exec sample. Any validation of this accounting must show the
  debt-adjusted clock advancing identically across a record/replay pair over many
  reads, not just matching at an origin.

DETLOG-location consolidation and this clock are orthogonal: the subscriber may
change *where* an entry is written, but the *value* it stamps is `guest_clock()`,
computed once, monotone, never rebased.

## 5. pinned=1 / deschedule hardening the in-guest reader needs

The clock counter is `pinned=1` (perf.rs:210): a PMU deschedule EOFs the read,
and `ctr_value_rdpmc` panics on `running != enabled` (matches the pinned=1 EOF).
That is acceptable for a benchmark on a quiet core but **not** for a production
in-guest reader:

- `rdpmc` is valid only when reading *your own currently-scheduled* counter *on
  the core you are running on*. If the guest thread migrates cores or is
  descheduled between read #1 and read #2 (e.g. a handler that injects a blocking
  syscall like `futex`), the second `rdpmc` can read the wrong core's MSR or hit
  the EOF/`index==0` case.
- The in-guest reader must therefore use the mmap page's `cap_user_rdpmc` +
  `index` + seqlock exactly as `ctr_value_fast_loop` does (perf.rs), and on
  `index==0` / seqlock-odd / deschedule **fall back to the `read()` syscall**
  (which re-reads from the kernel and is correct regardless of scheduling) rather
  than **panic**. This is the one place the current `ctr_value_rdpmc` (built for
  a pinned quiet-core bench) needs a robustness change before it can back a live
  clock. It is a hardening item for the owner, listed in §6.

## 6. Open design gaps — owner discussion required before any code

DESIGN ONLY. The following are explicitly out of scope for this spec and must be
settled with the owner before implementation:

1. **Expose an in-guest RCB read to LiteInst.** `mod perf` is private to
   `reverie-ptrace` (lib.rs:45); `PerfCounter`/`ctr_value_rdpmc` are unreachable
   from `reverie-liteinst`. Decide the additive surface (a small trait/shim vs.
   promoting a minimal reader) that lets the LiteInst host implement
   `read_clock`/`set_timer` (today `Unsupported`, tool_host.rs:616-638) without
   changing the `Tool`/`Guest`/`Backend` contracts.
2. **Deschedule-robust rdpmc.** Add the seqlock/`index==0` fallback-not-panic
   behavior of §5 to any in-guest read path.
3. **Live single-step-to-exact-count fallback (RAREST).** ptrace drives
   `attempt_single_step` (timer.rs:838) → `single_step_with_clock` (timer.rs:585)
   from the external tracer, bounded by `max_single_step_count = skid_margin + 5`.
   In-guest there is no external tracer to deliver SIGTRAP-per-instruction — *who
   single-steps whom?* This is an architecture question for the rare fallback
   (RECORD may ignore skid and record where it landed; REPLAY can use the scx-sim
   breakpoint-at-target-RIP + branch-count technique instead of live stepping),
   NOT a determinism question. Cost it separately.
4. **Discovery-trap bracketing (§1.3).** Decide whether to bracket the SIGSYS
   install path or subtract a calibrated per-site constant.
5. **Multi-thread axis.** This spec covers single-thread accounting cost (axis
   (a) = 0). Park-and-RPC-to-the-global-scheduler sequentialization is
   backend-independent and deferred until a multi-thread in-guest build exists; it
   cannot change the per-handler accounting verdict but is required before any L1+
   determinism claim. LiteInst tool mode is currently one process/thread
   (reverie-liteinst/CLAUDE.md "Supported Boundary").

## 7. Bottom line

The bracketing dance is: **read the RCB as the first act of `tool_trampoline`
(runtime.rs:1704), read it again as the last act before returning to the guest
(runtime.rs:1713), accumulate the difference as `tool_debt`, and define the
guest's deterministic clock as `raw_rcb − tool_debt`.** It rides the existing
per-syscall path, adds two counter reads per handler entry, and — at ~10 ns
`rdpmc` vs ~264 ns `read()` — keeps the ~31× trap-mechanism win at **~30×**
rather than collapsing it to **~20×**. Nesting is precluded by the trusted-syscall
gate (and made re-entrancy-safe by a depth gate regardless); a mid-handler PMI is
absorbed by the exit re-arm; and the whole thing feeds the *single* continuous,
never-rebased virtual clock that the one-in-guest-subscriber decision must read
rather than recompute — the standing #1095 constraint. Determinism is not in
question; **cost and clean composition are, and both are satisfied under `rdpmc`.**
