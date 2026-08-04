# Boxing coverage gap — whole-tree reap VERIFICATION (2026-08-04)

**Task:** `close_boxing_coverage_gap`. **Author:** co-coordinator (Opus 4.8).
**Verifies against:** agent-utils `safe-ci-dag-runner` whole-tree box at HEAD `1c7c8556`
(PR #15 `run --cores K`, checked out in the agent-utils primary at verification time).

## Question

Does routing a work tree through the runner's box actually *close* the coverage gap
documented in `ai_docs/hermit-invocation-paths-boxing-coverage-map_20260804.md` (parent
`8d220ad`) — i.e. does teardown **reap orphaned guests** (setsid/double-fork escapees that
reparent to init), leave **legitimate runs unharmed**, spare **out-of-scope siblings**, and
make the **scope disappear** rather than linger empty? The crux the owner named: today's
ad-hoc containment "NEITHER THROTTLES NOR REAPS — systemd removes a scope only when EMPTY,
so a live spinner keeps its scope alive." A YES requires the box to *actively kill* the
subtree, not wait for natural exit.

## Mechanism (read from the running code, not a flag)

`agent-utils/py/safe_ci_dag_runner`:
- **`scheduler.py` L468-469, L477** — after *every* step (success OR fail, not only on
  timeout) the runner calls `reap(proc, self.cgroups, step.tag)` then `cleanup(step.tag)`
  which rmdirs the step's child cgroup. Each step is launched `start_new_session=True`
  (own process group) inside its own child cgroup.
- **`teardown.py::reap` L90-115** — writes the step cgroup's `cgroup.kill` **FIRST**
  (atomic SIGKILL of the *entire subtree*, explicitly including "setsid / double-fork
  escapees that changed session/pgid but not cgroup membership"), then `killpg` as a
  belt-and-suspenders for the no-cgroup path. On containment-enabled-but-kill-failed it
  surfaces a warning (No Silent Failure).
- **`cgroup.py::reexec_in_scope` L526+** — the whole run re-execs into a transient
  `systemd-run --user` scope; the outer scope's `cgroup.kill` SIGKILLs every child step
  cgroup + escapee, so an aborted run leaves no orphans.

This is exactly "box the whole tree → teardown `cgroup.kill` reaps guests → cgroup rmdir'd."
It does **not** rely on natural exit, so a wedged/reparented guest is killed.

## Empirical verification (this run)

Harness (gitignored `scratch/boxing-verify/run.sh`) built a DAG with one `orphan.reap`
step and N=5 `legit` steps, run via
`python3 -m safe_ci_dag_runner run --dag <file> -v --no-profile`.

- **`orphan.reap` step:** main bash spawns a **detached `setsid` "guest"** (a nonce-tagged
  sleep-loop, self-capped at ~30s so a reap failure cannot create a forever-orphan), then
  the main command exits 0 — reproducing the real leak (supervisor exits, guest reparents
  to init). Spawned guest pid `1019373`.
- An **out-of-scope sibling** (my own child, started OUTSIDE the box) ran concurrently.

Runner invocation confirmed boxing ACTIVE: `re-exec inside transient systemd scope
safe-ci-1019281.scope … cgroup boxing ACTIVE (two-level cgroup-v2 scope; per-step
memory/CPU caps + setsid-proof teardown)`.

| Property (owner's VERIFY spec) | Result | Evidence |
|---|---|---|
| Planted guest REAPED, no ppid=1 survivor | **PASS** | `kill -0 1019373` → DEAD; clean `/proc/*/cmdline` scan (excluding self) → no guest sleep-loop survivor |
| N=5 legitimate runs UNHARMED (**N=5, stated**) | **PASS** | runner: `6 passed, 0 failed, 0 aborted, 0 skipped` (5 legit + orphan) |
| Out-of-scope sibling untouched | **PASS** | sibling `kill -0` alive after run; reap scoped to the box's cgroup only |
| Scope DISAPPEARS, not lingering empty | **PASS** | `find /sys/fs/cgroup -name safe-ci-1019281.scope` → gone |

(The harness's VERIFY-1 grep reported a false FAIL: the pattern string contains the nonce,
so grep matched its own argv and `/proc/self/cmdline`. The clean re-check above is
authoritative — guest is reaped.)

## Conclusion

The mechanism **closes the gap for any tree routed through it**: whole-tree `cgroup.kill`
on teardown reaps setsid-escapee guests, legit runs are unharmed, siblings outside the
scope are spared, and the scope is removed. **Part-1** (plant → reaped) was already proven;
**parts 2 & 3** (legit unharmed / siblings + scope) are now proven here.

## Residual = ADOPTION, not mechanism (gap still LIVE in practice)

At verification time, three **new** ppid=1 orphans existed on the box — none routed through
the box, confirming adoption is not yet in place:

- `570827`, `748315` — orphaned `worktrees/dbi/hermit/target/debug/hermit run
  --backend=kvm --strict --verify -- scratch/times-probe/probe` (state R, ~402s/~267s CPU),
  dead-agent scope `run-p2790728-i338088020.scope` (KVM `--strict --verify` hang pattern).
- `3227635` — wedged e2e guest `tests/e2e/language-runtimes/bash-loop-pipe-time.sh --run`
  (state S, 2d5h etime, ~1695s CPU), dead-agent scope `run-p1138550-i104676840.scope`.

Not reaped: not my children (Hard Invariant 15). Flagged to owner for explicit-PID
authorization.

**What remains to actually close the coverage gap** (owned elsewhere, do not duplicate):
1. **Adoption** — route hermit `cargo test` / e2e `test_harness.sh` / ad-hoc `hermit run`
   through the box (the "wrapper" = `safe-ci-dag-runner run` around the tree root, NOT a
   new enforcement impl). Enforcement is owned by **hermit-231b** (runner features);
   load-immunity by **hermit-ci** (`p0_implement_load_immune`).
2. **PR #15 landing** — the whole-tree box is still a draft (`1c7c8556`).
3. **Reconcile** PR #15 with `codex/cgroups-small-default-cap` (`0cfa1c9`: 1core/1GiB/10s
   default for undeclared steps) into one boxing story.
4. **Residue not closable by opt-in box:** typed explicit-path invocations
   (`./target/{debug,release}/hermit`) and `--allow-cgroup-failure` under CI — needs a
   scope-level reaper (owner previously forbade a 2nd reaper), a hermit self-watchdog, or
   discipline. Owner decision.

See memory `boxing-coverage-gap-bare-agent-hermit-runs` and the invocation-path map
(`ai_docs/hermit-invocation-paths-boxing-coverage-map_20260804.md`).
