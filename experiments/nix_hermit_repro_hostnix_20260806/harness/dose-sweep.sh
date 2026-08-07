#!/usr/bin/env bash
# dose-sweep.sh — find the minimum hermit flag-set ("dose") that makes a given
# derivation byte-reproducible on THIS host, at N repetitions.
#
# This host's PMU is unusable for hermit (`PMU validation failed ...
# AmdSpecLockMapShouldBeDisabled`), and hermit's default logical clock advances
# with retired-conditional-branch (RCB) counts read from that PMU. So the doses
# that matter here are the ones that take RCB out of the clock
# (`--no-rcb-time`) and out of preemption (`--max-timeslice disabled`).
#
# Usage: dose-sweep.sh '<nix-expr>' [N] [label-prefix]
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"

expr="${1:?nix-expr}"; N="${2:-5}"; prefix="${3:-dose}"

# Each entry is "<setarch-flag>|<hermit args>"; "native" is the control.
# The two `--no-namespace` rows are retained deliberately as the A/B against
# the superseded 20260729 mode, not as recommendations.
DOSES=(
  "native"
  "false|run --tmp=/tmp"
  "false|run --tmp=/tmp --no-rcb-time"
  "false|run --tmp=/tmp --no-rcb-time --max-timeslice disabled"
  "false|run --tmp=/tmp --no-rcb-time --max-timeslice disabled --strict"
  "true|run --no-namespace"
  "true|run --no-namespace --no-rcb-time --max-timeslice disabled"
)

for entry in "${DOSES[@]}"; do
  if [ "$entry" = "native" ]; then
    row=$(bash "$here/canonical-nrep.sh" "$prefix-native" native "$expr" "$N" 2>>"$LOG_DIR/$prefix.sweep.log")
    printf '%s\n' "$row"; continue
  fi
  sa="${entry%%|*}"; dose="${entry#*|}"
  slug=$(echo "$dose" | tr -cs 'A-Za-z0-9' '-' | sed 's/^-//;s/-$//')
  row=$(HERMIT_ARGS="$dose" HERMIT_USE_SETARCH="$sa" \
        bash "$here/canonical-nrep.sh" "$prefix-$slug" hermit "$expr" "$N" 2>>"$LOG_DIR/$prefix.sweep.log")
  printf '%s\n' "$row"
done
