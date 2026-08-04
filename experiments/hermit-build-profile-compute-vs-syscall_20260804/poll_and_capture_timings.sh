#!/bin/bash
# poll_and_capture_timings.sh — good-citizen poller that fires capture_build_timings.sh
# ONCE the shared box is clear, then exits. Owner-approved "capture --timings when box frees".
# Safety: requires the box clear on TWO consecutive checks 45s apart (avoids racing a peer
# mid-startup). Caps total wait; self-expires with a log line. Own setsid group => windable.
set -u
DIR=/home/newton/work/dev-hermit/experiments/hermit-build-profile-compute-vs-syscall_20260804
LOG="$DIR/poll_and_capture.log"
INTERVAL=300          # 5 min between polls
MAX_WAIT=$((3*3600))  # give up after 3h
elapsed=0

clear_check() {
  local busy expu
  busy=$(ps -eo args 2>/dev/null | grep -E 'validate\.sh|test_harness\.sh|prebuild-then-validate|harness\.sh (release|debug|native)' | grep -v grep | grep -v _capture_timings)
  expu=$(systemctl --user list-units 'exp-*' --no-legend 2>/dev/null | grep -c running)
  [ -z "$busy" ] && [ "${expu:-0}" = "0" ]
}

echo "=== poller start $(date -u +%FT%TZ), interval=${INTERVAL}s max=${MAX_WAIT}s ===" >> "$LOG"
while [ "$elapsed" -lt "$MAX_WAIT" ]; do
  if clear_check; then
    sleep 45
    if clear_check; then
      echo "$(date -u +%FT%TZ) box clear on 2 consecutive checks -> firing capture" >> "$LOG"
      bash "$DIR/capture_build_timings.sh" >> "$LOG" 2>&1
      echo "$(date -u +%FT%TZ) capture returned rc=$? -> poller exiting" >> "$LOG"
      exit 0
    fi
  fi
  echo "$(date -u +%FT%TZ) box busy (elapsed=${elapsed}s) -> sleeping ${INTERVAL}s" >> "$LOG"
  sleep "$INTERVAL"; elapsed=$((elapsed+INTERVAL))
done
echo "$(date -u +%FT%TZ) MAX_WAIT reached, box never cleared -> giving up (re-run poller when quiet)" >> "$LOG"
