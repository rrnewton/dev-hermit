# Deep audit of the tick-hub gates — 2026-08-08

**Why this exists.** Owner, web, 2026-08-08:

> I never was involved with ANY of those tick hub creations. I would suggest a deep
> audit of them. They are trying to rigidly remind the agent of things it may not
> notice. This is good for external systems like github but it seems dangerous in
> other cases.

Auditor: `tickhub-surgeon`. All findings below are from reading the source and
measuring the running system on `devbig1073`; every number states how it was
obtained. Nothing in this document was inferred from a gate's own description.

---

## 1. The governance finding (the axis the owner actually raised)

**Git history cannot establish provenance for a single one of these gates.**

All 17 arrived in **12 commits over 6 days**, 2026-08-03 → 2026-08-07. Both
mechanisms that could attribute them are blank:

| Attribution mechanism | Result |
|---|---|
| git author | `Ryan Newton` on **12 of 12** — the shared machine identity every agent commits under. Cannot distinguish owner from agent. |
| AGENTS.md role + team trailer | present on **0 of 12** |

So the one mechanism designed to attribute autonomous work was absent on exactly
the commits where it mattered most. This neither corroborates nor refutes the
owner's recollection from git alone — but combined with the owner's statement, the
conclusion is that **this is self-authored monitoring that issues instructions to a
coordinator operating under an autonomous close-and-respawn mandate, with no record
of who authorized any of it.**

The whole surface is 6 days old. It accreted fast, unreviewed, and — as the four
defects below show — faster than it was verified.

Commit map:

| Commit | Date | Gates introduced |
|---|---|---|
| `8698bd1` | 08-03 | primary_checkout_snapshot, github_main_health, pull_request_health, **stuck_agents** |
| `7a42ff5` | 08-03 | speculative_land_obligations |
| `4ed8e67` | 08-03 | ci_queue_health |
| `809eb05` | 08-03 | ci_lane_saturation |
| `847bf49` | 08-03 | **active_work_reconciliation** |
| `6f91646` | 08-03 | agent_container_lifecycle |
| `61e1e28` | 08-03 | memory_skill_sync (never activated) |
| `94c363c` | 08-04 | owner_tooling_directives |
| `dbee4e3` | 08-05 | **unowned_residue** |
| `3c421b2` | 08-06 | unpushed_parent_commits |
| `0630284` | 08-07 | **worktree_new_dead_owner**, validate_wall_regression, validate_wall_series_readiness |
| `e886201` | 08-07 | merge_gate_refire_due |
| `ecb2615` | 08-07 | **unowned_high_priority_backlog** |

Bold = removed 2026-08-08 (`119aa11`).

---

## 2. Four failure shapes

Three were known when this audit was commissioned. The fourth was found during it
and is the most damaging.

1. **Bound to a moving reference it cannot catch.** `primary_checkout_snapshot`
   demanded the parent equal a live-queried `origin/main` while 13 agents push it.
2. **Alarms on a condition the recipient cannot clear.** `merge_gate_refire_due`
   paged every 30 min for a backlog it deliberately does not auto-drain.
3. **The process outlives its own report.** See §3 — this is universal to the
   harness, not a property of any one gate.
4. **Mutation under a read-shaped name.** `unpushed_parent_commits` sounds like a
   report and performs remote pushes; `primary_checkout_snapshot` sounds like a
   snapshot read and commits + pushes to parent `main`.

The unifying rule, now recorded in `tick-hub.yaml`:

> **An alarm must be satisfiable by the actor it instructs.** Otherwise it is
> obeyed in a loop or muted — and a muted gate is *silently disarmed*, which is
> strictly worse than never having built it.

Three antidotes already exist in-tree and are now each in use: **DELTA**
(`slot_disk_residue`, `gate_refire --gate`), **SUSTAINED-ACROSS-N**
(`lane_health`), **TIME-BUDGETED DEFERRAL** (`primary_snapshot_gate`).

---

## 3. Shape 3 is universal, and it has a live high-impact instance

### 3a. Every gate can outlive its report

