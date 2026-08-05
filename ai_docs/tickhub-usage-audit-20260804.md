# tick-hub usage audit — why the operational-health tick did not catch a two-day-red main

Task: `tick-hub-usage-audit` (research). Durable closure artifact.
Author: co-coordinator, opus-4.8. Verified against the RUNNING things 2026-08-05 ~01:55Z (UTC;
2026-08-04 ~18:55 PDT). Companion runtime record: memory
`tickhub-schedule-never-registered-orc-stale-spec.md`.

## The question

Why did tick-hub — the consolidated operational-health heartbeat — stay silent through a ~38h /
two-day window in which Hermit `main` was red?

## Root cause (established from the runtime artifact, not the config)

The failure was **the monitor was never autonomously running**, compounded by **a self-referential
watchdog design** that could not have detected its own silence:

1. **Never registered as an autonomous schedule.** The operational-health workflow
   (`hermit-dev-operational-health-v1` → `ci-hub/bin/health-tick` → `ci-hub/health/tick-hub.yaml`)
   was ABSENT from every `~/.orc/sessions/*/workflows.db`. The only persisted spec was the stale
   reminder-only `hermit-dev-pr-health` (sleep → sendWakeup), which never ran `health-tick` and never
   polled `main`. ORC restores persisted `workflow_specs` per-session; the stale spec kept getting
   resurrected and the plugin's `orc.workflow(...)` + startup `killWorkflow(legacy)` did not override
   it in the live session. The `.tick-hub/fired-state` advances seen in the log were MANUAL dev-time
   `health-tick` runs, not a schedule. Diagnosis came from the fired-state **mtime** (frozen ~31–38h
   on a 5-min cadence), not from any config that said what *should* happen.

2. **Pin-guard second blocker.** Even if scheduled, `ci-hub/bin/agent-tool` exits 1 whenever the
   canonical `agent-utils/` checkout is dirty or ≠ parent pin — so the tick would never reach the
   reminders. That drift is another agent's in-flight work (Invariant 5; not ours to reconcile).

3. **Fragile schedule home.** The schedule lived only in ORC per-session `workflows.db`; a
   session/devserver death loses it and the successor resurrects the stale spec.

4. **Self-reference trap.** The original staleness detector watched only the tick's own productive
   output. A monitor that shares fate with the thing it monitors goes silent together — exactly how
   38h of silence read as health.

## The fix (LIVE and re-verified this session)

The schedule moved off fragile ORC-session state onto **systemd --user timers** (Linger=yes survives
reboot; Persistent=true catches up missed runs), and the watchdog was rebuilt as an INDEPENDENT
external alarm.

- `hermit-health-tick.timer` (OnUnitActiveSec=5min) → `~/.local/bin/hermit-health-tick.sh` — THE
  tick. `SuccessExitStatus=0 1` so the by-design pin-guard exit-1 is not a unit failure.
- `hermit-health-staleness.timer` (OnUnitActiveSec=10min) → `~/.local/bin/hermit-health-staleness.sh`
  — the independent watchdog.
- The landed workflow fix (hermit `9109dac`) makes the periodic health check actually POLL GitHub
  `main` and hard-warn — the pre-fix reminder-only path could never have seen a red main.

### Deliverable 1 — WHAT INVOKES THE ALARM (independent trigger, no shared fate)

The staleness alarm is woken by **`hermit-health-staleness.timer`**, a separate systemd --user timer
that re-arms off its OWN last activation, NOT off the tick service. The alarm script **contains zero
tick-hub code** — it only `stat()`s two files. A stopped tick-hub therefore does NOT stop the
watchdog: they do not share fate at the application layer. The ONLY common substrate is the systemd
--user manager itself. This is the explicit answer to the recursive "who watches the watcher": the
detector is decoupled from the detected.

### Deliverable 2 — APPEND-ONLY INVOCATION LOG (the true liveness signal)

`~/.local/state/hermit-health-tick-invocations.log` is appended on EVERY tick regardless of the
tick's rc. It is the "did the timer FIRE / is the monitor ALIVE" signal — distinct from
`.tick-hub/fired-state`, whose mtime bumps ONLY on a productive rc=0 tick. Watching fired-state alone
was the original mistake (it freezes for the legitimate pin-drift reason while the monitor is alive).

Live check this session: the log advances on the MEASURED 5-min cadence with zero manual triggers
(…01:26:49Z → 01:31:50 → 01:36:50 → 01:41:50 → 01:46:52 → 01:51:52Z), every line `rc=1 advanced=no`
at the by-design agent-utils pin gate (timer healthy, productive work correctly gated). Both timers
`active`+`enabled`; `LastTriggerUSec` advancing ~5 min.

### Deliverable 3 — PROVEN BOTH WAYS (an alarm that never fires and one that always fires are equally useless)

Bracketed via hermetic fixtures (env-overridable `HERMIT_HEALTH_*` paths; live artifacts UNTOUCHED —
`fired-state` mtime `1785772443` and cooldown stamp `1785886997` verified unchanged, so no genuine
stall was masked; escalation routed to an inert stub that authorizes nothing):

| Case | Heartbeat | fired-state | Classification | Escalated? |
|------|-----------|-------------|----------------|------------|
| A both fresh | 60s | 60s | `OK` | no (0) — does NOT always-fire |
| C heartbeat 16min stale | 960s | 60s | `ALARM: TIMER DEAD` (900s = 3×5-min) | yes (1) — the 38h-silence detector |
| D heartbeat missing | — | 60s | `ALARM: TIMER DEAD — log MISSING` | yes (1) |
| B fired-state 32h stale, known gate | 60s | 1920min | `WORK-GATED (agent-utils pin drift)` | daily nag, not loud page |
| E fired-state stale, unknown reason | 60s | 1920min | `ALARM: WORK-STALLED (unexpected)` | yes (1) |

Cooldown proven: rerunning case C immediately → `suppressed-by-cooldown`, 0 escalations (loud at most
once/hour). Threshold is DERIVED, stated with the number in script and message: **15 min = 3 × the
measured 5-min interval** (N=3 tolerates 2 consecutive missed ticks; the 3rd trips it).

## Residuals (tracked elsewhere, not this audit)

- agent-utils pin drift freezes the *productive* fired-state (health-tick self-gates rc=1). Benign
  while the heartbeat advances; correctly classified WORK-GATED, not paged as dead. Owned by the
  dirty-checkout agent.
- Escalation push via `scripts/orc-hermit-msg.py` (coordinator-pane detection) is unreliable; the
  alarm still logs durably to `staleness.log`. Tracked on sibling `tickhub-auto-invoke-ci-hub-health`.

No source changes in this audit; all fixture testing was non-destructive.
