# HANDOFF — land reverie #355, bump hermit pin, close detcore-misc-timeout-hang-on-main

Author: impl agent, opus-4.8 (2026-08-03). Governing task: `detcore-misc-timeout-hang-on-main` (P0, stays `in_progress`).
Successor: fresh coord picks up landing #355 the moment its checks go green.

## 1. Exact state of reverie PR #355
- Repo/branch: `rrnewton/reverie`, branch `fix/notifier-consume-dead-status-esrch-spin`.
- Head SHA: `820b2b64e05952c9836224d552e6fdce9582b516` (confirmed).
- Fix: `safeptrace/src/notifier.rs` `decode_status_return` (both sync `SyncWaitOwner` ~L541 and
  async `Event` ~L1129) now consume the reservation + `commit()` on decode error BEFORE returning it,
  so a getevent-ESRCH dead status is popped from `pending` instead of re-presented → kills the
  unbounded PTRACE_GETEVENTMSG ESRCH hot spin ("Face A"). `safeptrace/src/lib.rs` `Zombie::reap`
  is at its EXACT original (no drive-to-exit). Reverie-only ⇒ L0.
- CI at head 820b2b64 as of handoff: `state OPEN`, `mergeState BLOCKED`, purely on GitHub Actions
  backlog (mass-parallel drain saturating Actions). `Regular tests (GitHub-hosted)` = QUEUED/pending,
  `Host-dependent tests (self-hosted)` = IN_PROGRESS/pending, `merge-gate` = QUEUED. NO failures.
- PR body honesty: states #355 fixes the getevent-ESRCH hot spin and notes what remains (Face B);
  must NOT read as "fixes the hang" outright.

### Land it when — and ONLY when — both authoritative checks are GREEN at head 820b2b64:
    with-proxy gh pr checks 355 -R rrnewton/reverie      # both green at 820b2b64
    with-proxy gh pr merge 355 -R rrnewton/reverie --rebase
- NEVER `--admin`. Both `Regular tests (GitHub-hosted)` AND `Host-dependent tests (self-hosted)` are
  authoritative for reverie; QUEUED/stale/cancelled ≠ green.