`tick_hub/probes.py:34` runs each gate with
`subprocess.run(["bash","-c",cmd], timeout=30)`. On timeout Python kills **the
direct child only** — the `bash -c`. It does not signal the process group. Every
grandchild (the python gate script, its `git`/`gh` subprocesses) survives,
reparents to init, and keeps running.

So the tick prints `timed out after 30s`, records could-not-measure, and moves on
**while the work continues detached**. For a read-only gate that costs some CPU and
API quota. For a *mutating* gate the survivor performs writes after the tick
believed it failed.

Measured live during this audit: two orphaned
`unpushed_parent_commits.py --scope all --rescue` process trees at `ppid=1`,
running from `/tmp/post-commit-lock-test.AECFbV/{fixed,legacy}/`. Those belong to
another agent's A/B harness, not to the production tick — but their existence, and
the `timeout --signal=TERM --kill-after=30 240` wrapper in the `fixed` arm, show
this bug is already being chased from another direction.

### 3b. The serialized parent-main lock is held by inherited fd 9, not by its owner

**This is the most consequential finding in the audit.**

`scripts/parent-main-write` takes its mutex with `exec 9>"$lock"; flock 9`. A POSIX
`flock` is released when **every** file descriptor referring to that open file
description is closed — not when the acquiring script exits. Fd 9 is inherited by
every descendant.

`git commit` inside `parent-main-write commit` fires the **post-commit hook**,
which does exactly this (canonical hook, lines 18-19):

```bash
( "$root/ci-hub/health/unpushed_parent_commits.py" --scope all --rescue \
    >> "$root/ignored/unpushed-parent-rescue.log" 2>&1 & ) >/dev/null 2>&1
```

A detached background job that performs **network pushes** and inherits fd 9. It
therefore holds the parent-main mutex for its entire lifetime, long after
`parent-main-write` has printed `PARENT_MAIN_WRITE published=... ancestry=1/1` and
exited.

**Observed directly**: the lockfile recorded `pid=950753`, that pid was **GONE**,
and 12 unrelated descendant processes held the lock fd open. A subsequent writer
sees `REFUSED: another parent-main writer owns ... after 0s` naming a dead pid.

**Controlled reproduction**, own processes only, both directions:

```bash
# BROKEN — reproduces the production pattern
exec 9>the.lock; flock -n 9
( sleep 25 & ) >/dev/null 2>&1      # the post-commit pattern
exit                                 # "its own report says done"
=> LOCK STILL HELD by the detached grandchild (pid=… fd=9 cmd=sleep 25)

# FIXED — one token
( sleep 25 9>&- & ) >/dev/null 2>&1  # close the inherited lock fd
=> LOCK FREE, and the background job still runs
```

**Recommended fix:** close the inherited descriptor at the background launch
(`9>&-`, or defensively `{9..255}>&-`). This is strictly better than the timeout
wrapper being A/B tested: a `timeout 240` still holds the mutex for up to 240
seconds, whereas `9>&-` releases it immediately while the rescue push proceeds.
**Not applied here** — `/tmp/post-commit-lock-test.{fixed,legacy}` shows another
agent owns this experiment; this audit hands them the mechanism and the bracket.

### 3c. What this cost, measured

Four consecutive `parent-main-write` publish attempts by this agent were refused
during this session. Landing a 3-file commit required building it off `origin/main`
with a temporary index and `commit-tree`, because the shared parent tree could not
be fast-forwarded (an incoming commit touched a file another agent held
uncommitted, and git correctly refused to clobber it).

### 3d. One claim in the commissioning brief, corrected

The brief states `unpushed_parent_commits --rescue` "holds the serialized
parent-main writer lock … it is how it acquired the lock in the first place". The
*conclusion* is right; the *route* is not. Its own pushes go to
`refs/heads/rescue/auto-<sha>`, and the pre-push gate is destination-ref-scoped —
`[ "$remote_ref" = refs/heads/main ] || continue` — so a rescue push never invokes
`verify-push` and never takes the lock itself. It acquires the mutex **by
inheritance from the post-commit hook of whoever was publishing**, per §3b. This
matters for the fix: hardening `unpushed_parent_commits` alone would not release
the lock; the fd must be closed at the hook.

---

## 4. The 17, on three axes

