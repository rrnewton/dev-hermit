# Health Poller Dry-Run

`scripts/health-poll.py` is a read-only prototype for state-derived coordinator
health messages. This phase installs no cron entry, registers no ORC workflow,
sends no terminal input, and writes no cadence state.

## Reference Model

The design follows DeepScry's `multiagent_workspace/ops/poll.py` at commit
`1fff88fd1d7809809c1830f9a619dd299713fdf6` and the `tick_hub` primitives from
its pinned `rrnewton/agent-utils` commit
`ea4911a6e70b6df0a6ec3817b50dced4b9a7d4e3`.

DeepScry has one cheap outer tick and a registry of independently paced health
responsibilities. `tick_hub.is_due` compares each responsibility's last-fired
epoch with its cadence; a missing key is immediately due and cadence zero means
every tick. Due handlers emit line-oriented `HEALTH`/`ACTION`/`NOTE`/`ERROR`
records. A live `--flush` atomically advances only the fired keys, while a dry
run evaluates the same work without changing the state file. Heavy or mutating
work is described as an action for another worker rather than performed by the
poller. One loop can therefore service fast CI checks and slow maintenance
checks without separate schedulers or a repeated generic reminder.

Hermit's prototype keeps the independent cadence registry and state-derived
output, but exposes only `--dry-run`. Its optional `key=epoch` state file is
read-only and exists only to exercise due/not-due selection before scheduling is
jointly designed.

## Existing Reminder Path

Two existing parent-repository mechanisms supply general reminders:

- `.orc/plugins/hermit-dev/index.ts` registers `prHealthHeartbeat`, a durable
  `wf.loop` that sleeps for 30 minutes and calls `orc.sendWakeup` with a static
  request to run `scripts/pr_status.py`. Despite the common "hourly status"
  description, the checked-in PR-health interval is currently 30 minutes.
- `alignment_reminder_prompt.md` is the broader static status/alignment prompt.
  `AGENTS.md` separately requires `scripts/status-log.rs` on every hourly status
  update. No checked-in scheduler directly reads the alignment prompt.

The future integration point is to let one scheduler run this poller, then
deliver only its current `ACTION` records. That can subsume both the static PR
heartbeat and the general alignment reminder. This dry-run does not modify or
disable either mechanism and contains no `sendWakeup`, `tmux`, or `send-keys`
code.

## Checks And Cadences

| Check | Cadence | Current behavior |
|---|---:|---|
| `repo-hygiene` | 5 min | Dirty state, primary `main` invariants, cached `origin/main`, and parent gitlink mismatches |
| `ci-health` | 10 min | Current `rrnewton/hermit:main` `CI (GitHub-managed portable)` push run |
| `outstanding-prs` | 15 min | Exact open PR counts across `rrnewton/hermit` and `rrnewton/reverie` |
| `recent-actions` | 30 min | K most recent main-branch GitHub Actions runs |
| `recent-main-history` | 1 hour | 24-hour sliding window of main commits correlated with authoritative CI |
| `repository-lints` | 1 hour | Portable-path and whitespace checks |
| `stress-result-freshness` | 6 hours | Non-authoritative candidate-file spot-check (`STUB`) |
| `super-validate-freshness` | 6 hours | Non-authoritative candidate-file spot-check (`STUB`) |

The result stubs deliberately do not infer a pass from a filename or mtime.
They remain `STUB` until each producer writes a canonical marker containing at
least completion time, result, tested SHA, and exact command.

## Reproduce

From the parent checkout on `main`:

```bash
cd ~/work/dev-hermit
python3 scripts/test_health_poll.py
with-proxy ./scripts/health-poll.py --dry-run --force --recent-actions 8
```

`with-proxy` is required on hosts whose public GitHub access uses the Meta
forward proxy. Elsewhere, run the script directly.

To demonstrate frequency gating without writing state, create a disposable
state file outside the repository and point the poller at it:

```text
repo-hygiene=1785690000
ci-health=1785690000
```

```bash
./scripts/health-poll.py --dry-run --state-file /tmp/dev-hermit-health-state
```

Use `--force` for a complete snapshot regardless of that state. The process
returns zero when collection completes; health severity is carried by `CHECK`
and `ACTION` lines so one failed check cannot suppress the remaining checks.
