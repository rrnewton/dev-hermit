# LiteInst bitwise-parity 0→1: instrumentation-syscall provenance design

- **Date:** 2026-08-04
- **Status:** DESIGN / research artifact. No code change. Enumeration-mandate output; compat
  expansion stays paused; no scorecard cell.
- **Grounding SHA:** hermit `c369be3ff8e2c751a313b27979fa8f470dafecf0` (all file:line anchors below
  verified at this commit by direct read).
- **Prereq reading (product constraint):** `hermit/.claude/skills/continuous-virtual-time-is-sacred/SKILL.md`.
  This design is written to respect it; §5 states how.
- **Feeds:** the gated full green-reset; the LiteInst lane's honest bitwise count (currently 0/108).

## 1. Problem and scope

Bitwise parity coverage on LiteInst is **0 of 108** cells (audit:
`experiments/parity-bitwise-definition-audit_20260804/`). The cheapest possible cell —
`system-utils/record-getpid` (guest `hermit/tests/c/getpid.c`, one determinized syscall) — was used
to localize which of three named blockers binds
(`experiments/liteinst-first-bitwise-parity-cell_20260804/`):

- **`strip_lines` default (verify.rs:158) — not the blocker.** Display default; bypassed by
  `--verify-verbose` / raw `--log-file` capture.
- **Harness — not the blocker.** Cross-backend unstripped INFO-log diff reproduces from existing
  primitives.
- **Backend — THE blocker.** LiteInst's in-guest instrumentation issues real, guest-observable
  syscalls that detcore commits to the deterministic timeline, so the DETLOG cannot be byte-identical
  to ptrace's.

