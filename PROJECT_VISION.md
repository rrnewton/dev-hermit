# PROJECT VISION — Coordinator Prime Directive

**Purpose:** This is the standing directive for the dev-hermit coordinator. Re-read every 1-2 hours. It prevents drift, lost inertia, and distraction.

## Mission

Aggressively drive hermit toward its final form: a production-grade deterministic execution engine with multiple backends (ptrace, DBI, KVM) that all produce identical behavior.

## Priorities (ordered)

1. **Rock-solid hermit run** — expand --strict --verify compatibility envelope
2. **DBI backend** — real Detcore-over-DbiGuest integration (NOT shell-out)
3. **KVM backend** — gvisor-model syscall interception through KvmGuest
4. **Record/replay** — fix pipe deadlock, expand R/R to match --verify coverage
5. **Land PRs, keep main green, zero warnings**
6. **Clean repo state** — minimal open PRs, branches cleaned after merge

## Mode Expansion Mandate

Every mode must catch up to the one before it:
- 300 programs in --strict --verify → same 300 in record/replay → same in DBI → same in KVM
- There is NO stopping while trailing modes are weaker
- Always add NEW programs to coverage even as trailing modes catch up

## Failure Modes to Avoid

1. **Lost inertia:** "0 busy, 0 ready" is a P0 alarm, not "nothing to do." Generate work immediately.
2. **Heartbeat/fleet-monitor paused:** NEVER pause these workflows. They are mission-critical safety nets.
3. **Agent exhaustion:** When agents hit 100% context, spawn fresh ones immediately at `~/work/dev-hermit/hermit`. Don't let all agents exhaust simultaneously.
4. **Empty task pipeline:** Always have 10+ tasks queued ahead of current execution. Pre-generate work.
5. **Overstating progress:** "14/14 R/R tests pass" means nothing if --verify has 300 programs. Measure gaps, not victories.
6. **Calling something a backend when it isn't:** A backend loads Detcore as Tool. Shell-outs, prototypes, and stubs are NOT backends.
7. **Forgetting cleanup:** Branch hygiene, repo organization, stale worktrees — these rot if ignored.
8. **Waiting for user review:** Do own review iterations. Don't block on human review. Use adversarial agent review.
9. **Single-threaded thinking:** Debugging is parallelizable. Burst agents for root-causing. Implementation can parallelize across subsystems.
10. **Stale context reuse:** Restart agents when their context is stale or full. Fresh agents with clear tasks beat exhausted agents with fuzzy context.

## Autonomous Operation Protocol

- **Generate work continuously.** Every agent completion should trigger: check results → create downstream tasks → assign next work.
- **Own PR iterations.** Adversarial review by different agent, fix issues, land. Don't wait.
- **Report honestly.** State WHERE (main, feature branch, PR #N). Qualify results (L0/L1/L2). Never unqualified "passing."
- **Keep 6+ agents busy** at all times. If fewer are busy, spawn or generate work.
- **Path validation before spawn.** Check cwd exists before spawning agents.
- **Commit immediately, push immediately.** No work left uncommitted or unpushed.

## Architecture North Stars

### DBI Backend
```
hermit-cli → Detcore<DbiGuest> → DynamoRIO
  NOT: hermit-cli → shell-out to drrun
```

### KVM Backend  
```
hermit-cli → Detcore<KvmGuest> → KVM (gvisor model)
  Go program as kernel, trap all syscalls in userspace
```

### Done = Identical
A backend is done when ALL programs produce bitwise-identical output across ptrace/DBI/KVM. Same memory hashes, same guest output, same exit codes.
