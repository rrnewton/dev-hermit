---
name: validate-cpu-budget
description: "What to check BEFORE raising a validate CPU-time or step-timeout budget. Use whenever a validate/DAG run hits a time limit, whenever you are about to increase a timeout or cpu cap, and whenever validation gets slower. Enumerates the history/trend review, root-cause, and justified-vs-bloat classification the raise must be justified against — and how to tell whether the limit you are raising is enforcing anything at all."
---

# Raising a validate time budget

A time limit is the only thing standing between this repository and unbounded
test-time growth. Raising it is cheap, silent, and permanent, so it is the
default move under deadline pressure — which is exactly why it needs friction.

**This skill is that friction.** If you are here because a limit fired, work
the checklist in order. Do not raise anything until step 4.

The failure mode this exists to prevent is not one bad decision. It is fifty
individually-reasonable +10% raises that nobody ever reviews together.

---

## Step 0 — Is the limit you are about to raise actually enforcing anything?

Do this **first**, because it decides whether the rest of the checklist is even
about the right thing, and because an unenforced limit is worse than no limit:
it makes a budget look governed while nothing is governed.

Two distinct budgets exist, and they are not interchangeable:

| budget | unit | where |
| --- | --- | --- |
| `default_step_timeout` / per-step `timeout` | **wall** seconds | `hermit/ci/dag/*.json` |
| cgroup CPU cap | **CPU** seconds | `safe-ci-dag-runner` boxing |

Wall and CPU diverge under load. On a shared box a step can burn its wall
budget while using almost no CPU because it was waiting, and a runaway
`-j` can burn enormous CPU inside a comfortable wall budget. **Raising a wall
timeout to fix a CPU problem, or the reverse, treats the symptom.** Say which
one fired before you touch either.

Then check that the mechanism binds on the lane that matters:

```bash
# Does the DAG carry per-step CPU budgets at all, or only wall?
python3 - <<'PY'
import json
d = json.load(open("hermit/ci/dag/portable.json"))   # from the dev-hermit parent
steps = d["steps"]
print("steps:", len(steps),
      "| carrying cpu_timeout:", sum(1 for s in steps if "cpu_timeout" in s),
      "| default_step_timeout (WALL s):", d["default_step_timeout"])
PY
```

```bash
# Is the run boxed? An unboxed run enforces no cgroup cap, whatever the config says.
grep -n "allow-cgroup-failure" -B 6 hermit/ci/run-node.sh
```

`hermit/ci/run-node.sh` opts out of fail-closed boxing whenever `$GITHUB_ACTIONS` or
`$CI` is set, because the hosted portable lane runs in a throwaway VM that is
itself the containment boundary. **A cgroup CPU cap therefore binds on a local
developer run and does not bind in Actions.** If you are "raising the CPU
limit" to make CI pass, stop: on that lane the cap was not what failed.

> Verify the running thing, not the config. A flag that sets a cap and a cgroup
> that enforces one are different facts. For a live run, read
> `/sys/fs/cgroup/.../cpu.max` for the actual PID rather than trusting an exit
> code or a command line.

---

## Step 1 — Review the history and the **trend**, not the one run that failed

One run that blew a budget tells you almost nothing: it could be contention.
The trend tells you whether the budget is wrong or the tests are.

```bash
# Local validation history, exact-SHA records with durations
./ci-hub/ci-hub history --json | head -40        # parent; the DAG paths above are Hermit
./ci-hub/ci-hub newest-green --branch main --json

# Hosted history for the same lane
with-proxy gh run list --repo rrnewton/hermit --workflow <workflow> \
  --limit 40 --json headSha,conclusion,createdAt,updatedAt
```

Report a **trend with its denominator**, not a pair of numbers:

- how many runs you looked at, over what window;
- median and spread, not just the max — one slow run in forty is contention,
  forty slow runs is bloat;
- the **load average during each run** if the box is shared. A duration from a
  316-core box at load 85 is not comparable to one at load 5, and a "regression"
  that is really oversubscription will send you chasing a phantom.

**If there is no usable history, say so and stop.** A budget with no baseline is
a number someone made up. Check honestly:

```bash
./ci-hub/ci-hub newest-green --branch main --json | \
  python3 -c "import json,sys; d=json.load(sys.stdin)['report']; \
print({k:d[k] for k in ('branch_tip','commits_after_green','commits_with_records','commits_without_any_record')})"
```

If `commits_without_any_record` is most of `commits_after_green`, there is no
trend to read — fix the recording gap before setting a budget against it.

---

## Step 2 — Find the actual reason for the increase

Bisect the duration, not just the failure. The question is *which step* grew and
*why*, and the answer is almost always one of a small set:

| cause | tell | class |
| --- | --- | --- |
| a new test genuinely exercises new surface | one step grew; new test names in the run | possibly justified |
| a test got slower (bigger input, more iterations, added sleep) | same test name, larger duration | usually bloat |
| wrong parallelism (`-j`, `CARGO_BUILD_JOBS`, `--test-threads`) | CPU >> wall, or wall >> CPU | **config, not test** |
| cold cache / rebuild | build step grew, test steps flat | environment |
| contention on a shared box | everything grew proportionally; load average high | **not a regression** |
| a hang that the timeout is masking | duration pinned exactly at the limit | **never raise** |