## 2. Acceptance test recipe — VERBATIM, do not weaken (replaces any single green run)
The flake is 16–23% under load; a single green run proves NOTHING. Acceptance = the calibrated
matched-load probe, which co-schedules `head` (no-#355) as a VALIDITY CALIBRATOR: a wave counts only
if `head` is FLAKY in it (rejects under-powered waves that can't reproduce the race).

    cd /home/newton/work/dev-hermit/experiments/multisect_detcore_misc_20260803
    ./matched.sh <conc> <timeout_s> <waves> <label:binpath>...
    # e.g. after pin bump, build the pinned-main tests_misc into ignored/bins/pinned, then:
    ./matched.sh 32 20 24 head:ignored/bins/head pinned:ignored/bins/pinned

- PASS bar for the pinned-main binary: **≤ ~1/768 hangs across head-FLAKY (valid) waves** — i.e. the
  known-good reverie `22791b2f` noise floor. Reference numbers already recorded (24 valid waves,
  768 samples/label, load~330): head 221/768 (28.8%) FLAKY; known-good `5e190f7d` 5/768 (0.65%);
  #355-applied 4/768 (0.52%). See `ACCEPTANCE-pr355.md`.
- Prebuilt calibration bins in `ignored/bins/`: `head` (reverie d973a85b, known-bad),
  `5e190f7d` (reverie 22791b2f, known-good), `9c964fce` (flip fb2cf7e0), `pr355` (#355-applied).
- FLAKY IS RED: anything other than 0/100% is a flake; report the RATIO, never a verdict.

## 3. Pin-bump plan (after #355 merges to reverie main)
Bump hermit's reverie pin from `d973a85b` to a reverie-main SHA that INCLUDES `820b2b64`.
Follow memory `reverie-pin-bump-recipe-gotchas`. Gotchas that apply:
- `cargo update -p reverie-core` must be run **twice** (transitive re-resolve).
- 4 LiteInst cache keys LAG the pin → must be sed-bumped in lockstep.
- Run `check-reverie-pin.rs` after.
- This is a single-variable gitlink advance → follow `ci-hub/history/SUBMODULE-BUMPS.md` A/B protocol;
  determinism-related, so ONE green run is insufficient — use the calibrated matched.sh probe above
  as the powered acceptance gate, then append result to ci-hub history.
Then re-run matched.sh on the pinned-main tests_misc; require ≤~1/768 at head-FLAKY waves.

## 4. Face B — so the new ticket (`detcore-wait4-nondelivery-sigkilled-child`, P2, filed by owner) isn't cold
- Distinct from Face A (#355 kills Face A). Face B = a RARE **detcore inject-`wait4` poll spin**:
  detcore repeatedly injects `wait4` (orig_rax=0x3d) into the guest via SETREGSET+SINGLESTEP at
  trampoline 0x71000000; the guest vfork parent's `wait4` for its SIGKILLed child never completes
  because the child's exit status is never delivered to detcore's per-parent wait bookkeeping.
- PRE-EXISTING: present in known-good reverie `22791b2f` too; occurs at/below the 0.65% floor.
  NOT the fb2cf7e0 regression; NON-BLOCKING for main-green. Do not conflate with #355.
- Live-confirmed: on the (dropped) drive variant one supervisor polled 214s.
- The reap `drive_to_exit` follow-up was FALSIFIED against Face B (6/1008 vs #355-alone 6/648, no
  improvement) and DROPPED. Branch `fix/reap-drive-stopped-tracee-to-exit` @6a6c42d is LOCAL-ONLY,
  never pushed, no PR. Do NOT resurrect it; no dual adversarial review needed.

## 5. Uncommitted / mid-edit state
- Both worktrees CLEAN. reverie detached at `820b2b6`; hermit at pin `d973a85b`; temp
  `[patch."https://github.com/rrnewton/reverie.git"]` in hermit Cargo.toml/Cargo.lock reverted.
- Durable artifacts committed alongside this note: `ACCEPTANCE-pr355.md`, `matched.sh`, raw waves.
- Memory `detcore-misc-vfork-flaky-timeout-under-load` updated with calibrated acceptance + Face B.
- `/tmp/followup-pr-body.md` is OBSOLETE (drive-to-exit dropped) — ignore.

## 6. Task closure (coordinator only, after landing)
`detcore-misc-timeout-hang-on-main` stays `in_progress` until: (a) #355 merged to reverie main,
(b) hermit reverie pin bumped past 820b2b64 and landed, (c) matched.sh acceptance passes on pinned
main. Record landed SHAs, then close. Do NOT tag `implemented` prematurely (see
memory `tg-implemented-tag-landmine`).

---

## ADDENDUM 17:58:25Z (impl agent, opus-4.8) — Host-dependent CI HANG + final acceptance

### #355 CI is BLOCKED by a HANG in reverie "Host-dependent workspace tests", not a product failure
- PR #355 head `820b2b64`, state OPEN/BLOCKED, mergeable=MERGEABLE, review not required.
- `merge-gate` check FAILED **prematurely** (step "Require successful CI or local validation" ran
  at 14:50 before CI green; no locally-validated stamp on reverie). It REFIRES on CI completion
  (a fresh Merge Gate 30837576115 auto-queued). NOT a real failure; do not rebase to clear it.
- **HANG:** `Host-dependent tests (self-hosted)` step 6 "Host-dependent workspace tests".
  Baseline (last 5 reverie main runs): **~2 min** (1:56/1:58/2:05/2:00/1:59).
  Original run 30824642390 hung **2h44m** in step 6 -> I cancelled+reran (fresh runners).
  **Fresh rerun hung AGAIN in the identical step (17+ min and climbing, >8x baseline).**
- VERDICT: fresh runner + identical step-6 hang + monotonic climb = RECURRING hang, NOT a
  one-off wedge. Did NOT re-run a third time (pre-registered criterion).

### reverie CI is a SINGLE-RUNNER SPOF
- reverie has exactly ONE self-hosted runner: `reverie-ci-newton` (labels self-hosted,Linux,X64,
  reverie,**pmu**). NOT on this box (only hermit-gate-newton runs here; ci-runner/instances/reverie
  is an empty scaffold). Any one hung reverie job blocks ALL Host-dependent CI. Fix = add 2nd runner.

### Two theories for the hang (ESCALATE to reverie author; do not re-run):
- (a) PMU-tests-under-host-load: step is PMU-sensitive; single runner on a possibly fleet-loaded
  host -> pathological slowdown/hang. Favours infra.
- (b) #355 interaction cannot be excluded: reverie MAIN baselines pass in ~2min, the #355 BRANCH
  hangs. Difference = the branch. BUT the calibrated probe shows #355 does NOT hang the vfork test
  (0.14% floor), so if (b), it is a DIFFERENT test in the workspace suite, not the ESRCH path.
- Lean (a) given the fix removes a spin and the symptom is a generic PMU-suite hang, but (b) needs
  ruling out by someone with runner-host access (I have none).

### FINAL calibrated acceptance (probe WD ignored/matched/20260803T100454, 46/48 waves, 45 valid, 1440 samples/label)
- head (reverie d973a85b, no-#355 calibrator): 186/1440 = **12.92%** FLAKY (fired every valid wave)
- knowngood (reverie 5e190f7d): 1/1440 = **0.07%**
- pr355 (reverie 820b2b64, #355): 2/1440 = **0.14%**
- => #355 at the known-good floor (~90x below head). Bound: 1440 samples cannot resolve ~1/2760
  (0.036%); this proves floor-not-flaky, not "residual gone". ACCEPTANCE MET.

### STAGED PIN BUMP (execute the instant #355 merges to reverie main — ~15 min mechanical):
1. `with-proxy git -C reverie fetch origin main` ; NEW=$(git -C reverie rev-parse origin/main)
   -- confirm NEW contains #355: `git -C reverie branch -r --contains <merged-sha>` includes origin/main.
2. In a SLOT hermit worktree (NOT primary): `cargo update -p reverie-core` TWICE
   (root + --manifest-path liteinst-runtime-build/Cargo.toml). NEVER bare `cargo update`.
3. sed the 4 LAGGING LiteInst short-rev cache keys to NEW's first-8-hex:
   ci/dag/portable.json, validate.sh, hermit-install/build.rs, hermit-cli/tests/common/liteinst.rs.
4. `with-proxy ./scripts/check-reverie-pin.rs` -> "Reverie pin is current".
5. Parent gitlink: stage reverie submodule at NEW (single-variable A/B per ci-hub SUBMODULE-BUMPS.md).
6. Re-run matched.sh on pinned-main tests_misc; require pr355-class floor (<=~1/768 at head-FLAKY waves).
7. Coordinator closes detcore-misc-timeout-hang-on-main after merge+bump+matched pass.
- DO NOT pin 820b2b64 directly (PR-branch tip; reverie rebase/squash-merges => new SHA => orphan;
  check-reverie-pin.rs rejects non-ancestor of reverie main). See memory reverie-pin-bump-recipe-gotchas.

### Separate flake (NOT this task): command_strict_verify
- `kernel_activity_commands_are_deterministic_under_strict_verify` @ hermit main e072d313:
  9 PASS / 1 FAIL of 10 isolated (load ~78-95) = FLAKY ~10% = RED. Worse under full-DAG load.
  Independent of the reverie pin. Needs its own ticket.

### validate 913m estimate = CPU-minutes, NOT broken
- Real ledger-derived estimate printed ~9m27s (matches 8m18s baseline). 913m = print_wall_cpu_summary
  CPU total (user+sys across ~110 busy cores of 316; 455s DAG x ~110). Correctly labeled CPU. Ledger
  clean (no poison, max wall 1246s). Only risk = presentation (big CPU next to small wall).
