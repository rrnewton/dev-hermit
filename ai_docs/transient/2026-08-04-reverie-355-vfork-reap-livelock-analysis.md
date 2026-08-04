# Reverie #355 vfork/reap livelock — source analysis + narrow-fix verification

Date: 2026-08-04
Author: hermit-250 delegate (claude-opus-4-8)
Task: `vfork-child-stuck-in-ptrace-stop-parent-spins-on-futex-forever`
Slot: `worktrees/250-delegate/{reverie,hermit}` (analysis + draft/verify; not a canonical `slotNN`)
Reverie branch (local import for verification): `fix-ptrace-vfork-reap-resume` @ `7f577407c15510de786124c142e52171b632b00b`
Base reverie pin (hermit): `d973a85b328610c14c41c39fa57495b9f77c3c90`
Landing artifact: **reverie PR #355** (OPEN, MERGEABLE) `fix/notifier-consume-dead-status-esrch-spin`
@ `faf8a342c8ca2f7e43197e364949e76e227e8017` — "safeptrace: consume dead ptrace status on decode error
to end ESRCH hot spin". `git diff 7f57740 faf8a342 -- safeptrace/src/notifier.rs` is **EMPTY**: my
verified commit's notifier.rs is byte-identical to PR #355's, so the concurrency and rollback evidence
below bind directly to #355. No duplicate PR was opened; #355 is the artifact to land.

This is a Reverie-only ptrace-backend change. Assurance floor is **L0** (safeptrace suite);
the concurrency evidence below is a hermit-level in-process detcore repro, not an L1+ determinism claim.

---

## TL;DR

- **Q1 — sufficiency:** #355 changes ONLY the async notifier decode path to consume-on-error.
  Empirically **SUFFICIENT** at 16-wide: baseline hangs **~20% (23/112)**; with #355, **0/336** hangs.
- **Q2 — rollback regression:** **REFUTED.** The current #355 diff leaves the synchronous
  `SyncWaitOwner::decode_status_return` behaviorally UNCHANGED (adds a comment only); the `?`-rollback
  and wake-cleanup contract is intact. No narrower fix is needed — #355 is *already* the narrow,
  async-only fix. The `decode_error_rolls_back_return_transaction_and_wakes_cleanup` test still passes
  with #355 applied (see Verification).
- **Q3 — hermit 0321a015:** **Related by topic only.** It fixes a detcore-scheduler / KVM-backend vfork
  barrier, a different layer than the ptrace-notifier hot spin. It is already in the base hermit history
  and the ptrace-side hang persists without #355.
- **POSSIBLE vs IMPOSSIBLE:** **POSSIBLE and ACHIEVED.** A vfork/reap-only fix that does not touch the
  SyncWaitOwner rollback contract exists and is exactly #355.

---

## The bug (ESTABLISHED from source + repro)

The ptrace notifier latches a raw wait status, then decodes it. Decoding a ptrace-event stop reads
`PTRACE_GETEVENTMSG`. On the vfork + parent-`kill(child, SIGKILL)` teardown path the tracee can die
between the `waitid` that latched the event-stop and the `PTRACE_GETEVENTMSG` read → `ESRCH` →
`Error::Died` ("Death under ptrace", `man 2 ptrace`).

On the **async** `Event` path, the pre-#355 code was `let decoded = decode(reservation.status)?;`. The
`?` drops the `StatusReservation` uncommitted, so the undecodable dead status stays at the front of the
`pending` VecDeque and is re-presented identically on every re-poll — an **unbounded ESRCH hot spin**
pinning a CPU. Unlike the sync path, the async notifier path has **no cleanup claimant to hand off to**,
so rollback there produces livelock rather than progress. The guest parent's `wait4`/vfork wait then
blocks forever behind the wedged supervisor.