That last row deserves its own sentence. **A step sitting exactly at its limit is
a hang until proven otherwise.** Raising the limit on a hang converts a fast red
into a slow red and buys nothing. Get a stack or a log tail first.

The parallelism row is the one most often mistaken for bloat: a step that is
`-j1` on a 316-core box, or `-j316` in a 4-core VM, will look like a test-time
regression and is a one-line config fix.

---

## Step 3 — Classify, in writing, before you decide

Exactly one of:

**JUSTIFIED.** The added time buys coverage of a critical system or pins a
critical regression, and the cost is proportionate. State what it covers, what
would go unnoticed without it, and why it cannot be made cheaper. A test that is
slow because it is thorough is worth paying for; a test that is slow because it
sleeps is not.

**UNJUSTIFIED — bloat.** General accretion, redundant coverage, an input that
grew without reason, or a sleep standing in for a synchronisation primitive.
**Fix the test. Do not raise the limit.**

**UNJUSTIFIED — configuration.** Wrong thread count, cold cache, missing
`--locked`, a lane running work that belongs elsewhere. Fix the config. This
class is common and cheap to fix, and raising a limit to cover it hides the bug
permanently.

**NOT A REGRESSION.** Contention or an environment artefact. Record the load
average and move on — changing the budget for this is fitting noise.

Before choosing "justified", check the cheaper options: can the test shrink its
input and keep its oracle? Can it move to an occasional/nightly lane instead of
the blocking one? Can it run once instead of per-backend? See the Hermit skill
`hermit/.llms/skills/test-shrink-optimization/SKILL.md` — reduce inputs and
iterations while preserving real syscall, scheduler and coverage surface. (It
lives in the Hermit checkout, not beside this file; a relative link from here
would be broken.)

---

## Step 4 — Only now, and only with the justification recorded

If and only if the classification is JUSTIFIED:

- Raise the **specific** step's budget, not the global default. A global raise
  gives every future regression free headroom.
- Raise it to the measured need plus a stated margin, not to a round number.
  Say what the margin is for (contention? cold cache?).
- Put the justification **next to the number**, in the DAG or the commit
  message: what grew, by how much, over what denominator, why it is worth it,
  and the run URLs or record SHAs you measured.
- State the new total. A per-step raise is also a raise of the DAG's worst case.

A raise whose justification is not written down is indistinguishable from
bloat six weeks later, when the person reading it is not you.

---

## What a good report looks like

> `test.regular_crates` hit its 600s wall timeout. Over the last 24 hosted runs
> (14 green, 10 red) its median rose 410s → 590s; the jump lands at `abc1234`,
> which added 3 e2e determinism cells. CPU/wall is 3.9x, so this is real work,
> not waiting; load average during the slow runs was 8–20, so it is not
> contention. Classification: JUSTIFIED — the new cells cover the KVM exit path
> that had none. Raising `test.regular_crates` only, 600s → 900s (measured 590s
> median + 50% margin for cold cache). DAG worst case moves 28,200s → 28,500s.

Note what that contains: a denominator, a median rather than a max, the
CPU/wall ratio that rules out waiting, the load average that rules out
contention, the exact commit, and a per-step rather than global raise.

## What a bad report looks like

> Timeout was too tight, bumped it to 1200.

---

## Measured starting state, 2026-08-06

Recorded so the first person to use this skill is not guessing, and so a later
reader can tell what has changed since:

- `hermit/ci/dag/portable.json`: **47 steps, 0 of 47 carry a `cpu_timeout`.** The only
  budget is `default_step_timeout = 600` **wall** seconds. There is no CPU-time
  cap on the outer validate DAG at that commit.
- `hermit/ci/run-node.sh` passes `--allow-cgroup-failure` whenever `$GITHUB_ACTIONS` or
  `$CI` is set, so the hosted portable lane runs **unboxed** and a cgroup CPU
  cap would not bind there.
- The validated frontier was **>24h old with 30-31 of the last 33 commits
  carrying no validation record**, i.e. no usable duration trend existed at that
  time. The figure moved between two reads minutes apart (31/33 then 30/33),
  which is itself the point: this is a live counter, not a constant.
  Re-measure with the Step 1 command before trusting any baseline.

These are conditions, not constants. Re-derive them; do not cite them.

---

## Where this file lives

The originating task specifies `hermit/.llms/skills/`, linked from dev-hermit.
It is in the **parent** for now because a Hermit commit of it was refused by
Hermit's repo-wide Reverie-pin lint — the pin was stale (`0ae0c01b` vs latest
`6144323c`) and that gate blocks every commit, including a documentation-only
one. Bumping 46 revision entries across 10 manifests to land a skill file would
have mixed an unrelated, revalidation-bearing change into a docs commit, so it
was not done. Move this to `hermit/.claude/skills/validate-cpu-budget/` (which
`hermit/.llms/skills` already symlinks) once that pin is current; the content
needs no change beyond making the `hermit/`-prefixed paths above repo-relative
again.
