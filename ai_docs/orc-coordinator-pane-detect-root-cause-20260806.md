# "pane-detect finds 0 coordinators" — root cause and fix

**Task:** `orc_hermit_msg_push`
**Date:** 2026-08-06 (measurements 2026-08-05T21:49–21:56 PDT)
**Agent:** `egress-probe2` (opus-5)
**Fix commit:** `4b5d42882004e6b7e724e86c38d74c9467b32140` —
`scripts/orc-hermit-msg.py` (+185/−34) and `scripts/tests/test_orc_hermit_msg.py` (+219, new).
Local only; parent `main` is 48 ahead of `origin/main` and egress is 403, so
nothing is pushed. See §7 for a provenance note about who created that commit.

---

## 1. Verdict: the titled premise is refuted

**Pane detection is not broken, and it is not an ongoing outage.** Running the
shipped code read-only against the live socket at 2026-08-05T21:49 PDT:

```
resolved socket: /run/user/212630/orc-tmux/tmux-212630/default
orc tmux ls    -> ['orc-hermit']  (2.2 s)
find_coordinator_pane -> CoordinatorPane(session='orc-hermit', window='orc', pane_id='%0')
```

Exactly one coordinator, first try, no fallback.

`found 0` happened **once, ever**. Across the 326 parseable records in
`~/.local/state/orc-hermit-msg.log` (239 `sent` / 87 `failed`):

| count | failure class | first .. last |
| ---: | --- | --- |
| 78 | composer-verify | 2026-08-04T12:00 .. 2026-08-05T00:31 |
| 3 | `orc tmux ls` timeout | 2026-07-31T21:00 .. 2026-08-02T00:00 |
| 2 | tmux server down | 2026-08-05T01:00 .. 2026-08-05T02:00 |
| 2 | tmux socket missing | 2026-08-05T03:00 .. 2026-08-05T17:36 |
| **1** | **found 0 coordinators** | **2026-08-04T18:58:10 only** |

The composer-verify block splits cleanly at the Orc rebuild: 49 × missing
`'Input (Enter'` (08-02T11:00 .. 08-04T19:00), then 30 × missing
`'Type / for commands'` (08-04T19:38 .. 08-05T00:31). The 08-05 01:00→17:36
gap is simply the tmux server being down; the socket was recreated at 19:13
(socket mtime), and the three deliveries after it (20:06, 20:31, 21:24) all
succeeded.

## 2. Root cause of the one `found 0`: a window hint aimed at an agent pane

The record, verbatim:

```json
{"error": "expected exactly one live Orc coordinator for database 'hermit'; found 0 (coordinators: none)",
 "message_file": "/tmp/perf-relay.txt",
 "target": "db=hermit:hermit-perf",
 "status": "failed",
 "timestamp": "2026-08-04T18:58:10-07:00"}
```

`target` is `f"{session}:{window}"` where `window = args.window or "coordinator"`.
It reads `hermit-perf`, so **the caller passed `--window hermit-perf`** — an
*agent* window, while trying to relay a message to the `hermit-perf` agent.

`find_coordinator_pane()` applied the hints *before* the "is this pane running
`orc`?" test:

```python
if window_hint is not None and window != window_hint:
    continue
if Path(current_command).name != "orc":
    continue
```

On this fleet every agent window runs `claude` or `codex`; only the window
literally named `orc` runs `orc`. The two conditions therefore intersect to the
empty set for any agent window. Worse, the `coordinators:` list in the error
message was the **post-filter** list, so it structurally could never show what
the hint excluded — it always printed `none`.

The message was read as "the coordinator has vanished". It actually meant "your
window filter excluded the coordinator." That misreading is what produced this
task.

**Reproduced live, byte-identical to the 08-04 log record**, against the
unmodified code:

```
window_hint=None            -> OK   %0
window_hint='orc'           -> OK   %0
window_hint='hermit-perf'   -> FAIL expected exactly one live Orc coordinator for
                                     database 'hermit'; found 0 (coordinators: none)
window_hint='egress-probe2' -> FAIL  (identical string)
window_hint='hermit-coord'  -> FAIL  (identical string)
```

This is a Proxy Binding failure: the reported count (`found 0`) did not carry
the conditions that produced it (the hints applied, and the panes that were
actually live).

## 3. The fix

`scripts/orc-hermit-msg.py`:

* New `LivePane` dataclass and `parse_live_panes()` — parse **all** live panes
  in the active sessions first, with no coordinator filtering, so a failure can
  report what was really on the socket.
* `find_coordinator_pane()` now narrows in explicit stages
  (`live_panes` → `orc_panes` → `hinted` → `matching`) and raises a **different**
  error per stage, because each one is a different operator action:

  | condition | message |
  | --- | --- |
  | no pane is running `orc` | `no live Orc coordinator pane on <socket>: no pane in [...] is running \`orc\`. Live panes: ... The coordinator is probably restarting; retry shortly` |
  | hints excluded the coordinator | names the hints, lists the reachable coordinators, and — when the hinted window is a live agent pane — names it and its command, then says what to do instead |
  | wrong `--orc-db` | `no live Orc coordinator is running database 'X'; candidates: ... db=hermit` |
  | more than one | `found N: ... Disambiguate with --session/--window` |

* `--session` / `--window` are no longer `argparse.SUPPRESS`. Their help text
  now states they select among panes already running `orc`, and that `--window`
  is *not* how to message another agent.

Live output after the fix, for the exact misuse that caused the incident:

```
window_hint='hermit-coord' -> FAIL session/window hints excluded every coordinator
  (session_hint=None, window_hint='hermit-coord'); live coordinators on this socket:
  orc-hermit:orc (%0, orc); window 'hermit-coord' is orc-hermit:hermit-coord (%1, codex),
  an agent pane rather than the coordinator. This script only messages the Orc
  coordinator TUI; drop --window (or pass the coordinator's window) and ask the
  coordinator to relay, or use a TaskGraph note for agent-to-agent handoff
```

