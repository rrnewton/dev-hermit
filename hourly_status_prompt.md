<!--
Wake prompt for the durable hourly owner-status driver.

Delivered by scripts/hourly-status-relay.rs, which is fired by
hermit-hourly-status.timer (systemd --user). The driver prepends a machine-
generated header naming the exact scheduled hour and the paths involved, then
appends this file verbatim. HTML comments are stripped by
scripts/orc-hermit-msg.py before delivery, so this block is not sent.

Edit this file to change WHAT the coordinator is asked to do each hour. Do not
put scheduling logic here -- the driver owns the schedule and the per-hour
deduplication.
-->

HOURLY OWNER STATUS IS DUE.

Do all four steps, in order. Steps 3 and 4 are the ones that have silently
failed before, so do not stop after sending.

**1. Check the hour first — do not double-send.**
The header above names the SCHEDULED HOUR this wake belongs to. If a status for
that same hour has already gone out by any other path (notably the legacy
in-session `hourly-status-report` workflow, which fires on its own offset and
which this driver is meant to replace), do NOT send a second one. Skip to step 4
and record the skip. One status per scheduled hour, whoever sent it.

**2. Synthesize one status and send it to the owner GChat space.**
Space `spaces/AAQAA6Irlwg`. Cover: fleet size and per-agent activity;
egress/outage state; what actually tightened this hour; and blockers needing
owner input. Synthesize — do not dump a task list. Lead with the observable
consequence and the decision it creates. If nothing material changed, send a
one-line steady-state note rather than skipping: a skipped hour is
indistinguishable from a dead driver, which is the failure this whole mechanism
exists to prevent.

**3. Append the structured record with the EXACT text you delivered.**
Run `scripts/status-log.rs`. The `status_text` you log must be byte-identical to
what GChat actually received — take it from the send call's response, do not
retype or re-summarize it. Supply the real workstream→worker mapping, with task
ids verified against the live TaskGraph rather than remembered:

```
./scripts/status-log.rs \
  --mapping-json '{"<workstream-slug>":{"agent":"<agent>","task":"<task-id>"}}' \
  --status-file <file containing the exact delivered text> \
  --open-prs <N> --genuine-reds <N> --fleet-count <N>
```

Counts travel with their denominators: `--open-prs` is TOTAL open including
drafts across all three repos, and `--genuine-reds` comes from
`./ci-hub/ci-hub pr-status`, not from a glance at GitHub.

**4. Report the outcome back into the hour's record.**
The driver has already written a per-hour claim file; its path is in the header.
Append one line to it saying what happened this hour — `delivered`, or `skipped`
with the reason (already sent, nothing to report, GChat unreachable). That file
is how a later investigator tells "the hour was handled" apart from "the driver
fired and the coordinator dropped it on the floor".