**Both comparison endpoints are stable** (follow-up probe, 6 runs, this design's author): under a
fixed guest environment, LiteInst getpid's wall-clock-stripped DETLOG is bitwise self-identical
(3× stdout=/dev/null → md5 `80c8bc48`; 3× stdout=regular-file → md5 `e7ffbdb7`); the 72×
`/proc/self/maps` re-scan is stable in count and order; virtual-time trajectory identical run-to-run.
So this is a well-posed fix against a **stable golden reference (ptrace)** and a **stable subject
(LiteInst's own stream)** — not a chase against a moving target.

**In scope:** make LiteInst's *own* bootstrap and call-site-discovery events invisible to detcore's
determinism accounting, so only the *guest program's* syscalls and branches tick the clock and appear
in the compared stream. **Out of scope:** any change that makes backends agree by degrading time
(see §5); compat expansion; new scorecard cells.

## 2. Root cause, with exact anchors

For getpid, three instrumentation sources perturb the guest's determinism accounting. All three reach
detcore through the **same** `Tool::handle_syscall_event` — detcore is strictly backend-agnostic
(`detcore/src/lib.rs:9-33`), so there is no separate channel and no origin field today.

1. **`.so` load renumbers the guest syscall stream.** LiteInst loads `libreverie_liteinst.so`
   (pulling `libgcc_s.so.1`) as the first `openat`, before `ld.so.cache`. libc opens at guest syscall
   `#10` under ptrace vs `#19` under LiteInst.
2. **72× `/proc/self/maps` re-scan** to place trampolines (0 under ptrace).
3. **Injected `fstat`** per instrumentation fd (36 vs 2 under ptrace).

How each perturbs accounting:

- **The `#K` counter** (`finish syscall #K`) is `stats.syscall_count`
  (`detcore/src/tool_local.rs:1044-1045`, incremented `count_syscall` at `1083-1086`), read back at
  `detcore/src/lib.rs:1489-1502`. It bumps once per *intercepted* syscall — so every extra
  instrumentation syscall renumbers all subsequent finish lines.
- **Per-syscall virtual-time charge:** `add_syscall_with_cost(syscall_cost_ns)` at
  `detcore/src/lib.rs:1497`, cost table `detcore/src/syscall_time.rs:26-158` (unknown → `FAST_NS`, so
  even injected syscalls advance time), accumulator `detcore-model/src/time.rs:546-557`
  (`syscall_nanos`).
- **RCB (retired conditional branch) charge:** `update_logical_time_rcbs`
  (`detcore/src/lib.rs:364-394`) converts the Reverie clock delta since `committed_clock_value` into
  logical time at every handler entry (`pre_handler_hook`, `lib.rs:484-486`); accumulators
  `detcore-model/src/time.rs:570-589`; `NANOS_PER_RCB=10.0`, `NANOS_PER_SYSCALL=10000.0`
  (`time.rs:36,39`). **The instrumentation's own branches (trampoline logic, maps parsing) run inside
  the guest thread and are swept into the next syscall's RCB delta.**
- **DETLOG emission:** inbound `detcore/src/lib.rs:1455-1461` (already gated on
  `guest_past_first_execve()`); finish `detcore/src/lib.rs:2251-2257`; committed-time turn line
  `detcore/src/scheduler.rs:2876-2883` (`self.committed_time`, advanced in `bump_global_time`
  `scheduler.rs:2758-2848`).

## 3. Existing precedent — provenance handled in the backend glue, not detcore

The SaBRe/LiteInst plugin already recognizes and suppresses its runtime's *own* bootstrap `getrandom`
so that draw stays out of detcore's guest-visible random stream — **before** the RPC to detcore:
`detcore-sabre/src/lib.rs:214-231` (`handle_post_load_syscall`, `is_post_load_bootstrap_random`,
`post_load_syscall_pending`) and the libc `getrandom` reentrancy detour at `:249-276`. DBI has an
analogous origin-based route: a copied pre-exec child runs natively with no Detcore tool so its
syscalls bypass `handle_syscall_event` entirely (`detcore-dbi/src/lib.rs:1170-1252`).

**Implication:** the architecture already accepts "the backend knows which syscalls are its own and
filters them at the glue." This design generalizes that ad-hoc, getrandom-specific mechanism into a
principled provenance boundary — it is *not* a new abstraction invented from scratch.

## 4. Proposed mechanism (three sub-problems, increasing risk)

**Design goal (stated precisely):** the committed DETLOG and virtual-time trajectory must be a pure
function of the **guest program's** events, identical across backends *because the guest work is
identical* — never because time was made coarser (§5).

### 4A. Syscall suppression — TRACTABLE (glue filter, SaBRe precedent)
Tag LiteInst's own syscalls (`.so` loads during bootstrap, `/proc/self/maps` reads issued by the
trampoline placer, instrumentation-fd `fstat`s) at the SaBRe glue and suppress them before the RPC to
`handle_syscall_event`, exactly as `handle_post_load_syscall` does for `getrandom`
(`detcore-sabre/src/lib.rs:214-231`). Suppressed syscalls never reach detcore, so they never bump
`#K`, never charge `syscall_nanos`, never emit a DETLOG line. This directly fixes perturbations (1)
and (3) and the DETLOG-line divergence, and it needs **no** change to detcore's time code — the clean
path. Distinguishing instrumentation maps-reads from a guest's own `/proc/self/maps` read requires an
origin signal available at the glue (the trampoline placer is a known code path in the runtime).

### 4B. RCB attribution — HARD, the real risk surface (§5 applies most here)
The instrumentation's branches execute inside the guest thread and are captured in bulk by
`read_clock` at the next handler entry (`lib.rs:373`). Suppressing the *syscall* does not remove the
*branches the instrumentation ran*. Options, none free:
- **Bracket-and-subtract:** read the RCB counter immediately before and after each instrumentation
  region (the runtime already has entry/exit points around trampoline logic) and subtract that delta
  from `committed_clock_value` so the guest thread is charged only for *its own* branches. This
  preserves fine-grained continuity of guest time (it removes only non-guest branches) — the
  legitimate shape. Cost: an accounting hook per instrumentation region; correctness depends on
  precise bracketing (cf. `ai_docs/s1-inguest-bracketing-cost-measurement-design_20260803.md` and
  memory `inguest-rcb-accounting-spec-bracketing-dance`).
- **rdpmc-based self-metering** inside the runtime (reverie rdpmc primitive landed, PR #363; memory
  `reverie-rdpmc-read-primitive-implemented-pr363`) to measure its own RCB consumption for exact
  subtraction.
- **REJECTED:** coarsening/rounding/freezing time, or normalizing first-sample to a round origin, to
  make the trajectories match. That is the PR #1095 anti-pattern (§5).

### 4C. Guest-syscall renumbering consistency
Once 4A removes instrumentation syscalls, `#K` for the guest's own syscalls must line up with ptrace's
(libc opens at `#10` on both). This should follow automatically from 4A (the counter only counts what
detcore sees), but must be validated on the full stream, not just the first divergence.

## 5. Continuous-virtual-time constraint (non-negotiable)

Per `continuous-virtual-time-is-sacred`: virtual time must advance continuously and at fine
granularity as a pure function of guest progress. This design achieves parity by **removing non-guest
events from the accounting**, so the guest's own syscalls/branches still tick the clock at full
resolution — the two backends then agree *because the guest work is identical*. Explicitly forbidden
here (any reviewer must reject): rounding/quantizing timestamps; freezing/stalling the clock;
per-exec/per-process resets; first-read-epoch on a round origin (PR #1095's fake parity); or any
"make time coarser" mechanism. 4B is the sub-problem most at risk of sliding into this and needs the
strongest review.

## 6. Validation plan

- **Precondition (already met):** within-backend bitwise self-parity under fixed env (§1). Re-assert
  after any change.
- **Harness discipline (near-miss lesson):** hold the guest I/O environment identical across compared
  runs/backends. stdout=/dev/null (char device) makes glibc issue an extra `ioctl(1,TCGETS)=ENOTTY`
  tty-probe that a regular-file stdout skips — a real guest-syscall-stream difference that will read
  as false divergence if the two sides differ. Memory:
  `liteinst-first-bitwise-cell-backend-blocked-getpid`.
- **Compare the full trajectory, not the first sample** (skill §"Test the continuous evolution"):
  cross-backend wall-clock-stripped DETLOG diff on getpid must be empty; virtual-time COMMIT sequence
  must match turn-by-turn; then widen to the 17 honest-bracketed value-emitting cells before any
  scorecard claim.
- **Ratchet:** a cell counts as bitwise only when the full INFO log matches with matching timestamps.

## 7. Human-review & risk classification

This is a **core Reverie syscall-interception / determinization change** → `post-facto-human-review`
trigger 2 (and arguably 3, a determinization refinement). Per Reverie API Policy it must be discussed
with the owner before implementation and cannot be smuggled in as cleanup. 4A (glue filter) is
low-risk and additive; 4B (RCB attribution) is the load-bearing, review-heavy part. Open questions:
(a) exact origin signal for instrumentation maps-reads vs a guest's own; (b) precise RCB bracketing
boundaries in the LiteInst runtime; (c) whether any instrumentation event is *semantically* guest-
visible and must NOT be suppressed.

## 8. References

- Code anchors: `detcore/src/lib.rs:{9-33,364-394,484-486,1446-1461,1489-1502,2251-2257}`,
  `detcore/src/tool_local.rs:{1044-1045,1083-1086}`, `detcore/src/syscall_time.rs:26-158`,
  `detcore-model/src/time.rs:{36,39,546-557,570-589,604-621,741-770}`,
  `detcore/src/scheduler.rs:{2758-2848,2876-2883}`, `detcore-sabre/src/lib.rs:{190-303,214-231,249-276}`,
  `detcore-dbi/src/lib.rs:{1170-1252,1281-1408}`.
- Experiments: `experiments/parity-bitwise-definition-audit_20260804/`,
  `experiments/liteinst-first-bitwise-parity-cell_20260804/`.
- Memories: `liteinst-first-bitwise-cell-backend-blocked-getpid`,
  `parity-scorecard-is-stdout-sha-not-bitwise`, `verify-verdict-bound-to-stripped-compare-not-bitwise`,
  `inguest-rcb-accounting-spec-bracketing-dance`, `reverie-rdpmc-read-primitive-implemented-pr363`.
- Product constraint: `hermit/.claude/skills/continuous-virtual-time-is-sacred/SKILL.md`; PR #1095
  (memory `pr1095-fake-determinism-clock-review-lesson`).
