# Speculative-land remediation architecture at `6cbc776b`

This note describes what commit
`6cbc776b4c770f7c97716ddc563c9a99f8cab7a9` actually implements. All
`file:line` references below are to that commit.

## Bottom line

The Python remediation code does **not** message `hermit-lander`. It writes an
event to a machine-local JSONL store and prints diagnostics
(`ci-hub/remediation/protocol.py:434-493`). A separate ORC plugin workflow polls
that state and, when it sees a remediation-required gate result, calls
`orc.sendWakeup` (`.orc/plugins/hermit-dev/index.ts:239-287`). Thus an active
wake exists only while that ORC workflow is running.

This path uses no `herdr` command, no `tmux send-keys`, and no `tg note`. It is
also not an agent-polled message queue. The only agent-notification operation in
the path is the ORC plugin's `orc.sendWakeup` call
(`.orc/plugins/hermit-dev/index.ts:261-279`). The Python process cannot perform
that ORC effect; it merely produces the state and command output that the ORC
workflow consumes.

`remediation.state=triggered` means "a remediation event was recorded," not
"an agent received or acknowledged a message." The record initially says
`dispatch.state=pending` (`ci-hub/remediation/protocol.py:464-485`), and the ORC
wake path does not update that field after `sendWakeup`; it only caches an alert
signature (`.orc/plugins/hermit-dev/index.ts:256-284`).

## Data and control flow

```text
landing agent
    |
    | land_and_arm.py run
    v
ignored/ci-hub/land-intents/<repo>-pr<N>.json   (write-ahead intent)
    |
    | bounded gh merge; observe merged SHA
    v
ignored/ci-hub/obligations.jsonl                (append-only snapshots)
    |                         |
    | spawn detached          | spawn detached
    v                         v
exact-SHA validate.sh         protocol.py watch --id ... --poll-seconds 15
                              |
                              | records verifier state / remediation trigger
                              v
                    ignored/ci-hub/obligations.jsonl
                              ^
                              |
ORC plugin workflow, every 15s|
    land_and_arm.py recover --observe-timeout 5
    ci-hub watch-obligations --once --gate
                              |
                              v
                    orc.listAgents()
                       /             \
          live hermit-lander       no live hermit-lander
                    |                 |
      orc.sendWakeup([name], ...)  orc.sendWakeup([], ...)
                    |                 |
              named agent          coordinator

Five-minute ORC health workflow --> watch-obligations --once --gate
                                  --> coordinator hard warning
```

## What runs and who launches it

There are two 15-second polling loops, not one daemon:

1. Arming an obligation spawns a per-obligation Python watcher. `arm()` first
   launches an exact-SHA local validation process, then launches
   `protocol.py watch --id <id> --poll-seconds <n>`
   (`ci-hub/remediation/protocol.py:820-933`). The default interval is 15
   seconds (`ci-hub/remediation/protocol.py:23-30`), and `watch()` polls the
   GitHub run and local PID, evaluates the obligation, then sleeps
   (`ci-hub/remediation/protocol.py:656-732`). `_spawn_detached()` uses
   `nohup setsid --fork --wait`, redirects output to an obligation log, closes
   file descriptors, and starts a new session
   (`ci-hub/remediation/protocol.py:496-516`). It therefore survives recycling
   the task agent that invoked the merge.

2. Independently, the ORC plugin registers the restartable workflow
   `hermit-dev-speculative-land-remediation-v1`
   (`.orc/plugins/hermit-dev/index.ts:44-53,373-380`). Its loop runs the
   registered script every 15 seconds. That script first recovers unarmed land
   intents, then performs one obligation poll and emits a gate result
   (`.orc/plugins/hermit-dev/index.ts:39-51,239-287,309-313`). This is the
   process that can call `orc.sendWakeup`; it is launched and owned by the ORC
   workflow engine, not by the landing agent.

There is no cron entry, systemd unit, or boot service in this mechanism. On a
box reboot, both detached Python processes and the live ORC workflow stop. If
the same filesystem and an ORC session return, plugin evaluation registers the
restartable workflow again (`.orc/plugins/hermit-dev/index.ts:290-313,373-390`),
and its `recover` command can reconstruct work from the on-disk intent files.
Nothing in this commit starts recovery before an ORC/plugin session exists.

The five-minute operational workflow is a second visibility path for an
already-created obligation: tick-hub runs `watch-obligations --once --gate`
and emits an action on failure (`ci-hub/health/tick-hub.yaml:1-18`), after which
the operational heartbeat calls `orc.sendWakeup([], ...)`
(`.orc/plugins/hermit-dev/index.ts:205-232`). It does not run
`land_and_arm.py recover`, so it cannot repair the narrower merged-but-unarmed
intent gap.

## Persistence and the meaning of "atomic"

