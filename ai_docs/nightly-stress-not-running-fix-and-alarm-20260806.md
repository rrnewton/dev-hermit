# Nightly stress: why it stopped, the fix, and the missing-run alarm

**Task:** `nightly-stress-tests-not-actually-running` (P1)
**Date:** 2026-08-06 · **Author:** hermit-design
**Status:** watchdog implemented + bracketed locally (18/18); workflow authored, egress-gated for landing.
**Code:** `ci-hub/stress/check_freshness.py` · **Brackets:** `ci-hub/stress/test_check_freshness.py`
**Proposed workflow:** `ci-hub/stress/nightly-stress.workflow.yml` → `hermit/.github/workflows/nightly-stress.yml`

---

## 1. Why it wasn't running — the schedule is *gone*

Prior audits established the GitHub side: the only stress lane there is the weekly `super` job
(`hermit/.github/workflows/validation-levels.yml`, cron `23 05 * * 0`), which fired exactly twice and
was **CANCELLED at `timeout-minutes: 360`** both times — 6h00m00s each, zero verdicts ever produced.
A later correction found the real nightly was a **user crontab** (`30 4 * * *` → `ci-hub/stress/nightly.sh`),
not a workflow.

**Measured today, and this is the new fact:**

```
$ crontab -l
no crontab for newton
```

```
$ systemctl --user list-timers --all | grep -i stress    → (nothing)
$ ls /etc/cron.d/ | grep -i stress                       → (nothing)
```

**There is no schedule of any kind for the nightly stress lane.** The crontab that the 2026-08-03/04
notes record as installed and verified no longer exists.

And the log confirms it never became a habit:

```
$ cat ~/.local/state/nightly-stress.log        # 938 bytes, ONE run, ever
[2026-08-04T11:30:02Z] nightly stress at main HEAD 397fc846… (width=64 timeout=20s)
[2026-08-04T11:30:44Z]   wave 1/1: …,18,64,,,,,CALIB_UNDERPOWERED
ERROR  …vfork_parent_resumes_after_child_exec  passes=0 hangs=0 others=0
       (instances=0, bursts_ok=0, errors=1)
[2026-08-04T11:30:45Z] 🔴 P0 ALARM [ERROR] … tokens=CALIB_UNDERPOWERED
[2026-08-04T11:30:46Z] nightly stress complete — overall 🔴 RED/P0
```

So the owner's recollection — *"I think we've only actually done one"* — is now literally true of the
cron path as well: **one fire, and it measured nothing.**

### The root cause, stated as a property rather than an incident

Both failure modes are the same defect wearing different clothes:

| Path | How it failed | Why nothing noticed |
| --- | --- | --- |
| GitHub `super` (weekly) | cancelled at the 6h wall, twice | a cancelled job emits no red |
| user crontab (nightly) | the crontab itself disappeared | an absent schedule emits nothing at all |

**The schedule lived in unversioned, machine-local state, so it could vanish with no commit, no
review, and no alarm — and the alarm that existed could only fire on a *result*, so an absence of
results was silent.** Two nights passed unnoticed. That is the "silent-cancel" shape the task ties to:
the monitoring watched the wrong thing.

---

## 2. The fix — put the schedule in a tracked workflow, and split producer from watchdog

Full YAML: `ci-hub/stress/nightly-stress.workflow.yml`, destination
`hermit/.github/workflows/nightly-stress.yml`.

Two things it changes:

**(a) The schedule becomes a reviewable artifact.** `cron: "30 4 * * *"` in a tracked file cannot
disappear without a diff. This is the whole of the "make it actually run nightly" fix — the previous
mechanism was not *broken*, it was *unversioned*, which is why it could evaporate.

**(b) Producer and watchdog are separate jobs, and this is the load-bearing part.**

```yaml
jobs:
  produce:    # heavy calibrated burst; CAN be cancelled at the wall
  watchdog:   # cheap, minutes, `if: always()`, needs: produce
```

