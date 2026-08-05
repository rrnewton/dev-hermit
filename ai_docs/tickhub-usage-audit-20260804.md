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

**Negative side over N real observed cycles (not just a fixture).** The positive control (`ALARM:
TIMER DEAD` firing on a dead timer) is the half people skip — proving it fires matters as much as
proving it stays quiet. Both directions are proven. The live `hermit-health-staleness.log` shows
`grep -c 'ALARM: TIMER DEAD' == 0`: across ~9 consecutive real staleness-timer cycles (00:35→01:51Z)
with the heartbeat advancing every 5 min, the TIMER-DEAD branch fired **zero** times — it does NOT
fire under normal operation. An alarm that never fires and one that always fires are equally useless;
this one is silent when healthy and loud when dead.

### UNKNOWN ≠ HEALTHY (fire text says what it could NOT verify)

The original design's defect was that a silent monitor read as healthy. The alarm text now makes the
distinction explicit so UNKNOWN and HEALTHY can never render the same:
- dead timer → `no tick observed in Nmin … tick-hub state is UNKNOWN, NOT healthy — this alarm
  verifies liveness only and just lost it; a stale heartbeat is not a clean bill of health.`
- missing log → `No tick has EVER been observed: tick-hub state is UNKNOWN, NOT healthy (absence of a
  tick is not proof of health — UNKNOWN and HEALTHY are different).`

### What this alarm CANNOT see (stated plainly, not left implied)

The alarm verifies two things by file mtime: the timer FIRED (heartbeat freshness = liveness) and a
tick did PRODUCTIVE work (fired-state freshness). It does **not** verify that a firing tick's internal
health checks actually ran and meant anything. **A tick that fires on schedule and returns rc=0 while
its checks silently no-op — accepted, running, verifying nothing (the `--cgroups` shape) — would
advance both signals and read here as HEALTHY.** Closing that gap requires the tick to emit a
positive, counted receipt ("polled main @SHA, N checks executed") and the alarm to assert on that
count, not merely on freshness. Until then this alarm proves the monitor is ALIVE, not that its work
is CORRECT. This blind spot is now documented in the script header, not left implied.

## Residuals (tracked elsewhere, not this audit)

- agent-utils pin drift freezes the *productive* fired-state (health-tick self-gates rc=1). Benign
  while the heartbeat advances; correctly classified WORK-GATED, not paged as dead. Owned by the
  dirty-checkout agent.
- Escalation push via `scripts/orc-hermit-msg.py` is CONFIRMED broken machine-wide (every relay —
  staleness alarm, perf-relay, alignment reminders — fails identically: "coordinator pane did not
  show the expected empty Orc input box" / "found 0 coordinators"). This is shared tmux-relay infra,
  not a tick-hub defect. The alarm's `page()` no longer paints over it: it now logs
  `alerted-coordinator (push OK)` ONLY on a successful push and `escalation-PUSH-FAILED …
  coordinator was NOT reached … read this log directly` otherwise — "pushed" and "tried-but-failed"
  no longer render the same. The **durable `staleness.log` is the reliable sink** (survives agent
  recycling); the push transport repair is a separate cross-cutting concern.

## Deliverables added on owner review (2026-08-04, co-coord opus-4.8)

Live script `~/.local/bin/hermit-health-staleness.sh` (backup `…​.bak-preunknown-1785895329`),
re-proven both ways after each change (`bash -n` clean; negative→silent, positive→fires; live
artifacts untouched, fired-state `1785772443`):
1. Negative side proven over N real cycles (0 TIMER-DEAD in 9 healthy cycles), not only a fixture.
2. Fire text now says what it could NOT verify — UNKNOWN ≠ HEALTHY (both TIMER-DEAD branches).
3. The blind spot (fires-but-silently-no-ops, the `--cgroups` shape) stated plainly in the header.
4. `page()` distinguishes push-succeeded from push-failed so the durable log is honest.

No product source changes; the alarm is machine-local runtime under `~/.local/bin` (its relocation
into version control is the separate `relocate-tick-hub-config` work). All testing non-destructive.
