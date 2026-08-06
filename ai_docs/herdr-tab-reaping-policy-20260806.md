# herdr-run tab reaping: a staleness definition grounded in positive evidence

**Task:** `herdr-tab-reaping-policy` · `egress-probe2` (opus-5), 2026-08-06
**Status: DESIGN ONLY. Not implemented.** No code written, no agent-utils serialize slot taken.

## The mechanism, read from the running system

`agent-utils/.herdr-run/runs/<UTC-timestamp>-<agent>-<pid>/` holds `command`, `exit_code`,
`stdout`, `stderr`, `meta.json`. `meta.json` carries `{agent, argv, prefix, duration_seconds,
exit_code, from_cache, pane_id}` — e.g. `pane_id: "wE:p3"`.

**This changes the problem.** A previous finding of mine records that herdr exposes no tab
timestamps and that a tab's revision counter is not an activity signal. That remains true *of the
tab*, and it is why absence-of-output detectors keep failing. But the **run directory** carries
three things the tab does not: an owning **PID**, a recorded **exit_code**, and a **pane_id**
linking run to tab. Staleness can therefore be decided from evidence of **completion** rather than
evidence of **silence**.

## STALE — all three required, positive evidence only

* **S1** every run naming this `pane_id` has a recorded `exit_code` (it finished and wrote a result)
* **S2** the owning PID is not alive
* **S3** PID reuse is excluded. `kill(pid,0)` is **not** sufficient: PIDs recycle on a box running
  ~20 agents, so a recycled PID makes an unrelated live process look like proof of liveness. Bind
  identity as `ci-hub/lib/validate_lock.rs` already does for lock owners — `owner_pid` +
  `owner_boot_id` + `owner_start_ticks` (field 22 of `/proc/<pid>/stat`). That is the in-repo
  precedent for this exact problem; reuse it rather than re-deriving it.

**NOT stale, explicitly:** a pane whose run has **no** `exit_code` is IN FLIGHT. That is the "agent
is thinking" case. It is what defeated five detectors today: a *finished* agent and a *dead* agent
are identical on the "no activity" axis, and distinguishable on the `exit_code` axis.

## Verification — both ways, with counts

**Must NOT reap**, three cases, the confusable ones rather than an obviously-live tab:
(a) no `exit_code`, PID alive — working; (b) no `exit_code`, PID alive, no output for minutes —
**thinking**, the case that fools absence-detectors; (c) PID matches but `start_ticks`/`boot_id`
differ — recycled, treat as unknown.

**Must reap:** every run for the pane recorded an `exit_code`, PID gone, identity confirmed.

Report counts on both sides. A run that reaps 0 because nothing was stale and a run that reaps 0
because the detector is inert are indistinguishable without a planted positive case.

## Fail-safe direction and rollout

On any ambiguity — unreadable `/proc`, missing `meta.json`, absent `pane_id`, `boot_id` mismatch —
**do not reap**, and report what was declined and why, so "reaping nothing" is visible rather than
silent. The cost asymmetry is the reason: killing an active agent mid-work is far worse than
clutter, and a reaper that kills everything is disabled within a day.

Ship in **report-only** mode first (list what *would* be reaped, close nothing), checked against a
known-good population for at least one cycle before it may close anything.

## For the implementer

Route: agent-utils **serialize + re-pin**, not straight to main. Take the serialize slot *first*.
Open question before treating `pane_id` as evidence of a real tab: `meta.json` carries
`from_cache`, so some runs are cache hits that may never have allocated a pane.