An alarm that lives inside the heavy job dies with the heavy job — which is exactly how two cancelled
`super` runs went unnoticed. The watchdog only reads the durable store, so it cannot be cancelled by
the workload it watches. A producer that is cancelled, hung, or never scheduled surfaces as a **red
watchdog**, not as silence.

Two smaller decisions worth naming:

* `concurrency: cancel-in-progress: false`. A later scheduled run must not cancel an in-flight one —
  a cancellation here would be indistinguishable from the very failure this lane exists to catch.
* `runs-on: [Linux, X64, hermit, pmu]`, self-hosted. The flake is **load-dependent** (~28%
  per-instance hang at 128-wide under fleet load); an idle GitHub-hosted runner measures ~0% and
  produces a **false green**. The calibrator (§4) is what turns that from an assumption into a
  checked precondition.

---

## 3. The alarm path — and the one rule that makes it hard to silence

`ci-hub/stress/check_freshness.py` is the missing-run alarm. It answers a question no existing
component asked: **is the lane still producing?**

```
FRESH     a measuring run inside the bound
STALE     the newest measuring run is older than the bound
NEVER     the store has no measuring run at all
NO_STORE  the store file does not exist
exit 0 = FRESH · exit 2 = alarm · exit 3 = error
```

### Freshness keys on MEASUREMENTS, not on runs

The obvious implementation — *"alarm if the newest store row is older than N hours"* — has a hole that
the 2026-08-04 run walks straight through. That run fired, recorded a row, and **measured nothing**:
`CALIB_UNDERPOWERED`, `instances=0, bursts_ok=0, errors=1`. A run-keyed check would count it as
freshness.

So a harness whose calibrator is permanently under-powered — *the very fault that makes it stop
measuring* — would keep the staleness alarm silent forever. **An alarm that the fault it watches for
can switch off is not an alarm.**

Freshness therefore keys on the newest row with `bursts_ok >= 1 AND total_instances >= 1`, and rows
that ran-but-measured-nothing are counted separately as `no_result_runs`. A lane that is firing
nightly and producing nothing reads **STALE with a distinct reason**, not fresh.

This is the same discipline as `executed_tests` in the validate ledger: a run that executed nothing is
a no-result wearing a success badge, and the record has to be able to say so.

### The threshold is a policy choice, and says so

`--cadence-hours` is the schedule's period (24). The bound is `cadence + grace`, with `--grace-hours`
defaulting to one cadence: **one fully missed cycle is tolerated, two alarms.** That is a judgement,
not a derivation, so the report prints `bound_basis` calling it a policy choice rather than presenting
it as if the data implied it.

### Live output, right now

```
$ python3 ci-hub/stress/check_freshness.py
🔴 nightly-stress freshness: STALE
   the lane is still FIRING (40.46h since the newest run) but has not MEASURED
   anything for 57.68h — 1 run(s) produced no result. A firing-but-not-measuring
   lane is stale, not fresh.
   rows=5 measuring=4 no-result=1 malformed=0
   newest measuring run: 2026-08-03T18:17:17Z (57.68h ago)
   newest run of any kind:  2026-08-04T11:30:44Z
exit 2
```

Note the two timestamps differing by 17 hours. That gap *is* the finding: a run-keyed check would have
reported 40h and stayed quiet under a 48h bound; the measurement-keyed check reports 57.68h and
alarms. The distinction is not theoretical — it is the difference between alarming and not, on the
real store, today.

### Brackets: 18/18, both sides of every gate

| Bracket | Result |
| --- | --- |
| recent measuring run → FRESH (alarm is not inert) | pass |
| run just inside the bound → FRESH | pass |
| **FLAKY but measuring → FRESH** — freshness is about producing a result, not about it being green; a red nightly is a *working* nightly | pass |
| past the bound → STALE, reason names "not firing" | pass |
| **recent no-result runs do NOT confer freshness** → STALE, reason names FIRING / not MEASURED | pass |
| only-ever no-result runs → NEVER | pass |
| **a run-keyed check would have called these fresh** — pins the distinction so the two notions cannot be collapsed later | pass |
| `bursts_ok` without instances / instances without `bursts_ok` → not a measurement | pass |
| empty store, timestamp-less rows, missing store file, malformed lines | pass |
| tighter grace moves the boundary; the report names the bound | pass |

