# HANDOFF @2026-08-04T13:57:49Z (opt agent) — portable/privileged CI DAG manifest gate OOM fix

## CURRENT STATE
Fixing the DAG manifest-gate OOM (the drain blocker). Slot: worktrees/opt, branch
`codex/dag-mem-cap-jobs-pin-test-nodes` base origin/main **b384187e**. NOTHING PUSHED yet.

## ESTABLISHED (measured/verified)
- Manifest-gate OOM is **UNIVERSAL / PR-INDEPENDENT**. Main head b384187e (which
  CONTAINS the #1583 fix) FAILED full-profile **3/3** (12:04/12:08/12:12Z) with ZERO PR
  diff. Logs: /tmp/hermit-validate.AwS98t.log (main), .4BXYRw.log (#1591), .AvIciW.log (#1470).
- **#1583 LANDED** = main head b384187e (2026-08-04 08:00Z "ci/dag: scale portable-DAG
  memory caps to a pinned job count"). It fixed build.dbi_release/doc.rustdoc but the OOM
  **RELOCATED** to nodes it did not re-cap.
- First-failing node per run: main → `test.rr_suite_contract` OOM (8 oom_kill) +
  `build.privileged_tests` OOM@8G; #1591 (b6710caa, HAS fix) → `test.rr_suite_contract`
  OOM@2G; #1470 (c6f7e9eb, NO fix) → `build.dbi_release` OOM@8G.
- #1591 CONTAINS b384187e; #1470 does NOT (`git -C hermit merge-base --is-ancestor`).
- **ROOT CAUSE**: compile-bearing nodes have UNPINNED `CARGO_BUILD_JOBS`. #1583 pinned
  build.workspace/dbi_release/rustdoc (=8) but MISSED the `test.*` nodes → on the 316-core
  box the compile fans to nproc/32 cc1plus and blows the per-step cap.
- Runner mem model (v0.11.0 --userguide): `hard_mem_max_bytes` = VERBATIM hard cap
  (overrides derived); `rss_baseline_bytes` feeds outer -j model. Fix sets BOTH:
  rss_baseline=peak@j8, hard=ceil(peak×1.25). Reference: dbi_release rss_baseline=7301444403
  (7.3G) hard=9126805504 (9.13G) @j8.

## HYPOTHESIS (NOT yet measured — measurement IN FLIGHT resolves it)
Whether test.rr_suite_contract RECOMPILES C++ at runtime (→ job-pin is the fix) vs peak is
test RUNTIME (→ cap-only). build.workspace ALREADY builds --workspace --features
third-party-backends @j8 yet rr_suite_contract still showed cc1plus killed. MEASURE decides.

## IN FLIGHT
Background job **bsq8yz5i9** in worktrees/opt/hermit: builds build.workspace @j8 then measures
test.rr_suite_contract + build.privileged_tests peak. Log:
scratch/dag-memcap-measure2-b384187e.log ; output file:
/tmp/claude-newton/claude-212630/-home-newton-work-dev-hermit/7c9e73a9-c8be-4f54-bfde-8dba539e5808/tasks/bsq8yz5i9.output
As of 13:56Z build.workspace early (0 Compiling lines).

## STARTED-BUT-NOT-PUSHED — edits are MEASUREMENT SCAFFOLDING, finalize before commit
- portable.json `rr_suite_contract`: added CARGO_BUILD_JOBS=8; cap set to TEMP
  15032385536 (14G) + classification light→cpu-bound. **Re-set cap to peak×1.25 before commit.**
- privileged.json `build.privileged_tests`: added CARGO_BUILD_JOBS=8 (both cargo invocations);
  cap TEMP 15032385536. **Re-set to peak×1.25 before commit.**

## REMAINING latent nodes (same class, decide scope): all UNPINNED compile-bearing portable
test.* — hermit_integration, arbitrary_binaries, cli, liteinst_strict(3G), sabre_examples(3G),
hermit_modes, app_strict_verify(4G; memory says OOM'd 9x under concurrency), command_strict_verify(4G),
ignored_syscall_regressions(4G); privileged cpuid.faulting. app_strict_verify is a PROVEN
concurrency OOM (see memory portable-dag-manifest-gate-release-dbi-oom.md) → include it.

## TRAPS PAID FOR
- run-node.sh needs `SAFE_CI_DAG_RUNNER=/home/newton/work/dev-hermit/agent-utils/rs/bin/safe-ci-dag-runner`
  (agent-utils is PARENT-level; run-node.sh only searches inside hermit worktree/PATH). Without it:
  "safe-ci-dag-runner not found" exit 2.
- Runner v0.11.0: `run --dag X --only group.job`.
- `ci-hub validate-status --sha X --json` verdict FAILED/exit3 can be receipt-disqualification
  OR gate-fail — read raw ledger ignored/validate-run-ledger.jsonl + the log_file to disambiguate.

## NEXT
1. Wait bsq8yz5i9; `grep -iE "peak|OOM|EXIT=" scratch/dag-memcap-measure2-b384187e.log`.
2. Set rss_baseline_bytes=peak, hard_mem_max_bytes=ceil(peak×1.25) on both nodes.
3. Pin jobs on remaining latent nodes (esp app_strict_verify).
4. DUAL-VERIFY per #1583: plant>cap→OOM 10/10; genuine@cap→no-kill 10/10.
5. Solo-validate (validate.sh via systemd-run --user producer path), commit, push
   `git push origin HEAD:refs/heads/codex/dag-mem-cap-jobs-pin-test-nodes`, draft PR to rrnewton/hermit:main.

## COORDINATOR: STOP in-flight full validates on hermit heads — all doomed by this
PR-independent OOM until this fix lands. Same class as #1583.

## DELTA @2026-08-04T13:58:27Z
Measurement bsq8yz5i9 status — Compiling lines so far: 328. Peak/exit signals:
```
[build.workspace] ✓ PASS   Build workspace (cargo build --workspace --features third-party-backends) (113s)  [Finished `dev` profile [unoptimized + debuginfo] target(s) in 1m 53s]
BUILD_WORKSPACE_EXIT=0
[test.rr_suite_contract] ✓ PASS   rr suite source contract (scratch dirs fresh and cleaned) (81s)  [Running tests/rr_suite.rs (target/debug/deps/rr_suite-1d6204bd2cf1ad75)]
RR_SUITE_EXIT=0
[build.privileged_tests] ✓ PASS   Build Hermit and the focused test binaries used by the privileged lane (25s)  [Executable tests/misc/mod.rs (target/debug/deps/tests_misc-61b9396040d7da30)]
PRIV_TESTS_EXIT=0
```
If peaks present: hard_mem_max_bytes=ceil(peak*1.25), rss_baseline_bytes=peak, on
rr_suite_contract (portable.json) + build.privileged_tests (privileged.json); revert the
TEMP 15032385536 caps. Then dual-verify + pin remaining latent nodes + solo-validate + push.

HANDOFF-FINAL @2026-08-04T13:58:27Z: READ THIS FILE (ai_docs/transient/HANDOFF-dag-memcap-jobs-pin-20260804-1357.md) — it is complete and current. Supersedes the earlier text-only status.

## DELTA-2 @2026-08-04T13:59:09Z — MEASUREMENT PASSED
build.workspace PASS 113s; test.rr_suite_contract PASS 81s; build.privileged_tests PASS 25s
— ALL at CARGO_BUILD_JOBS=8 with the 14G measurement cap. CONFIRMS: job-pin=8 is the fix
(nodes compile+run fine at j8; the CI OOM was nproc-fanout). ESTABLISHED, not hypothesis.
- Peak RSS numbers: NOT extracted — runner logs peaks to profile dir only, printed
  to stdout ONLY on OOM. Find in worktrees/opt/hermit/.safe-ci-dag-runner/profiles/ or
  ignored/ci/perf/run-node/. NEXT AGENT: read peak from there, else re-run with a TIGHT cap
  (e.g. 3G rr_suite, 8G priv) and read the peak≈ only if it OOMs, or use `sweep` subcommand.
- ANCHOR (coordinator preflight): base b384187e is POST-ANCHOR (validate OK). Anchor=bfb0a9ef1c30 (2026-08-03 18:43).
- CAP-SETTING FALLBACK if peak unextractable: since nodes pass at j8, a conservative safe cap =
  match sibling pinned nodes — rr_suite_contract→ set rss_baseline≈2.5G, hard≈4G (was 2G, too tight);
  build.privileged_tests→ keep 8G hard but ADD job-pin (pin alone may suffice at 8G). BUT prefer
  a real measured peak: re-run each node with cap just above expected and read runner metrics.
- REMINDER: revert the TEMP 15032385536 caps in both files before commit.

## DELTA-3 @2026-08-04T13:59:23Z — PEAK RSS from profile CSVs (THE cap-setting numbers)
CSV: ignored/ci/perf/run-node/{portable,privileged}/step_profiles_*.csv
```
timestamp,machine_id,container_class,git_sha,outer_jobs,profile_base_sha,enforcement_kind,runner_name,step,classification,inner_jobs,elapsed_s,returncode,ok,timed_out,cpu_timed_out,oom_kills,peak_bytes,thread_peak,effective_cores,user_s,sys_s,throttled_s,quota_utilization_pct,external_cpu_s,external_cores,co_tenants_start,co_tenants_end,ambient_bucket,load1_start,load1_end,load5_start,load5_end,host_cpu_psi_avg10_start,host_cpu_psi_avg10_end,host_cpu_psi_avg60_start,host_cpu_psi_avg60_end,host_memory_psi_avg10_start,host_memory_psi_avg10_end,host_memory_psi_avg60_start,host_memory_psi_avg60_end,host_io_psi_avg10_start,host_io_psi_avg10_end,host_io_psi_avg60_start,host_io_psi_avg60_end,step_cpu_psi_avg10_start,step_cpu_psi_avg10_end,step_cpu_psi_avg60_start,step_cpu_psi_avg60_end,cpu.burst_usec,cpu.nice_usec,cpu.nr_bursts,cpu.nr_periods,cpu.nr_throttled,cpu.system_usec,cpu.throttled_usec,cpu.usage_usec,cpu.user_usec
2026-08-04T13:57:53,AMD_EPYC_9D85_158-Core_Processor,affinity316_cpu-max-unknown,b384187efd725c504d69281f043d442325d4fcb2,1,b384187efd725c504d69281f043d442325d4fcb2,unverified,local,test.rr_suite_contract,cpu-bound,316,80.717,0,true,false,false,0,3766153216,42,6.0382,422.010,65.378,0.000,,2033.693,25.195,0,0,busy,75.690,43.560,59.790,53.110,0.00,0.00,1.25,0.31,0.04,2.22,0.40,1.37,0.00,0.15,0.01,0.06,0.00,0.00,0.00,0.00,0,0,0,0,0,65377674,0,487387217,422009542
2026-08-04T13:58:19,AMD_EPYC_9D85_158-Core_Processor,affinity316_cpu-max-unknown,b384187efd725c504d69281f043d442325d4fcb2,1,b384187efd725c504d69281f043d442325d4fcb2,unverified,local,build.privileged_tests,cpu-bound,316,25.214,0,true,false,false,0,1759023104,58,3.6757,78.954,13.724,0.000,,1402.162,55.611,0,0,busy,43.560,49.950,53.110,54.090,0.00,0.06,0.31,0.28,2.22,0.81,1.37,1.16,0.15,0.15,0.06,0.13,0.00,0.00,0.00,0.00,0,0,0,0,0,13723837,0,92678015,78954177
```
Read the peak-RSS column above. Set rss_baseline_bytes=peak, hard_mem_max_bytes=ceil(peak*1.25)
on rr_suite_contract (portable.json) + build.privileged_tests (privileged.json), revert TEMP 14G caps,
pin remaining latent test.* nodes, then solo-validate (base b384187e is POST-ANCHOR ✓), push draft PR.

## DELTA-4 @2026-08-04T14:00:02Z — FINAL CAP NUMBERS (established, peak_bytes @j8, oom_kills=0)
- test.rr_suite_contract: peak 3766153216 (3.51G). SET rss_baseline_bytes=3766153216,
  hard_mem_max_bytes=4831838208 (~4.5G = peak*1.28). Old cap 2G was the OOM cause.
- build.privileged_tests: peak 1759023104 (1.64G, INCREMENTAL — under-estimate; CI builds
  from scratch). KEEP hard_mem_max_bytes=8589934592 (8G) + job-pin (pin is the real fix;
  measured 1.64G proves pin collapses the 8G+ fanout OOM). rss_baseline_bytes=5368709120 kept.
- Applying these edits now to make branch commit-ready.
HANDOFF-FINAL @2026-08-04T14:00:02Z: READ THIS FILE — DELTA-4 has the final numbers; earlier deltas are history.

## DELTA-5 @2026-08-04T14:11:27Z — COMMITTED + PUSHED + PR OPEN; peak_bytes TRAP found
- PR: https://github.com/rrnewton/hermit/pull/1597 (draft, [impl agent, opus-4.8])
- Branch: codex/dag-mem-cap-jobs-pin-test-nodes | HEAD a968b0cf | base b384187e (POST-ANCHOR)
- TRAP (established, cost a mis-set cap): step_profiles peak_bytes == cgroup memory.peak
  is CAP-INFLUENCED — compile page-cache is reclaimable, so it fills available headroom
  and OVER-STATES required memory. Negative control: rr_suite_contract at a 512MiB cap
  PASSED via reclaim (oom_kills=0, rc=0). => the JOB-PIN is the fix, NOT the cap number.
  Earlier "3.51G peak" was RSS-under-headroom, mostly reclaimable.
- FINAL caps (matched to #1583 additive+GiB convention, with MEM-CAP DERIVATION desc on
  each node): rr_suite_contract rss=3.5GiB hard=5.0GiB (was 2G, too tight unpinned);
  privileged_tests pin-only, kept generous 8G (warm measure was incremental/unreliable).
- IN FLIGHT: full portable solo-validate @a968b0cf via systemd-run --user; log at
  worktrees/opt/hermit/ignored/validate-logs/ (see .latest-portable-log).
- NEXT: on validate PASS, mark PR ready-ish (still coordinator lands); coordinator must
  confirm authoritative "Regular tests (GitHub-managed portable)" green at head. LATENT
  nodes (app_strict_verify etc.) deliberately OUT OF SCOPE — this PR fixes only the 2
  proven-failing nodes.
HANDOFF-FINAL @2026-08-04T14:11:27Z: read ai_docs/transient/HANDOFF-dag-memcap-jobs-pin-20260804-1357.md DELTA-5 — complete and current (supersedes DELTA-4).

## DELTA-6 @2026-08-04T14:23:31Z — VALIDATE RAN; my fix WORKS; ONE node left (strict_compat)
CURRENT STATE: PR #1597 pushed @a968b0cf; full portable solo-validate @a968b0cf COMPLETED
(boxed, CPU/wall 23.8x — real run). Log: worktrees/opt/hermit/tmp -> /tmp/hermit-validate.2FA0He.log
ESTABLISHED (measured, this run):
- test.rr_suite_contract -> ✓ PASS 112s, no OOM. MY FIX WORKS.
- EVERY other portable node PASSED UNPINNED: app_strict_verify✓ hermit_integration✓ cli✓
  command_strict_verify✓ hermit_modes✓ detcore_unit✓ regular_crates✓ dbi_release✓ etc.
  => REFUTED: the "latent nodes need pinning" hypothesis. Do NOT broaden the PR to them.
- ONLY test.strict_compat -> ✗ FAIL OOM-KILLED (cap≈6.0GiB peak≈6.0GiB, 8 oom_kills, 64s).
  It is the LAST/terminal portable node (everything deps on it), which is why it only
  surfaced after rr_suite/earlier OOMs cleared. Third node #1583 missed, same class.
- TRAP (established): strict_compat's reverie-dbi/build.rs:339 "panic" (exit 101) is a
  DOWNSTREAM OOM-CASCADE artifact — its child cc1plus was SIGKILLed by cgroup memory.max
  ("c++: fatal error: Killed signal terminated program cc1plus"). NOT a real build break.
FILE:LINE: strict_compat node = ci/dag/portable.json:470-479. cmd (line 472) is a NESTED
  ./validate.sh --portable-strict-compat-only (does a cold RELEASE build). Fix: prefix
  CARGO_BUILD_JOBS=8 (env inherits into the nested cargo). cap 6442450944, rss 3221225472.
- Earlier TRAP still holds: step_profiles peak_bytes==cgroup memory.peak is CAP-INFLUENCED
  (page-cache reclaimable) -> over-states required mem; JOB-PIN is the fix not the cap #.
IN FLIGHT: none (validate finished). PUSHED: a968b0cf (branch codex/dag-mem-cap-jobs-pin-test-nodes).
STARTED-NOT-PUSHED: about to pin strict_compat (portable.json) + commit + re-validate.
NEXT: (1) prefix CARGO_BUILD_JOBS=8 on strict_compat cmd, add MEM-CAP DERIVATION desc,
keep 6.0G cap; (2) commit; (3) re-run full portable solo-validate via systemd-run --user;
(4) on GREEN, push, PR #1597 ready (coordinator lands after authoritative portable gate
green at head). Producer path: systemd-run --user + SAFE_CI_DAG_RUNNER=.../agent-utils/rs/bin.
HANDOFF-FINAL @2026-08-04T14:23:31Z: read ai_docs/transient/HANDOFF-dag-memcap-jobs-pin-20260804-1357.md DELTA-6 — complete and current (supersedes DELTA-5).