and, when the hinted window does not exist at all (the 08-04 case today, since
`hermit-perf` has since exited): `... ; no live pane has window 'hermit-perf'`.

## 4. Tests

`scripts/tests/test_orc_hermit_msg.py`, 20 tests, all passing. They drive
`find_coordinator_pane()` against a fake `run_tmux` reproducing the real socket
shape (one `orc` coordinator pane plus agent windows running `claude`/`codex`).

Both sides are bracketed:

* **Positive** — the coordinator is selected with no hint, with `--window orc`,
  with `--session orc-hermit`, and when the start command carries no `--db`
  (session-name fallback).
* **Negative, hint misuse** — an agent window hint must name the window and its
  command, must still show the reachable coordinator, and **must not** emit
  `coordinators: none` or `no live Orc coordinator pane`.
* **Negative, genuine absence** — no `orc` pane, a dead `orc` pane, and a pane
  outside the active sessions each yield the "coordinator probably restarting"
  message, which must stay distinguishable from the hint-miss message.
* Database mismatch, two-coordinator ambiguity, an out-of-session `--session`,
  start-command `--db` parsing (both `--db X` and `--db=X`, plus unparseable
  input), and malformed `list-panes` lines.

**Mutation bracket.** Restoring the old single-message behaviour (count without
conditions) and rerunning: **9 of 20 tests fail**, each on the assertion that
distinguishes the conditions. Restoring the fix: 20/20 pass. The tests are not
inert.

## 5. End-to-end verification

**Relay reaches the coordinator — yes, verified.** A live self-test at
2026-08-05T21:54:07 PDT:

```
$ ./scripts/orc-hermit-msg.py "SELF-TEST from egress-probe2 ..."
orc-hermit-msg: sent to orc-hermit:orc (%0)
```

Not just the exit code and the `{"status": "sent", "pane": "%0", ...}` log
record — `tmux capture-pane -t %0` shows the message text present in the
coordinator's pane. Delivery is bound to observed arrival, not to a return
value.

Incidental observation: the composer border currently reads
`┌ Input (Enter, Esc, Ctrl+G pause, Ctrl+T inbox, Ctrl+C clea…` again — the
`'Input (Enter'` title that the Aug-2 build had replaced with "Paste not
available here" is **back**. So that title flips between Orc builds and is not a
sound readiness signal either way; the current check keys on the stable
`"Type / for commands"` empty-input placeholder, which is correct, and the
typed-input delivery path works regardless of whether bracketed paste is
enabled. No change needed — but do not re-add a title-based check.

## 6. Separate defect found: the hourly cron relay is not firing at all

The task also asked to verify a **cron tick** reaches the coordinator. It does
not, and the reason is unrelated to pane detection.

The hourly alignment-reminder relay
(`--message-file /home/newton/work/dev-hermit/alignment_reminder_prompt.md`) ran
**169 times, hourly on the hour, from 2026-07-29T03:00 to 2026-08-05T03:00**.
Since 03:00 it has produced **zero** records — about 19 missed hours — while
manual relays at 20:06, 20:31, 21:24 and 21:54 in that same window all
succeeded. So this is not a delivery failure; the driver has stopped firing.

No surviving driver could be found:

* `crontab -l` → `no crontab for newton`
* no user systemd unit references `orc-hermit-msg`
  (`hermit-health-tick.timer` is alive but polls health; it does not relay)
* no Orc workflow spec in the live session's `workflows.db` references
  `orc-hermit-msg` or `alignment_reminder`

The only surviving hourly path is the in-session `hourly-status-report`
workflow, which uses `orc.sendWakeup(...)` and does no pane lookup — the
workaround the task description already names.

This is an **absence**: nothing logs a tick that never fires, so the silence is
indistinguishable from health from inside the relay log. It needs its own task —
either restore a durable driver (a systemd `--user` timer, like
`hermit-health-tick` was made after the same class of failure) or record that
the alignment reminder was intentionally retired in favour of the self-wake.

## 7. Provenance note on the commit

I did not run `git commit`. At 21:54:46 PDT — while the live self-test relay was
running — an automated parent committer created
`4b5d428 "Diagnose Orc coordinator pane mismatches"` containing exactly my two
files and nothing else (`git show --stat` confirms: 2 files, +370/−34). Other
commits from the same committer appear around it (`39b41bf`, `ce64c7a`,
`13c791e`, `1c13b78`). The committed content is byte-identical to the verified
working tree (`git status --short scripts/` is empty; tests and live detection
both re-verified at that HEAD). Flagging it because the commit action was not
mine, and because anyone auditing this repo's history should know an
auto-committer is active on the shared parent.

## 8. Residue

1. Nothing is pushed — egress is 403 on CONNECT and parent `main` is 48 ahead of
   `origin/main`.
2. The missing hourly cron driver (§6) is unfixed and needs its own task.
3. There is still **no supported way to relay to an agent pane**. `--window` now
   says so explicitly and points at TaskGraph notes, but if agent-to-agent tmux
   delivery is actually wanted, it needs a separate tool — the coordinator relay
   should not grow that mode, since its composer-readiness and `orc`-command
   checks are coordinator-specific.
4. 15 of the 341 lines in `orc-hermit-msg.log` are not JSON (e.g. `stub`, and
   raw `orc-hermit-msg: error: ...` stderr text). Some caller redirects stderr
   into the JSONL log. Harmless to `log_delivery`, but any consumer must skip
   unparseable lines — as the analysis in §1 does.
