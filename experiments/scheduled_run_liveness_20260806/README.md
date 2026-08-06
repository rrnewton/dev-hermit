# A cancelled scheduled run is silent — and an in-run detector cannot fix the general case

**Task:** `cancelled-scheduled-run-is-silent` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local analysis only, egress down (403), no `gh` calls.

## Standing on prior work, not redoing it

hermit-ghdag implemented the fix (PR #1634, head `49a357059`) and hermit-coord adversarially confirmed
it. I did not re-litigate that. I rebuilt the helper from the PR head myself
(`rustc --edition=2021 -D warnings`, clean) and re-ran its decision table to confirm it behaves as
claimed — it does. The GitHub run history (both weekly runs hitting the 6 h wall at exactly 6 h) is
**prior measurement, cited not re-derived**: egress is down, so I could not call `gh`.

This artifact answers the part that was left open: **what the point-fix covers, what it structurally
cannot, and what detects the rest.**

## Answer

PR #1634 fixes **one job in one workflow**, and by construction it can only ever fix that shape.

* **Coverage today: 1 of 4.** Four scheduled jobs can be silently cancelled; one is protected.
* **Four absence classes are unreachable from inside the run.** A detector that lives in the run
  cannot observe its own absence. Cancellation detection has to be out-of-band *by construction*.
* The helper is also a **second implementation** of a three-way outcome decision the repo already has
  a canonical table for. Measured drift: **3 of 10 states disagree** — all benign today, all latent.

## 1. Coverage matrix

Scheduled workflows in the fleet (`origin/main`): **2 in hermit, 0 in reverie.**

| workflow | cron | job | timeout | runner | cancel alert |
| --- | --- | --- | ---: | --- | --- |
| `validation-levels.yml` | `23 05 * * 0` (weekly) | `quick` | 180 | ubuntu-latest | **none** |
| | | `full` | 360 | `[Linux, X64, hermit, pmu]` | **none** |
| | | `super` | 720 (was 360) | `[Linux, X64, hermit, pmu]` | ✅ `super-cancel-alert` (#1634) |
| `runner-health.yml` | `7,37 * * * *` (every 30 min) | `health` | 10 | ubuntu-latest | **none** |

**1 of 4 protected.** The most uncomfortable gap is `full`: it sits in the *same workflow*, on the
*same scarce `pmu` runner*, behind a **360-minute wall** — the exact wall that was measured killing
`super` twice at exactly 6 h. If `full` hits it, that is silent today. And `runner-health` — the
every-30-minute heartbeat the fleet leans on — has no detector of its own cancellation at all: the
monitor is unmonitored.

## 2. What an in-run detector can never reach

`super-cancel-alert` is `needs: super` + `if: always()`. That catches a **per-job** kill. It cannot
catch:

| class | why the alert job does not fire |
| --- | --- |
| **C1 whole-run cancel** | `concurrency: cancel-in-progress: true` is set at workflow level; a manual or API cancel does the same. The alert job is cancelled along with the run. *(Disclosed in the PR.)* |
| **C2 run never created** | The cron did not fire, or GitHub auto-disabled the schedule after 60 days of repo inactivity. No run → no job → no alert. |
| **C3 run never started** | Queued forever behind the scarce `pmu` runner. Prior measurement: the 2026-07-26 run queued **~6.5 h** before it even began. While queued there is no job result to key on. |
| **C4 schedule removed** | Workflow file edited, renamed, or deleted. |

All four are **absence**. This is the same shape as the finding itself, one level up: the fix converts
"a cancelled job is silent" into "a cancelled job is loud", but *"no job at all"* stays silent — and
that is strictly the larger hole, because C2 and C4 are permanent once they happen.

> **A detector inside the thing it monitors cannot observe its own absence.** That is why the design
> below is out-of-band and asserts a *positive* ("a terminal run finished within its own period")
> rather than watching for a *negative* ("something reported cancelled").

## 3. Classifier drift (measured, latent, benign today)

The repo already has a canonical three-way outcome table: `agent-utils/py/ci_hub_check_outcome.py`
(`scripts/classify-required-check.sh` is a thin `exec` wrapper around it, not a second engine).
`super_cancel_alert.rs` restates that decision inline in Rust. I ran both over 10 conclusion values:

| conclusion | canonical | helper | agree? |
| --- | --- | --- | --- |
| success | PASSED | silent | yes |
| failure | FAILED | silent | yes |
| **cancelled** | NO_RESULT | **ALERT** | yes ← the defect this task is about |
| skipped | NO_RESULT | silent | **no** |
| timed_out | FAILED | ALERT | **no** |
| startup_failure | FAILED | ALERT | **no** |
| neutral / action_required / stale / *empty* | NO_RESULT | ALERT | yes |

**7/10 agree.** The three divergences are latent, not live, and I want to be precise about why rather
than inflate them: `needs.<job>.result` can only ever be `success`, `failure`, `cancelled`, or
`skipped`, so **`timed_out` and `startup_failure` are unreachable through this wiring**, and `skipped`
is separately gated off by the job's `if:`. So the helper is behaviourally correct where it is wired.
The cost is future drift with no parity test — the same "one verifier per authority" pressure the
shared-predicate work exists to relieve. **Carry-forward, not a blocker.**

## 4. Design: out-of-band liveness

`scheduled-run-liveness` (prototype here). For each scheduled workflow it asserts a positive:

> **a *terminal* scheduled run of W finished within W's own period × grace** — and the *most recent*
> scheduled run is not itself a no-result.

Three properties that matter:

1. **The window is derived from the workflow's own cron**, not hardcoded — the value carries the
   condition it was computed under. Verified against the real files:
   `runner-health.yml` → 1 h period → 2 h window; `validation-levels.yml` → 168 h → 336 h window;
   `ci-portable.yml` → correctly reports *not scheduled*.
2. **Classification is delegated** to the canonical `ci_hub_check_outcome.py` when reachable (with a
   declared fallback that is reported in the output as `classifier: fallback`), so this tool does not
   become a *third* copy of the same table.
3. **Both conditions are required.** Freshness of *a* success is not enough — see case J below.

### Mutation matrix (10 planted cases, both sides bracketed)

| case | fixture | verdict |
| --- | --- | --- |
| A | terminal success 4 days ago, 336 h window | **OK** (silent) |
| B | single run concluded `cancelled` | ALERT `NO-TERMINAL-RUN` |
| C | no scheduled runs at all | ALERT `NEVER-RAN` — **C2** |
| D | last terminal success 1575 h ago vs 336 h window | ALERT `STALE` — **C2/C4** |
| E | newest scheduled run `status=queued` | ALERT `NO-TERMINAL-RUN` — **C3** |
| F | terminal **failure** inside the window | **OK** (silent) — correct: a failure is already loud |
| G | hourly, terminal success 23 min ago vs 2 h window | **OK** (silent) |
| H | hourly, last terminal success 24.4 h ago | ALERT `STALE` |
| I | fresh `workflow_dispatch` success, no schedule run | ALERT `NEVER-RAN` |
| J | success inside the window **plus two newer cancelled runs** | ALERT `LAST-RUN-NO-RESULT` |

**J is the case that justifies the second condition, and it is the real observed shape** — the weekly
`super` history is exactly "an older success, then cancels". A naive detector asking *"was there a
success in the window?"* returns green on J. **I is the twin:** a detector asking *"was there any
recent run?"* returns green when the schedule has stopped firing and only manual dispatches remain.
Both are the proxy trap this whole task is about, so both are planted.

Negative controls (A, F, G) confirm the detector is silent on healthy input — it is not a thing that
always alerts.

### Where it runs, and why not in a workflow

**Not** in GitHub Actions. A monitor that shares a failure domain with the monitored thing inherits
C1–C4: a monitoring workflow can itself be cancelled, auto-disabled, or queue-starved, and then its
silence is once again indistinguishable from health. The right home is the **coordinator's hourly
status rollup** (`scripts/status-log.rs`, per the operating model) — it is outside GitHub entirely, it
already runs hourly, and hourly is well inside both windows (2 h and 336 h). Cost: one `gh run list`
per scheduled workflow — two calls — plus a sub-second local evaluation.

## 5. Recommendations

1. **Generalise the alert instead of adding a second bespoke job.** `full`, `quick` and `health` need
   the same treatment `super` got. A reusable composite action (or a matrix job keyed on
   `needs.*.result`) applied to every scheduled job keeps the next scheduled job from re-opening the
   hole by default. As it stands, protection is opt-in per job, which is how it got to 1 of 4.
2. **Add the out-of-band liveness check** — it is the only layer that covers C1–C4, and C2/C4 are
   permanent-once-hit.
3. **Have the helper call the canonical classifier** rather than restating it, or add a parity test
   pinning the two tables together. Today's divergence is inert; that is a reason to fix it cheaply
   now, not a reason to leave it.
4. **Keep the 720-minute timeout provisional and measure the real duration.** `super` has never
   completed, so its true runtime is known only to be **> 6 h**. Do not trim `SUPER_REPETITIONS` to
   make it fit before one completion has been measured — that would weaken the L4 20× stress
   guarantee to satisfy a budget nobody has yet established.

## Limitations

* The detector has only been exercised against **synthetic fixtures**; it has never consumed real
  `gh run list --json` output. That integration is unverified and needs egress.
* Run-history facts (the two 6 h walls, the 6.5 h queue) are **prior measurements by hermit-ghdag**,
  cited here, not independently re-measured.
* `cron_period_hours` is a coarse parser (weekly / daily / hourly). It deliberately **over**-estimates
  the period, which makes the detector quieter rather than falsely loud; a `*/15` style cron would be
  treated as hourly.

## Reproduction

```bash
cd experiments/scheduled_run_liveness_20260806
./scheduled-run-liveness --runs fixtures.json --root /home/newton/work/dev-hermit   # exit 1, 7 alerts
```

## Files

| file | what |
| --- | --- |
| `scheduled-run-liveness` | the out-of-band detector prototype, with its rationale inline |
| `fixtures.json` | the 10 planted cases (A–J) including the three negative controls |
| `results.csv` | every measurement and mutation with its observation and verdict |
| `metadata.json` | SHAs, toolchain, egress state, and the stated limitation |
