# dev-hermit COORDINATOR HANDOFF — restart manifest (2026-08-02 ~10:00 EDT)

**Why restart:** tg sync backend down — coordinator-side taskgraph WRITES fail ('import command failed'; tgSync times out; survived config reload). Reads fine. Fix the tg sync issue, then resume.

## Post-restart checklist
1. `adoptOrphans` — re-adopt the tmux-persistent agents.
2. Verify tg-write recovered (orc.taskgraph note probe).
Do NOT resume any agent work until tg-write is confirmed fixed (probe create+update+note+close). If still broken, hold the fleet + report.
3. Re-attach the fixed lanes + resume the gated items below.
4. Confirm main GREEN (it was green all night, ~42 tasks closed overnight).

## Restart paths
- **SOFT:** `/restart orc` keeping tmux → `adoptOrphans` + resume from committed branches + per-worktree `HANDOFF.md`.
- **HARD:** tmux torn down + fresh orc → respawn fleet from the roster, each resumes from its pushed branch + `HANDOFF.md`.
- **Both:** confirm tg-write before resuming.

## Fixed agent roster + lanes (respawn if missing)
- hermit-coord (coordination), hermit-kvm (claude — KVM), hermit-liteinst (claude — LiteInst flagship), hermit-sabre (SaBRe), hermit-dbi (DBI), hermit-e9patch (e9patch), hermit-ci (CI), hermit-lander (landing). Plus dynamic hermit-NNN (231/238b/229 etc).
- KVM + LiteInst-flagship = CLAUDE (load-robust). Others codex gpt-5.6-sol.

## In-flight (agent → task, resume these)
- hermit-231 → fix-load-dependent-scheduling-vtime (IMPLEMENTED, pre-land, **P0 per owner**) — the stress-torture-caught load-dependent-scheduling determinism bug; root cause = RCB/PMU skid under load. NEEDS dual review (Claude+Codex, #141) + owner vtime-model discussion (#159).
- hermit-238b → fresh_patching_backend_architecture (e9patch-convergence read) + gvisor-systrap-benchmark-repro (DONE, local same-host, in benchmarks/) + impl-backend-perf-comparison.
- hermit-kvm → kvm_backend_stats_provider + recover_dirty_kvm_l3 + KVM-RPC-no-runtime-serialization verify (owner TODO).
- hermit-sabre → audit_cross_backend_detlog (DONE) + sabre_non_gated_parity + sabre_backend_patch_and.
- hermit-liteinst → liteinst-multiproc-and-inguest-flagship (BACKLOG, owner-gated on in-guest vDSO clock gap, PR #1466 red-gate draft) + other liteinst ratchet + stats.

## GATED — awaiting owner decisions
1. vtime P0: dual-review + vtime-model blessing (#159) → then land.
2. LiteInst flagship #1466: in-guest vDSO clock gap resolution (sacred-continuous-vtime).
3. gvisor same-host head-to-head table: review (dev-hermit/benchmarks/).
4. e9patch-wrong-architecture: DETLOG via ptrace-host not in-guest → converge to SaBRe/LiteInst in-guest model (238b read pending). Pick target model.

## Overnight wins (landed / on main)
- LiteInst in-guest fastpath 2.96× faster than ptrace (PR #1443; 5-sample strict medians ptrace 4.193s vs in-guest 1.417s — WORKLOAD: confirm exact program from hermit-liteinst).
- KVM `ls` parity fully closed (detpid + tty/winsize + getppid).
- SaBRe confirmed REAL (GDB-proven) + DETLOG-forwarder.
- CI per-cell fanout (#1447), CPU-time timeout (#4), CLI polish (#1444), examples→manifests, target→hermit/ignored.

## Key architecture finding (DETLOG audit, Hermit ca1b7fea)
detlog!=tracing::info!; ptrace/e9patch/liteinst put Tool+GlobalState in the TRACER with one file subscriber (free sequential DETLOG). SaBRe's Tool is in the injected process (detcore-sabre/src/lib.rs:139-152) with no subscriber. Fix = in-guest subscriber emitting directly + happens-before ordering (not host arrival time). Confirms e9patch is on the wrong (ptrace-host) path.