---

## 4. What the alarm is watching (and the gap that remains)

The harness the workflow drives is the **calibrated** path — `matched-burst.sh`, which co-schedules a
known-flaky binary in every wave and discards waves where the calibrator comes back clean, on the
grounds that such a wave was too under-powered to have exposed anything. That mechanism is what makes
a green meaningful on a variably-loaded box, and it is why `CALIB_UNDERPOWERED` exists as a token at
all.

**The residual tension, stated rather than resolved:** the 2026-08-04 run shows the calibrated design
can produce zero valid waves and then raise `🔴 P0 ALARM [ERROR]` with the same urgency as a real
determinism failure. Both *are* alarm-worthy (a lane that cannot measure is a broken lane), but
collapsing "could not measure" into the same P0 as "determinism broke" is how alarm fatigue starts,
and fatigue is what makes the real FLAKY invisible. `check_freshness.py` keeps the two separate on the
freshness axis; **the verdict axis in `stress_store.py` still routes `ERROR` and `FLAKY` to the same
P0.** Splitting those is the natural follow-up and is deliberately not bundled here.

---

## 5. Coverage — worth re-stating, because it bounds what any of this can catch

The current stress workload is `tests_misc:vfork::vfork_parent_resumes_after_child_exec`, chosen
because it is a *confirmed* reproducer (~28% per-instance hang at 128-wide under load). That is the
right kind of workload — it exercises the reap/vfork path where flakiness actually bites.

It is also *one* workload. The scheduling/futex-contention and concurrent-process-tree paths named in
the original task's coverage assessment are still not in the nightly set. A green nightly therefore
means "this one known-racy path was deterministic under load last night" — which is worth having, and
is not the same as "hermit is not flaky". The workflow's `workflow_dispatch` inputs make widening the
set cheap; picking the additional workloads is a separate decision.

---

## 6. What must still happen (all egress-gated)

1. **Land `nightly-stress.yml`** into `hermit/.github/workflows/` — needs a slot and a PR.
2. **Confirm `DEV_HERMIT_PARENT` is set on the `[Linux, X64, hermit, pmu]` runners.** The workflow
   fails loudly (`:?`) if it is not, rather than silently running nothing — but it has not been
   verified on a runner.
3. **Decide the `super` lane's fate.** It has produced zero verdicts in two attempts and is
   mis-named (`run_super_suite` never calls `run_ci_manifest_lane`, so "super" ⊄ "full"). Either cut
   its scope to fit a wall it can actually meet, or retire it in favour of this lane. Once the
   watchdog lands, its cancellations at least stop being silent.
4. **Split `ERROR` from `FLAKY`** in the stress verdict taxonomy (§4).

---

## 7. Not established

* **No workflow was run**, and none could be — landing needs egress. The workflow YAML is authored and
  reviewed-by-reading only; it has never been executed by GitHub Actions.
* **No stress burst was run.** The live output in §3 is `check_freshness.py` reading the existing
  durable store, not a new measurement. Running a burst on this box is exactly the heavy concurrent
  load the current directives say to avoid.
* **The crontab's disappearance is observed, not explained.** `crontab -l` is empty and the log stops
  on 2026-08-04; *why* it went (box recycle, manual clear, never persisted across a reboot) is not
  established, and the fix deliberately does not depend on knowing — moving the schedule into version
  control makes the cause moot.
* **`check_freshness.py` is not yet wired to anything.** It is bracketed and runs correctly by hand;
  the workflow that would call it nightly has not landed, and no existing cron calls it.
