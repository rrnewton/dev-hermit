#!/usr/bin/env bash
# Nightly Hermit determinism stress run — driver.
#
# WHAT: at main HEAD, run the shared concurrent-BURST probe for each workload in
# the stress set, then fold every burst through the flaky-is-red verdict and
# record one durable run per workload into the ci-hub store
# (ignored/ci-hub/stress-runs.jsonl). Any non-CLEAN verdict raises a P0 alarm.
#
# WHY it lives here (parent host), not on a GitHub runner: the flake this exists
# to catch is LOAD-DEPENDENT (multisect measured ~28% per-instance hang at 128
# concurrent under real fleet load; an idle single-tenant GH runner shows ~0% =
# false green). The nightly must run where the load is — this dev host.
#
# HARNESS SHARING (owner rule "do NOT write a second one"): this driver owns NO
# burst logic. It shells out to ONE shared primitive via $STRESS_BURST_CMD, whose
# contract is:
#
#     $STRESS_BURST_CMD <sha> <width> <timeout_s> <workload>  ->  burst CSV on stdout
#       CSV row: sha,short,build_s,burst_N,hangs,passes,other,hang_rate,STATUS
#
# multisect's experiments/.../probe.sh already emits that CSV; the only missing
# piece for nightly is a relocatable / --prebuilt build mode so a main-HEAD run
# does not collide with multisect's SHA-keyed worktrees. Until that shared tool
# is agreed & placed (see ci-hub/stress/README.md), $STRESS_BURST_CMD is unset
# and this driver records a RED "ERROR: primitive not wired" run rather than
# silently passing. That is intentional: a silent-green nightly is the exact
# failure mode this task exists to kill.
#
# ENV:
#   STRESS_BURST_CMD   command template (see contract above). REQUIRED to probe.
#   STRESS_WIDTH       concurrent instances per burst          (default 64)
#   STRESS_TIMEOUT     per-instance timeout seconds            (default 20)
#   STRESS_WORKLOADS   space/newline-separated workload slugs  (default set below)
#   STRESS_REPO        OWNER/REPO                              (default rrnewton/hermit)
#   CI_HUB_STRESS_STORE  store path (default ignored/ci-hub/stress-runs.jsonl)
#   CI_HUB_AGENT       agent tag recorded on each run          (default hermit-250)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERMIT="$ROOT/hermit"
STORE_PY="$ROOT/ci-hub/stress/stress_store.py"
WIDTH="${STRESS_WIDTH:-64}"
TIMEOUT="${STRESS_TIMEOUT:-20}"
REPO="${STRESS_REPO:-rrnewton/hermit}"
AGENT="${CI_HUB_AGENT:-hermit-250}"
# Alarm sink: a STANDING open task, NOT the (closable) setup task — else a closed
# task swallows every future nightly P0 and the alarm goes silently invisible.
ALARM_TASK="${STRESS_ALARM_TASK:-nightly_stress_red_triage}"

# Default workload set — the paths where flakiness actually bites (NOT /bin/echo).
# Seeded with the CONFIRMED reproducer; grows to reap/waitpid + scheduling/futex.
DEFAULT_WORKLOADS='tests_misc:vfork::vfork_parent_resumes_after_child_exec'
WORKLOADS="${STRESS_WORKLOADS:-$DEFAULT_WORKLOADS}"