**Observability** = can ORC see this directly? If yes it must not be policed here.
**Blast radius** = what does firing this *cause*?

| # | Gate | Cadence | Command | Observability | Blast radius | Verdict |
|---|---|---|---|---|---|---|
| 1 | `merge_gate_refire_due` | 1800s | `gate_refire.py --gate` | **External** — GitHub merge-gate parking; ORC blind | Page → human runs `--refire`. No autonomous action | **KEEP, re-scoped** `8a0545d`: was standing-state + literal `{summary}`; now delta + names PRs |
| 2 | `worktree_disk_residue` | 900s | `slot_disk_residue.py --gate` | **Local disk** — ORC has no shell | Detect-only; reclaim needs a recovery SHA | **KEEP** (new `119aa11`, replaces #13) |
| 3 | `validate_wall_regression` | 3600s | `wall_series.py --gate-regression` | **Ledger** — ORC blind | Report only; currently HOLDS at rc=0 | **KEEP** |
| 4 | `validate_wall_series_readiness` | 86400s | `wall_series.py --readiness` | **Ledger** — ORC blind | Report only | **KEEP** — standing by design, but daily; re-verify the 90d/3.91d premise (see §5) |
| 5 | `owner_tooling_directives` | 3600s | `directives/check.py` | **Owner intent recorded outside TaskGraph** — ORC blind | Report only | **KEEP — high value**, but currently red for a lineage artifact; see §5 |
| 6 | `unpushed_parent_commits` | 900s | `unpushed_parent_commits.py --scope all --rescue` | **"Not on GitHub"** — external by construction | ⚠️ **MUTATES**: pushes `rescue/auto-*`. Additive, cannot destroy | **KEEP, but see §3b/§6** — mutation under a read-shaped name; self-clears via `--not --remotes` |
| 7 | `speculative_land_obligations` | 300s | `ci-hub watch-obligations --once --gate` | **External** — PR/landing state | Report only; discrete items that clear | **KEEP** |
| 8 | `primary_checkout_snapshot` | 300s | `operational_health.py primary-snapshot` | **Local git** — ORC has no shell | ⚠️ **MUTATES**: commits + pushes parent `main`. Touches no agent | **KEEP, re-scoped** `0e14559`: moving-reference + ordering bug fixed; 60-min deferral budget |
| 9 | `github_main_health` | 900s | `operational_health.py github-main` | **External** | Report only | **KEEP** |
| 10 | `pull_request_health` | 900s | `operational_health.py pull-requests` | **External** | Report only | **KEEP** |
| 11 | `ci_queue_health` | 900s | `operational_health.py queue-health` | **External** | Report only | **KEEP** — shape (c) risk: instantaneous sample, no sustain |
| 12 | `ci_lane_saturation` | 900s | `lane_health.py tick` | **External** — runner lanes | Report only | **KEEP** — already the SUSTAINED-ACROSS-N reference implementation |
| 13 | `worktree_new_dead_owner` | 900s | `worktree_liveness.py --fail-on-new-dead` | ❌ **ORC owns agent liveness** | 🔴 Feeds autonomous close-and-respawn | **REPLACED** by #2. Reported an agent dead while it held the freshest activity timestamp on the box |
| 14 | `stuck_agents` | 300s | `operational_health.py agents` | ❌ **ORC owns the fleet** | 🔴 Feeds autonomous close-and-respawn | **REMOVED** `119aa11` |
| 15 | `active_work_reconciliation` | 300s | `ci-hub active-work --gate` | ❌ **ORC owns tasks + agents** | 🔴 Task reassignment / agent replacement | **REMOVED** `119aa11` |
| 16 | `unowned_high_priority_backlog` | 300s | `unowned_backlog.py --gate` | ❌ **ORC owns TaskGraph** | Report; censused a DB the orchestrator could not see for a whole session | **REMOVED** `119aa11` |
| 17 | `unowned_residue` | 3600s | `residue_sweep.py --gate` | ❌ **ORC fleet + TaskGraph** | Report + routes notes | **REMOVED** `119aa11`; its one non-ORC signal is covered by #2 |
| — | `agent_container_lifecycle` | 300s | `agent-podman.rs reconcile` | **Containers** — libpod scopes outside the agent cgroup; ORC blind | 🔴 **was** `--apply` → `podman stop`+`rm` | **KEEP, re-scoped** `119aa11`: `--apply` withdrawn; audit retained |
| — | `memory_skill_sync` | 3600s | *(commented out)* | n/a | n/a | **INACTIVE since `61e1e28`** — never activated. See §5 |

Net: **17 → 13 active.** 4 removed, 3 re-scoped, 1 replaced, 9 kept unchanged.

Every removal was an ORC-observability failure with a destructive or misleading
blast radius. **No gate was removed for watching something real.**

---

## 5. Gates whose stated purpose may no longer exist

- **`memory_skill_sync`** — committed 2026-08-03 and deliberately left commented
  out ("Activation is the owner's call"). Five days inactive. Either activate it or
  delete it; a permanently-disabled block is a claim of coverage that does not
  exist.
- **`validate_wall_series_readiness`** — its premise is retention 3.91d < 90d and
  conditioning 4/26. Both are dated 2026-08-07 measurements. **Not re-derived
  here** (would need a ledger query). If retention has since grown, the gate is
  reporting a blocker that has cleared. Flagged, not concluded.
- **`ci_queue_health`** — the only remaining gate that pages on an instantaneous
  sample, which its neighbour `ci_lane_saturation` explicitly contrasts itself
  against. Not a defect today; the shape (c) candidate if it becomes noisy.

- **`owner_tooling_directives` — a claim I was given, checked, and must qualify.**
  The commissioning brief credits this gate with "21 pieces of dropped owner
  intent", and it is the reason the brief warns against removing gates that look
  like noise. The gate genuinely earns its place — owner intent recorded outside
  the TaskGraph is invisible to ORC and to every other check. But its live output
  right now is not 21 fresh drops:

  ```
  state=red  source_rows=16  records=21
  satisfied=0 partial=0 open=0 gated=0 needs_owner=0 unaccountable=0
  missing_task=21  not_landed=0  unverifiable=0  drift=21
  ```

  **All 21 records are `missing_task`** — their task ids do not resolve in the
  current database. That is the `hermit1.db` → `hermit2.db` lineage split, not 21
  newly dropped directives. The in-flight fix is `fe29f35` *"directives: resolve
  tasks across the database lineage, and split task_not_found from missing_task"*.

  Two consequences worth stating. First, the *tracking* value (21 directives under
  watch, 16 source rows) is real and is the reason to keep the gate. Second, until
  `fe29f35` propagates, this gate is red every hour for a reason its recipient
  cannot clear — **failure shape 2**, arrived at from a different direction. It
  should self-resolve; if it does not, it needs the same delta or deferral
  treatment as #1 and #8.

---

## 6. Recommended follow-ups (none applied here)

1. **`9>&-` at the post-commit hook's background launch** (§3b). Highest value on
   the board: it unblocks parent-main writes fleet-wide. Owner:
   whoever owns `/tmp/post-commit-lock-test`.
2. **Kill the process *group* on gate timeout**, not just the child
   (`start_new_session=True` + `killpg`) — `tick_hub/probes.py`. This closes shape 3
   generally rather than per-gate. Lives in `rrnewton/agent-utils`.
3. **Rename mutating gates to say so**, or split them. `unpushed_parent_commits`
   and `primary_checkout_snapshot` both read as reports and both write.
4. **`rescope-agent-container-reclaim-to-process-evidence`** — filed; restore
   autonomous container reclaim only on process evidence, never an agent census.
5. **Require the role+team trailer on tick-hub changes.** 0 of 12 carried one. A
   pre-commit check on `ci-hub/health/tick-hub.yaml` would close the governance gap
   the owner raised, at the point where it recurs.

---

## 7. Admission tests now recorded in `tick-hub.yaml`

Both are inline in the config, where the next author meets them before adding a
gate:

1. **Can ORC observe this directly?** Yes → it does not belong here.
2. **Is this alarm satisfiable by the actor it instructs?** No → it will be looped
   on or muted.

To which this audit adds a third, from §3:

3. **Can this gate's work outlive its own report, and does it mutate?** If both,
   it needs process-group teardown and must not hold a shared lock.