`land_and_arm.py` writes one schema-version-1 intent file per repository/PR
under `ignored/ci-hub/land-intents/` by default
(`ci-hub/remediation/land_and_arm.py:32-53,150-167`). The write uses a temporary
file, `fsync`, and `os.replace` (`ci-hub/remediation/land_and_arm.py:41-49`). It
writes state `prepared` before starting the merge command, records
`merged-unarmed` after GitHub exposes the merge SHA, then calls the armer and
records `armed` (`ci-hub/remediation/land_and_arm.py:170-226`). The recovery
scan revisits every non-armed intent, asks GitHub whether the PR merged, and
arms the observed SHA (`ci-hub/remediation/land_and_arm.py:268-317`).

This is a write-ahead, crash-recovery protocol, not an atomic transaction with
GitHub. A process can die after merge but before arm; the durable intent makes
that gap recoverable when the ORC workflow next runs. It does not roll back a
merge, prevent a raw `gh pr merge` from bypassing the wrapper, or recover if the
machine-local files are lost (`ci-hub/remediation/land_and_arm.py:176-224,268-317`).

Obligations live in `ignored/ci-hub/obligations.jsonl` unless
`CI_HUB_OBLIGATIONS_STORE` overrides the path
(`ci-hub/history/obligations.py:48-50`). It is an append-only stream of full
schema-version-1 snapshots. Readers select the last event for each obligation;
writes take an exclusive file lock, flush, and `fsync`
(`ci-hub/history/obligations.py:22-26,60-93,119-124,222-258`). The opened schema
contains the exact SHA, verifier states, watcher PID, alert, and remediation
objects (`ci-hub/history/obligations.py:126-217`).

Both intent and obligation stores are below `ignored/`, which is gitignored
and explicitly machine-local (`.gitignore:21-30,118-118`). They normally
survive an agent recycle and an ordinary reboot of the same disk. They are not
version-controlled, replicated, or recoverable after workspace/disk loss.

## Wake behavior and coordinator fallback

On a failure, `evaluate_obligation()` records `overall_state` as
`remediation_required`, chooses revert when the failed land is still the main
tip or fix-forward after main advances, then records one idempotent remediation
trigger (`ci-hub/remediation/protocol.py:333-407,434-493`). The test proves only
that one `remediation-triggered` event is appended; it does not exercise ORC
message delivery (`ci-hub/remediation/tests/test_protocol.py:102-154`).

The ORC workflow interprets exit code 2 plus the text
`state=remediation-required` as actionable. It lists agents and considers a
lander alive when an entry is named exactly `hermit-lander` and its status is
not dead, failed, retired, or terminated. It then calls
`orc.sendWakeup(["hermit-lander"], ...)`. Otherwise it calls
`orc.sendWakeup([], ...)`, which is the concrete coordinator fallback used by
this plugin (`.orc/plugins/hermit-dev/index.ts:243-279`; the fallback contract
is stated in `.orc/plugins/hermit-dev/README.md:19-23`). No coordinator task is
created and no remediation is executed automatically.

If a fresh replacement named `hermit-lander` exists at poll time, it receives
the wake even though it has none of the prior agent's context: selection is by
name and coarse status only (`.orc/plugins/hermit-dev/index.ts:261-270`). The
wake body includes the gate report and a generic instruction to execute the
recorded action (`.orc/plugins/hermit-dev/index.ts:271-279`). The gate summary
contains the obligation ID, repository/SHA, verifier states, recommendation,
and remediation state, but not the stored dispatch instruction
(`ci-hub/remediation/protocol.py:735-801`). A fresh lander must query the
obligation store, for example through `ci-hub obligations`; the normal status
view prints the failure summary (`ci-hub/remediation/protocol.py:802-817`).

If the named lander is absent before the poll, the coordinator is woken. If a
lander receives the wake and is then recycled, delivery is **not** durable:
the ORC workflow caches the stable report signature and suppresses another wake
while that report is unchanged (`.orc/plugins/hermit-dev/index.ts:235-237,255-284`).
There is no acknowledgement transition from `dispatch.state=pending`, no agent
identity or lease recorded with delivery, and no retry-to-replacement logic in
this commit (`ci-hub/remediation/protocol.py:477-482`;
`.orc/plugins/hermit-dev/index.ts:271-281`). The obligation itself persists and
keeps `ci-hub health` nonzero until explicit resolution
(`ci-hub/ci-hub.rs:496-531,762-852`), so a human/coordinator can recover it, but
the active wake is best-effort and effectively once per stable alert signature.

## Correct characterization

The accurate description is:

> A write-ahead land intent and append-only, machine-local obligation log make
> merge-to-arm gaps recoverable while an ORC session is operating. Detached and
> ORC polling detect verifier failure. The ORC plugin then makes a best-effort
> wake to a currently live agent named `hermit-lander`, or to the coordinator
> when no such agent is listed. Wake delivery is not acknowledged or retried
> across a later agent recycle.

Calling this simply an "atomic transaction with durable recovery that actively
wakes hermit-lander" overstates two properties: GitHub merge plus local arm is
crash-recoverable rather than atomic, and the recorded remediation is durable
but the agent wake is not.
