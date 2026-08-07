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

**ONE BULLET PER AGENT, AND IT NAMES WHAT THAT AGENT IS DOING RIGHT NOW.**
The 2026-08-07T01:00 report listed 19 workstreams for 7 agents — `hermit-w3`
appeared seven times — and not one of the 19 was active work: 16 were tagged
`implemented` and 3 had not been started. It rendered an ownership ledger as an
activity report, and the owner could not tell whether `hermit-w3` was an agent
or a workstream. So, per live agent:

- **Exactly one bullet.** An agent is a worker, not a portfolio. Its one bullet
  is the single task it is working on this hour.
- **Derive activity from live TaskGraph, not from memory or from ownership.** A
  task tagged `implemented` is finished and waiting to land — it is a landing
  obligation, not activity, and it keeps status `in_progress` by policy, so
  status alone will mislead you. `open`/`backlog` means not started. Neither
  belongs in the active list.
- **Name the workstream, not the id.** Use a stable descriptive
  `major-goal/sub-goal` slug — `backend-parity/dbi-stack-hash-determinism`, not
  `dbi_detlog_stack_hashes` and never `phase-1` or `option-a`. The same work
  keeps the same slug next hour, so the owner can follow it across reports.
- **Say where.** Give the agent's slot or working directory, so a name resolves
  to a workspace.
- If an agent has no active task, say that plainly. "Idle, 2 PRs awaiting
  landing" is a real and useful status; seven stale bullets are not.

**3. Append the structured record with the EXACT text you delivered.**
Run `scripts/status-log.rs`. The `status_text` you log must be byte-identical to
what GChat actually received — take it from the send call's response, do not
retype or re-summarize it. The mapping mirrors the rules above and the script
enforces them: one entry per agent, `major-goal/sub-goal` slugs, a mandatory
`cwd`, and every task id dereferenced against live TaskGraph. It refuses rather
than records a mapping that disagrees, so a refusal means the report needs
fixing, not that the flag needs working around.

```
./scripts/status-log.rs \
  --mapping-json '{"<major-goal>/<sub-goal>":{"agent":"<agent>","task":"<task-id>","cwd":"<slot-or-cwd>"}}' \
  --awaiting-landing-json '{"<major-goal>/<sub-goal>":{"agent":"<agent>","task":"<implemented-task-id>","cwd":"<slot-or-cwd>"}}' \
  --status-file <file containing the exact delivered text> \
  --repos <owner/name>[,<owner/name>...] \
  --open-prs <N> --genuine-reds <N> --fleet-count <N> \
  [--ready-prs <N>]
```

`--repos` is REQUIRED and names the repository set every count below was taken
over — it is the denominator, and the script refuses without it. Name the repos
explicitly rather than relying on "the usual ones"; an unstated denominator is
what once put `open_prs=105` and `open_prs=10` in the log nineteen minutes
apart, one counting all open PRs and the other a ready subset.

`--awaiting-landing-json` is optional and is where implemented-but-unlanded work
goes: it stays on the record without being counted as activity.

Counts travel with their denominators: `--open-prs` is TOTAL open INCLUDING
DRAFTS across exactly the repositories you passed to `--repos`, and
`--genuine-reds` comes from `./ci-hub/ci-hub pr-status`, not from a glance at
GitHub. If you also report the ready/non-draft subset, it goes in `--ready-prs`;
it is a separate field and can no longer be written as `open_prs`.

Take `--open-prs` and `--repos` from the same source in the same breath. Note
that `ci-hub pr-status` polls TWO repositories (`rrnewton/hermit`,
`rrnewton/reverie`), so if you intend a wider set you must count the extra
repositories yourself rather than inheriting pr-status's total.

**4. Report the outcome back into the hour's record — by ATOMIC TYPED REWRITE,
never by appending.**
The driver has already written a per-hour claim file; its path is in the header.
That file is how a later investigator tells "the hour was handled" apart from
"the driver fired and the coordinator dropped it on the floor".

**It is JSON, and it is the dedupe authority. Do not append a line to it.**
`read_claim` parses it with `serde_json`, which rejects trailing content, so one
appended human-readable line makes the whole claim unparseable. The driver then
fails closed — `outcome=corrupt-claim` — and that hour can no longer be
processed until a human repairs the file by hand. (Before the fail-closed fix
landed, the same append was worse: the claim degraded to a synthetic
`pending@epoch0`, aged into `ReclaimStale`, and RE-SENT an hour that had already
gone out.)

Record the outcome in the claim's documented `detail` field, rewriting the whole
object atomically — write a sibling temp file, then rename over the original:

```python
import json, os
p = "<claim path from the header>"
d = json.load(open(p))                      # fails loudly if already corrupt
d["detail"] = "delivered"                   # or: "skipped: <reason>"
tmp = p + ".tmp"
open(tmp, "w").write(json.dumps(d) + "\n")
os.replace(tmp, p)                          # atomic
```

**Never change `state`.** It is the dedupe authority: `decide()` treats exactly
`"delivered"` as "this hour is closed forever". Any other value — including a
more descriptive one like `"gchat_delivered"` — makes the hour read as
undelivered, age out, and send a second owner status. Put richer delivery
evidence in ADDITIONAL fields (`delivery_state`, `gchat_message_name`,
`status_text_sha256`, …) and leave `state` alone.

Afterwards the claim must still parse and still dedupe. Confirm with a dry run,
which writes nothing:

```
./scripts/hourly-status-relay.rs --hour <this hour> --dry-run
```

It must print `outcome=duplicate-hour ... claimed=no-op`. If it prints
`outcome=corrupt-claim`, the rewrite was not valid JSON — fix the file rather
than leaving the hour wedged.
