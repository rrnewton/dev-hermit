# Adversarial review plan — demo5 scheduler fairness/aging PR

Date: 2026-08-01. Reviewer: adversarial-reviewer, opus-4.8.
Task: `demo5-fix-scheduler-fairness-impl` (owner hermit-226). PR: PENDING.
Design under implementation: `ai_docs/scheduler-time-model-fairness-aging-design_20260801.md`
(bounded service-lead eligibility overlay, phase-1 turn-based, research-only flag).

Mandate (owner + user): testify whether the fairness/aging change
(a) actually makes demo5 pass ~1 min RELIABLY (multi-run), and
(b) is IN LINE WITH DETCORE determinism — fairness must be a faithful
deterministic fn of guest progress (#140), NOT host-load/wall-dependent, and
must add NO nondeterminism. Try to BREAK it.

## (b) Determinism-principle attack surface — read the DIFF against these

D1. **Fairness currency must not be host-derived.** Charging must use a
    deterministic committed unit (base_cost=1 per committed turn, or a capped
    local DetTime delta). REJECT if S[i] accumulates from wall clock, real
    time, host poll-retry counts, `GlobalTime`/`committed_time` delta
    (includes scheduler extra_time + host-sensitive poll retries — design §
    "fairness currency must not be an alias for GlobalTime"), or thread arrival
    order that is itself host-timing-dependent.
D2. **No new host clock / no GlobalTime mutation.** Grep the diff for any read
    of SystemTime/Instant/rdtsc/clock in the fairness path. IP4 must be "no
    change". `GlobalTime` must not be frozen/rounded/reset/used as selection key.
D3. **Tentative-pop transactionality.** Charge ONLY on committed turns
    (step6_reenquue). A tentative pop that is undone must NOT charge; a commit
    charges exactly once. (Detcore runqueue is transactional — runqueue.rs
    408-558.) Double-charge or charge-on-undo => nondeterminism / drift.
D4. **Deterministic tie-break.** Within the eligible band selection must remain
    existing (priority, fifo_turn). No argmin(S) (that livelocked —
    min-vtime-scheduler-study). No arbitrary/hashmap-iteration-order tiebreak.
D5. **Integer-only, overflow-safe.** State transitions integer arithmetic;
    saturating/checked wide ints; renormalization (subtract common min) must
    preserve ordering+lead and must NEVER renormalize guest time.
D6. **Wake credit bounded + deterministic.** S[i]=max(S[i], F - WAKE_CREDIT),
    WAKE_CREDIT <= one base slice. A short sleep cannot erase over-service debt;
    long sleeper cannot bank unbounded credit. Empty→nonempty keeps a monotonic
    remembered floor (no reset-by-emptying-queue).
D7. **Poller-agnostic.** The LIVENESS/correctness path must not inspect
    ResourceID, syscall number, readiness, progress, or poll_attempt. (The
    existing exponential poller backoff heuristic may remain as a perf opt but
    must not be the correctness argument.)
D8. **No invented wakeups.** A blocked task stays blocked until a modeled
    wake/timeout/signal/recorded-external. Fairness must not synthesize a wake.
D9. **Default-off.** First patch changes NO production default; overlay behind a
    research-only scheduler flag. If it flips a default, that is a core DetCore
    scheduling change landing silently — REJECT.
D10. **L2 regression.** Overlay OFF must be byte-identical to current main on
    the e2e verify corpus + backend-parity matrix (no accidental behavior change
    when disabled). Overlay ON must itself be L2 (`--strict --verify`
    bitwise-identical across two runs) on a representative multithreaded set.

## (a) demo5 reliability — the empirical gate

VALID enforcer only: `demos/05-qemu-boot.py` OUT-OF-CONTAINER. Do NOT use the
in-container `qemu_controller.py --timeout` (virtualized clock → tripped by
vtime-skew before qmp.sock, gives false wedge — see demo5-pmu-skid memory).

Watch for BOTH known failure modes (pmu-skid memory):
  - ~1/5 real PMU-skid panic-then-hang (reverie-ptrace timer.rs:809), needs
    SIGKILL to pgid; sweep harness must reap pgid.
  - vtime-skew poller-livelock (the H8 wedge the fix targets).

Runs: N>=8 boots with the overlay ON at the PR's chosen B. Record per run:
wall seconds, pass/fail, exit reason. RELIABLE ~1min bar => strong majority
boot < ~90s with no wedge; a single-run demo is NOT evidence.

Load-independence (my stress guardrail): repeat a subset under host-load swing;
schedule fingerprint / verify result must be invariant. Any load-dependent
pass/fail => violates #140.

## The DESIGNER'S OWN caveat — the sharpest lever

Design § "Demo5: what this can and cannot claim": terminal broken demo5 has
vCPU **dtid 7 blocked OUTSIDE the run queue** (7th untimed futex wait @ turn
171,416 never woken) while {3,5,11,13} already rotate fairly. Runnable fairness
**cannot select dtid 7 or synthesize the missing condvar wake.** The overlay can
only fix demo5 by changing the EARLIER interleaving so the lost wait never
happens — a HYPOTHESIS, not a theorem.

=> A green boot is NOT enough. Per design's own rejection gate: demand TRACE
evidence that the vCPU wake/readmission actually occurs (or the bad wait is
avoided) and guest RCB progress resumes. Use dtid_activity.rs
(experiments/demo5-rootcause-20260731/log-science/) STARVED-TAIL vs EXITED vs
BUSY-POLLER witness. "Balanced turn counts among {3,5,11,13}" = FAILURE, not
success (design says so explicitly). If the PR shows a green boot with dtid 7
still starved-tail, the boot succeeded for a DIFFERENT reason (host timing) and
the fix is unproven / possibly nondeterministic.

## Rejection conditions (from design § Rejection conditions — verify each)
- needs is_polling_turn / syscall/resource classifier for liveness;
- GlobalTime frozen/rounded/reset/used as primary selection key;
- make -j8 returns to two-poller/zero-producer pattern;
- exact replay choices become ineligible without explicit bypass;
- demo5 boots WITHOUT an explainable vCPU wake/readmission transition;
- fairness guarantee disappears under chaos priority differences.

## Corroborating anti-deja-vu gate
Re-run recovered `make -j8 --strict --verify` (the case that killed min-vtime).
Must complete (not 300s timeout) AND show bounded producer wait — not the
107,874-vs-107,776 two-monopolist / ~105-producer-turn signature.

## Verdict template (post on PR + task)
(a) reliability: PASS/FAIL with N-run wall-time table + enforcer used.
(b) determinism: per-item D1..D10 verdict, bound to PR head SHA.
Terminal-state: does trace show real dtid-7 wake/readmission or just balanced
{3,5,11,13}? Overall: ALIGNED / NOT-ALIGNED + concrete break if found.
