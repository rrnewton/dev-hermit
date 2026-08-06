#!/usr/bin/env bash
# Durable driver for the hourly alignment-reminder relay.
#
# ROOT CAUSE this replaces
# ------------------------
# The relay delivered `alignment_reminder_prompt.md` to the coordinator pane 169
# times, hourly on the hour, 2026-07-29T03:00 -> 2026-08-05T03:00 PDT, then
# stopped dead. Measured evidence:
#
#   * the FINAL record is status=failed with
#     `tmux socket does not exist: /run/user/212630/orc-tmux/tmux-212630/default`
#   * a NEW tmux server socket was created at 2026-08-05 19:13 -- ~16h later --
#     and the relay did NOT resume
#   * 18 of the 169 ticks had already failed (first 2026-07-31T21:00) and the
#     schedule kept running, so FAILURE ALONE never killed it
#
# Together those say the driver was SESSION-BOUND: it lived inside the ORC
# session / the old tmux server, died with it, and nothing outside that session
# owned the schedule, so a fresh server could not resurrect it. Same failure
# class as the operational-health wf.loop, and this is the same fix that class
# already got: a systemd --user timer with Linger, which survives logout,
# reboot, and session recycling.
#
# WHY THE DEATH HID FOR ~19 HOURS
# -------------------------------
# The delivery log only records ATTEMPTS. A tick that never fires writes
# nothing, so the log looked healthy right up to the last entry -- the outage
# was visible only by noticing an absence. This script therefore appends to an
# INVOCATION log FIRST, before anything that can fail, so "the timer fired" is
# observable independently of "the message was delivered". That separation is
# the whole point; without it a silent stop reads as health again.
set -uo pipefail

ROOT=/home/newton/work/dev-hermit
MESSAGE_FILE="$ROOT/alignment_reminder_prompt.md"
RELAY="$ROOT/scripts/orc-hermit-msg.py"
INVLOG="${ALIGNMENT_RELAY_INVLOG:-$HOME/.local/state/alignment-reminder-relay-invocations.log}"
SOCKET="${ALIGNMENT_RELAY_SOCKET:-/run/user/$(id -u)/orc-tmux/tmux-$(id -u)/default}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
note() { printf '%s %s\n' "$(ts)" "$*" >>"$INVLOG"; }

mkdir -p "$(dirname "$INVLOG")" 2>/dev/null || true

# HEARTBEAT FIRST. Every tick appends exactly one line here regardless of what
# happens next, so a flat invocation log means the TIMER is dead -- the original
# defect -- and never merely that delivery was skipped.
note "tick start pid=$$"

if [[ ! -r $MESSAGE_FILE ]]; then
    note "tick end rc=0 outcome=skipped reason=message-file-unreadable path=$MESSAGE_FILE"
    exit 0
fi
if [[ ! -x $RELAY && ! -r $RELAY ]]; then
    note "tick end rc=0 outcome=skipped reason=relay-missing path=$RELAY"
    exit 0
fi

# A missing tmux socket is the EXPECTED state between coordinator sessions. It
# is a skip, not a failure: exiting nonzero here would mark the systemd unit
# failed for a condition that is routine and self-healing, and a unit in a
# failed state is one `systemctl` default away from not being retried.
if [[ ! -S $SOCKET ]]; then
    note "tick end rc=0 outcome=skipped reason=no-tmux-socket socket=$SOCKET"
    exit 0
fi

out="$(timeout 120 python3 "$RELAY" --message-file "$MESSAGE_FILE" --socket "$SOCKET" 2>&1)"
rc=$?
# The delivery log (orc-hermit-msg.log) remains the authority on WHAT was
# delivered; this line records only that the tick ran and how it ended, so the
# two signals stay independent.
summary="$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)"
if (( rc == 0 )); then
    note "tick end rc=0 outcome=delivered"
else
    note "tick end rc=$rc outcome=relay-failed detail=$summary"
fi

# Always exit 0: a delivery failure is recorded, not escalated to a unit
# failure. The relay failed 18 times in its first life without that being a
# reason to stop scheduling, and the systemd unit must behave the same way.
exit 0
