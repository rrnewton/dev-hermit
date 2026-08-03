# Process-improvements retrospective — 2026-08-02

- **Author:** hermit-coord (co-coordinator), Opus 4.8
- **Task:** `process-improvements-retro` (P1); blocks `adopt-github-merge-queue` (P0)
- **Scope:** step back from today's blockers and turn each into a concrete,
  enforceable process rule — then wire the durable ones into skills.
- **Method:** ground-truth only — git history, PR/run state, and task notes, not
  agent chatter. Each finding cites an exact SHA, PR, or run.

Companion artifacts already landed today, referenced below:
[CI-recovery adversarial review](transient/2026-08-02-ci-recovery-process-adversarial-review.md),
the `ci-debugging` skill ([PR #1493](https://github.com/rrnewton/hermit/pull/1493)),
and the `validate.sh --only` selector ([PR #1492](https://github.com/rrnewton/hermit/pull/1492),
landed `98149f7f`).

---

## The seven failure modes

Each entry: **what happened** (with evidence) → **root cause** → **the rule** →
**where it's wired**.

### 1. Claiming green on WARM checkouts; stale-state confusion

**What happened.** Results were reported green from warm/incremental checkouts,
and stale pre-fetch snapshots produced phantom "ahead/behind" alarms. Concrete
example today: the `hermit` primary showed `main [ahead 1]` with stray commit
`84b7d01a` "Record validation runs in parent ledger". A blind reset would have
looked like data loss; a blind "it's ahead, push it" would have created a
duplicate. Measuring first showed `84b7d01a` had an **identical patch-id
(`c6bb8017`)** to `e4381a1c`, already landed via PR #1502, and was also preserved
on `origin/codex/validate-run-ledger` + local `w1502` — a pure duplicate, safe to
reset. The `reverie` "behind" warning was a **stale pre-fetch snapshot**: after
`git fetch`, HEAD already equalled `origin/main` (`d2fb9a05`), zero ahead/behind.

**Root cause.** Warm caches and pre-fetch refs are not ground truth. A green run
on an incremental build can hide a cold-build break; a `git status` before a fetch
can invent divergence.

**The rule — cold-verify-by-default + physical-plausibility.**
- Authoritative validation runs against a **cold/fresh** tree, or explicitly
  labels the result "warm, not cold-verified." Never headline green from a warm
  checkout without saying so.
- Before acting on ahead/behind/dirty state, **fetch first**, then re-measure.
- Apply a physical-plausibility check: a 3-second "full build+test" is not
  plausible — suspect a warm cache or a skipped step.
- "Measure twice": for any destructive reconciliation, prove the state
  (patch-id / ancestry / reachability) before `reset`. Never `git clean`.

**Wired:** `repo-cleanliness` + `hermit-coord` skills (primary-freshness:
fetch-then-measure, cold-verify labelling). This retro is the reference example.

### 2. Agents STALLING at decision menus instead of executing

**What happened.** Agents presented option menus ("shall I do A or B?") and waited,
for changes they were already authorized to make — burning wall-clock on
round-trips the mission explicitly pre-authorizes (close+respawn, land routine
work, publish draft PRs).

**Root cause.** Treating routine authorized operations as approval-gated. The
mission statement is "forward-driving, self-healing… drives all work forward
without stalling on approval for routine operations."

**The rule — execute-don't-deliberate for authorized changes.**
- If the task or standing policy already authorizes the action, **do it**, then
  report what you did — do not ask which option to pick.
- Reserve `AskUserQuestion` for genuinely owner-only decisions (irreversible,
  outward-facing, or policy-changing). A menu is a last resort, not a status
  update.
- When choosing among equivalent routine paths, pick the obvious default, state
  it in one line, and proceed.

**Wired:** `hermit-coord` skill (dispatch discipline); reinforced by the mission
header in `AGENTS.md`.

### 3. Coordinator OVER-MANAGING critical agents

**What happened.** The coordinator sent idle-check pings to agents mid-flight on
critical work, interrupting their context for no new information.

**Root cause.** Confusing "liveness monitoring" with "poking." A productively
working agent needs no ping; the ping costs it context and the coordinator a turn.

**The rule — don't-manage-critical-agents.**
- Suppress idle pings to an agent that is actively producing (commits, PR
  updates, task notes within the interval). Read its ground-truth output instead.
- Intervene only on real stall signals: no git/PR/note progress across the
  interval, a stream error, or a blocker note.
- Prefer reading the artifact (branch tip, PR checks, task note) over asking the
  agent for a status it already emitted.

**Wired:** `hermit-coord` skill (health-check = read artifacts, not poll agents).

### 4. Accepting BS blocker-narratives

**What happened.** Plausible-sounding blocker stories were accepted at face value
("CI can't work because…", "the submodule is missing…") without first-principles
interrogation. Example class: a `DynamoRIO configure ENOENT` reported as a missing
submodule when the real cause was **cmake absent** (see #6).

**Root cause.** Narrative accepted before mechanism verified. A blocker is a
hypothesis, not a fact, until reproduced.

**The rule — first-principles-blocker-interrogation.**
- For every blocker ask: *what exactly failed (exact error + exit status), why,
  and do we even need the thing that's blocked?*
- Reproduce the blocker with the smallest command before accepting it. An `os
  error 2` / ENOENT is a **spawn** failure (missing tool), not automatically a
  missing input.
- Distinguish "impossible" from "not-yet-provisioned" from "wrong layer."
- Reject a blocker narrative that has no exact error attached.

**Wired:** `ci-debugging` skill (classify locally-reproducible vs env-only; read
the exact failure), `hermit-coord` skill (blocker interrogation).

### 5. Ruleset / merge-gate confusion

**What happened.** Repeated confusion between three distinct gating layers:
GitHub **Actions workflow** results, classic **branch-protection**, and the newer
**repository ruleset**. `merge-gate` was read as a diagnostic when it is a
re-fire placeholder that is **red-by-design until the portable lane completes**.

**Root cause.** Three overlapping mechanisms with similar names; no single
reference for which check is authoritative and how a merge is actually enqueued.

**The rule — know the gating model.**
- `rrnewton/hermit:main` is gated by a **ruleset** requiring **two** checks:
  the `merge-gate` job **and** `Regular tests (GitHub-managed portable)`. A
  locally-validated label satisfies **only** `merge-gate`, never the portable
  check.
- `merge-gate` red before portable finishes is **noise**, not a failure — read
  the portable rollup's per-job results.
- Enqueue via `gh pr merge --rebase --auto`; a queued/stale/cancelled check is
  not green.
- This is exactly the structural fragility that `adopt-github-merge-queue` (the
  P0 this retro blocks) is meant to remove.

**Wired:** `ci-debugging` skill (merge-gate is a placeholder; authoritative
gate); `hermit-lander` skill (enqueue mechanics + dual required checks).

### 6. cmake/toolchain provisioning gap masquerading as a code bug

**What happened.** On a fresh box, `failed to configure DynamoRIO: No such file
or directory (os error 2)` was read as a missing submodule / code bug. The real
cause was **cmake absent** — a spawn ENOENT from the build script. Fixed on main:
`718d83d3` "Fail fast in `make` when the native build toolchain is missing" and
`826f64e8` "make install-deps: auto-install the native build toolchain when
missing" (see also PR #1499's preflight).

**Root cause.** No toolchain preflight, so a provisioning gap surfaced as an
opaque runtime error deep in a build step and was misattributed to product code.

**The rule — install-deps-asserts-toolchain.**
- `make`/`make install-deps` **preflights and asserts** the native toolchain
  (cmake, libunwind headers, …) and fails fast with a clear "install X" message
  before any compile.
- Read `os error 2` / ENOENT from a build step as a **missing-tool** hypothesis
  first; check `which <tool>` before blaming a submodule or source.

**Wired:** landed in the `Makefile` (`718d83d3`, `826f64e8`); documented in the
build section of `AGENTS.md`.

### 7. Reporting agent-chatter vs git ground-truth

**What happened.** Progress reported from what an agent *said* rather than what
git/PR state *shows* — the precondition for phantom closures (a task "done" whose
work never landed).

**Root cause.** Chatter is cheaper to read than ground truth, but only ground
truth is verifiable.

**The rule — ground-truth-not-chatter.**
- Bind every status claim to a SHA / PR / run, never a branch name or an agent's
  assertion (the evidence-block rule in `AGENTS.md`).
- Coordinator closes a task only after the merge commit is reachable from
  `origin/main` — never on a chat "it's landed."
- Health rollups read artifacts (branch tips, PR checks, task notes), not agent
  self-reports.

**Wired:** `AGENTS.md` Task Lifecycle (IMPLEMENTED-tag vs coordinator-close);
`hermit-coord` skill (evidence bound to commits).

---

## Skill-wiring summary

| Rule | Skill(s) | Status |
| --- | --- | --- |
| cold-verify-by-default + measure-twice | repo-cleanliness, hermit-coord | to wire |
| execute-don't-deliberate | hermit-coord | to wire |
| don't-manage-critical-agents | hermit-coord | to wire |
| first-principles-blocker-interrogation | ci-debugging, hermit-coord | ci-debugging done (#1493); coord to wire |
| know-the-gating-model | ci-debugging, hermit-lander | ci-debugging done (#1493) |
| install-deps-asserts-toolchain | Makefile + AGENTS.md | **landed** (`718d83d3`, `826f64e8`) |
| ground-truth-not-chatter | AGENTS.md, hermit-coord | AGENTS.md done; coord to reinforce |

The highest-leverage remaining wiring is a single consolidated `hermit-coord`
skill update covering rules 1, 2, 3, 4, and 7 (coordinator-facing); it is a
docs-only PR against `rrnewton/hermit:main`.

## Bottom line

Today's blockers were overwhelmingly **process**, not product: warm-state
misreads, deliberation instead of execution, poking healthy agents, unverified
blocker narratives, gating-model confusion, a provisioning gap read as a code
bug, and chatter read as ground truth. Six of seven now have a concrete rule; two
(toolchain preflight, gating-model + blocker-interrogation) are already landed or
wired. The structural fix for the gating confusion is `adopt-github-merge-queue`,
which this retro unblocks.