ts() { TZ=UTC date +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" >&2; }

# Evidence capture for ATTRIBUTION. Default ON for the nightly: the whole point of
# this run is to catch a flake, and a flake we cannot attribute is half-useful. We
# set STRESS_CAPTURE_DIR so the shared burst primitive (which flows it down to
# matched.sh) preserves a bundle per FAILING instance; then on any non-CLEAN
# verdict we fold `attribution.py report` into the alarm so the P0 states a CAUSE
# (INFRASTRUCTURE / HERMIT_NONDETERMINISM / ENVIRONMENT / …), not just a rate.
CAPTURE="${STRESS_CAPTURE:-1}"
CAPBASE="$ROOT/ignored/ci-hub/stress-capture"
ATTR_PY="$ROOT/ci-hub/attribution/attribution.py"

# --- resolve main HEAD (read-only; never mutates the primary checkout) --------
if ! with-proxy git -C "$HERMIT" fetch -q origin main 2>/dev/null; then
  log "WARN: could not fetch origin/main; using local ref"
fi
SHA="$(git -C "$HERMIT" rev-parse origin/main 2>/dev/null || git -C "$HERMIT" rev-parse HEAD)"
log "nightly stress at main HEAD $SHA (width=$WIDTH timeout=${TIMEOUT}s repo=$REPO)"

overall_alarm=0
# Attribute the preserved failing-run bundles in a capture dir to a CAUSE.
# Echoes a one-line attribution summary (empty if nothing to attribute) and,
# when there is evidence, writes the full per-bundle report to a sidecar.
attribute_capture() {
  # $1=capture_dir  $2=marker_path (sidecar is <marker>.attribution.txt)
  local capdir="$1" marker="$2"
  [ -n "$capdir" ] && [ -d "$capdir" ] || return 0
  find "$capdir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | grep -q . || return 0
  [ -f "$ATTR_PY" ] || return 0
  local report
  report="$(python3 "$ATTR_PY" report "$capdir" 2>/dev/null)" || return 0
  [ -n "$report" ] || return 0
  printf '%s\n' "$report" > "${marker%.json}.attribution.txt" 2>/dev/null
  # One-line headline: the SUMMARY line the report prints last, plus a dropped-cap note.
  local headline dropped
  headline="$(sed -n 's/^ATTRIBUTION SUMMARY: //p' <<<"$report" | tail -1)"
  dropped="$(cat "$capdir/.dropped-over-cap" 2>/dev/null | wc -l)"
  [ "${dropped:-0}" -gt 0 ] && headline="$headline (+$dropped bundle(s) dropped over cap)"
  echo "$headline"
}

raise_alarm() {
  # $1=workload $2=verdict $3=one-line detail $4=capture_dir(optional)
  overall_alarm=1
  local marker="$ROOT/ignored/ci-hub/stress-alarm-$(TZ=UTC date +%Y%m%dT%H%M%SZ)-$$.json"
  mkdir -p "$(dirname "$marker")"
  local attr; attr="$(attribute_capture "${4:-}" "$marker")"
  local detail="$3"; [ -n "$attr" ] && detail="$3 | ATTRIBUTION: $attr"
  printf '{"ts":"%s","repo":"%s","sha":"%s","workload":"%s","verdict":"%s","detail":"%s","attribution":"%s","severity":"P0"}\n' \
    "$(ts)" "$REPO" "$SHA" "$1" "$2" "$detail" "$attr" > "$marker" 2>/dev/null
  log "🔴 P0 ALARM [$2] $1 — $detail (marker: $marker)"
  [ -n "${STRESS_NO_ESCALATE:-}" ] && return 0
  # Best-effort external escalation (durable store record is the primary signal).
  command -v tg >/dev/null 2>&1 && \
    tg note "$ALARM_TASK" "P0 NIGHTLY-STRESS RED [$2] $REPO@${SHA:0:12} workload=$1 — $detail. Flaky-is-red: determinism of outcome violated. Triage immediately." >/dev/null 2>&1 || true
  [ -x "$ROOT/scripts/status-log.rs" ] && \
    "$ROOT/scripts/status-log.rs" --append "nightly-stress P0 RED [$2] $1 @${SHA:0:12}" >/dev/null 2>&1 || true
}

# --- per-workload burst + record ---------------------------------------------
REPS="${STRESS_REPS:-1}"
for wl in $WORKLOADS; do
  log "workload: $wl (reps=$REPS width=$WIDTH)"
  # Per-workload capture dir; the burst primitive inherits it via env and
  # preserves a bundle per failing instance. Keyed by workload+sha+run so a
  # multi-workload night never cross-contaminates evidence.
  wl_cap=""
  if [ "$CAPTURE" != "0" ]; then
    wl_cap="$CAPBASE/$(printf '%s' "$wl" | tr ':/' '__')-${SHA:0:12}-$(TZ=UTC date +%Y%m%dT%H%M%SZ)"
    export STRESS_CAPTURE_DIR="$wl_cap"
  else
    unset STRESS_CAPTURE_DIR
  fi
  if [ -z "${STRESS_BURST_CMD:-}" ]; then
    # Primitive not wired yet (awaiting shared-harness agreement). Record RED.
    csv="$SHA,,,,,,,,NOWIRE"
  else
    # Run REPS burst waves; concatenate CSV rows — stress_store aggregates them,
    # so "over the night's total reps" is one verdict across every wave.
    csv=""
    for rep in $(seq 1 "$REPS"); do
      row="$($STRESS_BURST_CMD "$SHA" "$WIDTH" "$TIMEOUT" "$wl" 2>/dev/null)"
      [ -z "$row" ] && row="$SHA,,,,,,,,NOOUTPUT"
      csv="${csv:+$csv$'\n'}$row"
      log "  wave $rep/$REPS: $row"
    done
  fi
  # Record; stress_store prints a machine summary (VERDICT=... ) to stdout and
  # exits 2 on RED, 0 on CLEAN. Capture stdout for the alarm text.
  summary="$(printf '%s\n' "$csv" | python3 "$STORE_PY" record --csv - \
        --repo "$REPO" --sha "$SHA" --workload "$wl" \
        --width "$WIDTH" --timeout "$TIMEOUT" \
        --source-tool "${STRESS_BURST_CMD:-<unwired>}" --trigger nightly)"
  rc=$?
  verdict="$(sed -n 's/^VERDICT=\([A-Z]*\).*/\1/p' <<<"$summary")"
  case "$rc" in
    0) log "🟢 ${verdict:-CLEAN} $wl" ;;
    2) raise_alarm "$wl" "${verdict:-RED}" "${summary#VERDICT=* }" "$wl_cap" ;;
    *) log "WARN: recorder exited $rc for $wl: $summary" ;;
  esac
done

log "nightly stress complete — overall $([ $overall_alarm -eq 0 ] && echo GREEN || echo 🔴 RED/P0)"
exit $overall_alarm
