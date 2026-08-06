# Hourly alignment-reminder relay: why it died, and the durable driver that replaces it

**Task:** `hourly_alignment_reminder_relay` · hermit-clone (opus-5), 2026-08-05/06
**Local, no egress.** Diagnosis is measured; the driver is installed, armed, and verified firing.

## 1. Diagnosis — a session-bound schedule, killed by its host

The task reported "169 ticks then zero, no surviving driver". I re-derived that and found the
cause, which the ticket did not have:

**The final tick failed, and it names the killer.**

```json
{"timestamp": "2026-08-05T03:00:01-07:00", "status": "failed",
 "error": "tmux socket does not exist: /run/user/212630/orc-tmux/tmux-212630/default",
 "message_file": ".../alignment_reminder_prompt.md", "target": "db=hermit:coordinator"}
```

Three measurements together identify the failure class:

| measurement | value |
|---|---|
| alignment_reminder records | 169 — **151 sent, 18 failed** |
| first failure | 2026-07-31T21:00 — **and the schedule kept running for 4 more days** |
| last successful send | 2026-08-04T22:00 |
| final record | 2026-08-05T03:00:01, failed on a missing tmux socket |
| current tmux socket ctime | **2026-08-05 19:13** — a *new* server, ~16 h later |

So: **failure alone never killed it** (it survived 18). What killed it is that the tmux server it
depended on went away, and when a fresh server appeared 16 hours later **the relay did not come
back**. Nothing outside that session owned the schedule. That is the same class as the
operational-health `wf.loop` death — session-runtime state is not a durable schedule — and it gets
the same fix that class already got.

Independently confirmed there was nothing left to restart: `crontab -l` → *no crontab for newton*;
no user unit references `orc-hermit-msg` or `alignment_reminder`.

**Why it hid for ~19 hours:** the delivery log records only *attempts*. A tick that never fires
writes nothing, so the log reads healthy right up to its last line. The outage was observable only
as an absence.

## 2. The fix

**`scripts/alignment-reminder-relay.sh`** (version-controlled in dev-hermit, not `~/.orc`) plus
**`scripts/systemd/alignment-reminder-relay.{service,timer}`**, installed to
`~/.config/systemd/user/`. Modelled on `hermit-health-tick`, which adopted this pattern after the
identical failure.

Three design decisions, each traceable to a measurement above:

1. **Heartbeat before anything that can fail.** The script appends `tick start` to
   `~/.local/state/alignment-reminder-relay-invocations.log` as its first action, so **"the timer
   fired" is observable independently of "the message was delivered."** Without this separation a
   silent stop reads as health again — the exact way this outage hid.
2. **A missing tmux socket is a SKIP, not a failure.** It is the routine state between coordinator
   sessions. Exiting nonzero would put the unit in a failed state for a self-healing condition.
3. **The script always exits 0.** The relay failed 18 times in its first life without that being a
   reason to stop scheduling; the unit must behave the same way.

`Persistent=true` gives the property the dead driver lacked: a tick missed during downtime runs on
resume, rather than the schedule simply ceasing to exist. `Linger=yes` is already set for this user,
so the timer survives logout and reboot.

## 3. Verification — it fires

**Driver, both directions, before install:**

```
no-socket  rc=0  tick end ... outcome=skipped reason=no-tmux-socket    <-- negative
real socket rc=0  tick end ... outcome=delivered                        <-- positive
```

**Through the systemd unit path** (proving the unit, not just the script):

```
invocation log: 0 -> 2 lines
systemctl show ... Result=success  ExecMainStatus=0
```

**Armed and durable:**

```
UnitFileState=enabled   ActiveState=active   Persistent=yes   AccuracyUSec=30s
NEXT Wed 2026-08-05 23:00:00 PDT (hourly on the hour, matching the original cadence)
Linger=yes
```

**The design proved itself within minutes.** The unit-path run *failed to deliver* —
`coordinator pane did not show the expected empty Orc input box after 6 attempts` — while the unit
still reported `Result=success`. That is decision 3 working on a real occurrence: a flaky delivery
does not take the schedule down. The two signals now read separately:

```
heartbeat (timer fired):  2 lines
delivery  (message sent): 171 alignment_reminder records
```

**Side effect to record honestly:** verification delivered two real alignment reminders to the
coordinator pane (one succeeded at 05:03:04Z, one failed at 05:03:38Z). That is the relay doing its
job, but it was triggered by testing, not by the schedule.

## 4. Decision: restore, not retire — and why the driver is in the parent, not agent-utils

The task offered restore-vs-retire. Restore is right: `alignment_reminder_prompt.md` is present and
current, the relay had a 4-day unbroken cadence, and the in-session `hourly-status-report` workflow
is not a substitute — it uses `orc.sendWakeup` and does no pane delivery, so it cannot carry this
content.

**On the requested home (`agent-utils/tick-hub`): I deliberately did not put it there, and the
reason is measured.** `agent-utils/` is currently **clean and exactly at the parent pin `570e786`**.
Writing into it would make it dirty, which:

- fails `make check-agent-utils-pin`, and
- re-breaks `ci-hub/bin/agent-tool`'s pin guard, which gates **`health-tick`** — the monitor that
  spent ~38 h silently gated on precisely this condition and has only just come clean.

The serialize + push + re-pin path that would resolve it **requires egress**, which is down. So
placing the driver in `agent-utils` today would trade one working monitor for another. The parent
repo satisfies the stated policy (*substantive logic lives version-controlled in dev-hermit, not
`~/.orc`*) — `scripts/` is the parent's documented home for coordination tooling, and
`scripts/orc-hermit-msg.py`, the thing this drives, already lives there.

**Follow-up when egress returns:** relocating to `agent-utils/tick-hub` is a file move plus the
serialize→push→fetch→re-pin sequence. Nothing in the driver depends on its path except the unit's
`ExecStart`.

## 5. Remaining gap, named rather than left implicit

**Nothing watches the new heartbeat.** I made the death *observable* (the invocation log) but not
*alarmed*. If this timer dies, the log goes flat and — absent a watcher — that is once again visible
only by someone noticing an absence.

The pattern to extend is `~/.local/bin/hermit-health-staleness.sh`, which already does exactly this
for the health-tick heartbeat: stat the log, alarm if age exceeds ~3× the interval (here: hourly ⇒
~3 h), with a cooldown. I did not modify it because it is a live, owner-reviewed artifact outside
version control and outside this task's scope; changing another agent's running monitor without
authorisation is the wrong trade. Recommended as the next task, with the threshold derived the same
way the existing one is (`3 × measured interval`, stated alongside the number).

Also untested by me: `Persistent=true` catch-up across an actual downtime window. The property is
declared and shown active (`Persistent=yes`); I did not manufacture a reboot to observe it.

## Files

`scripts/alignment-reminder-relay.sh` (new, +x) ·
`scripts/systemd/alignment-reminder-relay.service` (new) ·
`scripts/systemd/alignment-reminder-relay.timer` (new) — all uncommitted, egress down.
Installed copies live in `~/.config/systemd/user/`; the repo copies are the source of truth so a
rebuild is reproducible rather than archaeological.

## Reproduction

```
bash -n scripts/alignment-reminder-relay.sh
ALIGNMENT_RELAY_INVLOG=/tmp/i.log ALIGNMENT_RELAY_SOCKET=/tmp/nope.sock \
  scripts/alignment-reminder-relay.sh          # skip path, rc=0
systemctl --user list-timers alignment-reminder-relay.timer
tail ~/.local/state/alignment-reminder-relay-invocations.log
```