Observed live signature at 16-wide (baseline, before build A): a zombie/unreaped `cat` child plus a
supervisor thread at ~99.8% CPU in a futex/re-poll spin (dead-status/ESRCH sub-signature), and,
separately, an alive ptrace-stopped `cat` (passive block). The 0/336 build-A result below shows #355
resolves the observed hang in this repro.

---

## Q1 — What #355 changes, and is it sufficient?

`git show 7f57740` (import of #355), `safeptrace/src/notifier.rs`, +40/-1:

1. **`Event::decode_status_return` (async), notifier.rs:~1129** — the load-bearing change. Replaces
   `let decoded = decode(reservation.status)?;` with a `match` that, on `Err`, **commits** the
   reservation (pops the dead status off `pending`) AND commits the transaction
   (`WAIT_OWNER_NOTIFIER`), then `return Err(error)`. The dead status is consumed exactly as a
   successful decode would consume it, so the caller's next wait advances to the tracee's real terminal
   status. Rationale in-code: "No decode error here is retryable … consuming on any error is both safe
   and necessary for liveness."

2. **`SyncWaitOwner::decode_status_return` (sync), notifier.rs:~559** — **comment only.** The code
   `let decoded = decode(reservation.status)?; reservation.commit(); transaction.commit(WAIT_OWNER_NONE);`
   is unchanged. The added comment documents WHY the sync path must keep rolling back (it has a cleanup
   claimant to hand off to) and warns "do not replace it with a blanket commit."

**Sufficiency verdict: SUFFICIENT (empirically, at the tested width).** See Verification — baseline
~20% hang vs 0/336 with #355 at identical 16-wide harness/binary construction. Causal reason: the fix
removes the exact re-poll-of-dead-status mechanism that produced the ESRCH hot spin; consuming the dead
status lets the subsequent wait reach the real terminal status.

UNVERIFIED / bounds: sufficiency is measured on the in-process detcore `vfork::` tests at 16-way process
concurrency on this host, not the full validate suite nor a real multi-hundred-process hermit workload.
"0/336" bounds the residual hang rate low but cannot prove zero.

---

## Q2 — Does #355 regress the SyncWaitOwner rollback contract?

**No — REFUTED by the diff.** The suspicion was that #355 might make the sync path consume-on-error and
thereby break the rollback-and-wake-cleanup invariant. The current #355 diff does not touch the sync
path's behavior at all; it only edits the async path and adds an explanatory comment on the sync path.

The contract (sync path): on decode error the `?` drops the reservation uncommitted (status stays at
front of `pending`) and drops the transaction uncommitted (Drop rolls `WAIT_OWNER_SYNC_RETURNING → SYNC`
and notifies); `SyncWaitOwner::Drop` then transitions `SYNC → NONE` and notifies, handing the tracee to
a waiting cleanup claimant that re-waits synchronously — forward progress WITHOUT a hot spin. This is
asserted by `decode_error_rolls_back_return_transaction_and_wakes_cleanup` (notifier.rs:5139).

Because the two paths differ structurally (async has no cleanup claimant; sync does), the correct fix is
asymmetric: consume on async, roll back on sync. #355 already implements exactly that asymmetry. **There
is no narrower fix to propose — #355 IS the vfork/reap-only fix and it does not touch the rollback
contract.**

---

## Q3 — Is hermit 0321a015 related?

`0321a015` "Fix KVM vfork barrier teardown for deferred child registration (#1152)" touches
detcore-model/src/config.rs (`backend_defers_vfork_child_registration`), detcore/src/resources.rs
(`ResourceID::VforkFailed`), detcore/src/scheduler.rs (`step2a_wait_for_vfork_barrier`). That is the
detcore **scheduler / KVM backend** vfork barrier — a different layer than the reverie ptrace notifier
hot spin. It is already present in the base hermit history used for the baseline repro, and the
ptrace-side hang reproduces regardless. **Verdict: related by topic (vfork teardown), different bug in a
different backend/layer; not a substitute for #355.**

---

## Verification

### Repro harness
`scratch/vfork-repro.sh BIN ROUNDS WIDTH TIMEOUT` — runs WIDTH parallel processes, each
`timeout $TMO $BIN 'vfork::' --test-threads=1`, tallies ok / hang(rc 124|137|143) / other per round.
Each process runs the 2 in-process detcore vfork tests
(`vfork::clone_vfork_parent_waits_for_child_exit`, `vfork::vfork_parent_resumes_after_child_exec`).

### Confound ruled out (fake-ok check)
The Build-A binary genuinely runs the vfork tests — not 0-match-exit-0:
`tests_misc-a3b003b9 'vfork::' --list` → 2 tests; a single timed run →
`running 2 tests … test result: ok. 2 passed; 0 failed … finished in 0.03s` with real
`DETLOG SCHEDRAND` scheduler output. The ~1s wall for 240 runs is legitimate (16 cores × ~74ms/process).

### Concurrency before/after (16-wide, same host, same harness)

| Build | Reverie | Binary | Rounds×Width | Hangs | Rate |
|---|---|---|---|---|---|
| Baseline (no fix) | d973a85b | `hermit/target/.../tests_misc-0838faa2` | 6×16 + 1×16 | 19 + 4 = **23/112** | **~20%** |
| Build A (#355) | d973a85b + 7f57740 | `worktrees/250-delegate/hermit/.../tests_misc-a3b003b9` | 6×16 + 15×16 | **0/336** | **0%** |

Baseline per-round hangs: 0,4,5 (run 1) and 0,4,5,4,6,0 (run 2) — clearly non-zero, ~20%.
Build A: 21 consecutive rounds of 16, all ok=16 hang=0.

Build A was produced via a LOCAL-ONLY `[patch."https://github.com/rrnewton/reverie.git"]` in
`worktrees/250-delegate/hermit/Cargo.toml` mapping the reverie crates to the slot checkout at
branch `fix-ptrace-vfork-reap-resume` (7f57740). That patch is build-only and MUST NOT be committed.

### SyncWaitOwner rollback test (with #355 applied)
`cargo test -p safeptrace --features notifier decode_error_rolls_back_return_transaction_and_wakes_cleanup`
in the slot reverie @ 7f57740. NOTE: the notifier module is `#[cfg(feature = "notifier")]`-gated
(safeptrace/src/lib.rs:17) — a bare `cargo test -p safeptrace` compiles 0 notifier tests and silently
"passes"; the `--features notifier` flag is REQUIRED.

RESULT: **PASS.** `Running unittests src/lib.rs (safeptrace-dc6aaf263afdd46c) … running 1 test …
test notifier::test::decode_error_rolls_back_return_transaction_and_wakes_cleanup ... ok …
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 92 filtered out`. The sync rollback +
wake-cleanup contract holds with #355 applied.

---

## POSSIBLE vs IMPOSSIBLE verdict

**POSSIBLE and already achieved.** The narrow, vfork/reap-only fix that leaves the SyncWaitOwner
rollback contract intact is exactly #355: consume-on-error on the async `Event` path only, sync path
unchanged. No additional "resume-drive" build (build B) is required — the async consume-on-error alone
took the 16-wide hang from ~20% to 0/336, and it neither touches nor regresses the sync rollback path.

## Confidence + UNVERIFIED gaps

- HIGH: the diff scope (async-only, sync comment-only) and the rollback-contract non-regression (read
  directly from `git show 7f57740`).
- HIGH: baseline hangs at 16-wide (~20%, 23/112, two independent runs).
- HIGH: #355 eliminates the hang in this repro (0/336, confound ruled out).
- MEDIUM: "sufficient" generalizes beyond the in-process detcore vfork tests / this host / 16-way — not
  measured against full validate or a large real hermit workload; 0/336 bounds but cannot prove zero.
- Not attempted: a standalone reproduction independent of the detcore test harness.

---

## FINAL VERIFICATION AT THE LANDED PIN (assiduous-debugger, 2026-08-04)

Re-verified the whole thing at the **landed** state, binding every result to my own runs
(not a handed log). Pins: hermit main `8f656b4d`, reverie `79517704` (== hermit `Cargo.lock` rev).

### Provenance verified by the running thing, not the config
The reused `tests_misc-61b9396040d7da30` binary sits in a slot whose committed hermit `Cargo.lock`
reads reverie `d973a85b` (the OLD unfixed rev) with **no `[patch]`** and a clean tree — so the config
*looks* like it built against the unfixed reverie. It did not. The binary embeds
`reverie-2fc770f7a9c80803/7951770` and the build log shows `Compiling safeptrace … rev=79517704…#79517704`.
The build-time `[patch]` was applied, built, then reverted (leaving the child clean). **The artifact is
genuinely built against the landed fix.** (Had I trusted the lockfile, I would have wrongly concluded the
16-wide result was against unfixed reverie.)

### Gate 1 — 16-wide livelock repro (end-to-end), with a load-robust discriminator
A wall-only harness at this box's load (up to ~383/316 cores) cannot distinguish the livelock (burned
core: CPU≈wall) from mere contention (slow: CPU≪wall). I wrote `harness16-cpuwall.sh`, which for any
over-budget run reads `/proc/PID/stat` utime+stime and classifies **LIVELOCK (CPU≥70% of wall)** vs
**CONTENTION**. Result on the `79517704` binary:

    livelock=0  contention=0  over 640 runs (40 rounds x 16-wide)  at load 123–168  →  NO-LIVELOCK

Nothing even exceeded the 30 s budget. Combined with the predecessor's 320 and hermit-250's 336,
~1,300 fixed-side 16-wide runs with **zero** livelock. Harness-is-not-inert: same harness family showed
**23/112 (~20%) hangs on baseline `d973a85b`** (hermit-250) — it demonstrably detects the livelock.

### Gate 2 — rollback contract NOT regressed (the "tension"), powered
Ran the 4 contract/guard tests directly on the pin-`79517704` `safeptrace` test binary (93 tests
compiled with `--features notifier`; NOT a 0-test silent build), powered ×10:

    decode_error_rolls_back_return_transaction_and_wakes_cleanup      (rollback contract — the tension)
    new_child_decode_return_precedes_cleanup_for_fork_vfork_and_clone (Died-consume — the fix, positive)
    notifier_clone_parent_decode_error_preserves_fifo_front           (async non-Died rollback, negative)
    synchronous_clone_parent_decode_error_preserves_fifo_front        (sync rollback, negative)
    → 10/10 rounds, all 4 pass every round (0 failures)

### Why the "tension" is genuinely dissolved (not merely untriggered)
The originally-measured PR tip `faf8a342` consumed on **all** decode errors — that is the version that
broke the rollback contract. What **landed** (`79517704`) is refined: it consumes **only** on
`Err(error @ Error::Died(_))` and rolls back every other error (`Err(error) => return Err(error)`). So
the fix (kill the ESRCH/Died hot spin) and the contract (roll back non-death errors) no longer conflict —
the narrow-fix the task asked for is exactly what merged. Sync path is byte-for-byte unchanged.

### Verdict
Both non-negotiable gates GREEN at the landed pin on my own runs, provenance-checked. The narrow
vfork/reap fix that preserves the SyncWaitOwner rollback contract EXISTS, is LANDED as reverie #355 (as
merged, `79517704`), and is pinned in hermit main `8f656b4d`. detcore_misc's 16-wide livelock is
resolved. Per owner directive: keep the task open (do not close); tag `implemented`.

Logs: `scratch/assiduous-vfork-logs/{detcore_misc-16wide-cpuwall.log,reverie-unit-direct-79517704.log,harness16-cpuwall.sh}`.
