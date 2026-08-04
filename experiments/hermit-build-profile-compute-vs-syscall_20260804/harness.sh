#!/usr/bin/env bash
# Measurement harness for TWO shared-setup questions:
#   Q_profile: does an unoptimized (debug) hermit run guests materially slower
#              than optimized (release), and does it depend on guest shape?
#   Q_pin:     does hermit-ptrace run BETTER pinned to ONE core (same-core
#              tracer+guest, warm cache) than unpinned (cross-core: IPI +
#              cache-line migration)? Hermit sequentializes threads anyway, so
#              the parallelism given up is parallelism never used.
#
# Backend: default `hermit run` == reverie-ptrace (the per-syscall host
# round-trip path). State this in reports.
#
# METHOD: boxed; medians over N; report WALL and CPU-seconds (user+sys).
#   placement=unpinned : systemd --user unit, NO cpuset (scheduler free).
#   placement=pinned1  : run via $CPUSET_RUN (hermit-220's stateful allocator,
#                        which PICKS the core). If $CPUSET_RUN is unset, the
#                        pinned arm is SKIPPED — we do NOT hand-roll pinning.
#
# Usage: harness.sh <profile-label> <hermit-bin|-> [N]
#   profile-label: release|debug ; hermit-bin '-' means native-only pass
#   Env: CPUSET_RUN="cpuset-alloc run --cores 1 --"  (allocator wrapper)
#        GUESTS="compute_bound syscall_bound"
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PROFILE="${1:?profile label}"
HERMIT="${2:?hermit bin or -}"
N="${3:-7}"
OUT="$HERE/results.csv"
GUESTS="${GUESTS:-compute_bound syscall_bound}"
GBIN="$HERE/guests/bin"
CPUSET_RUN="${CPUSET_RUN:-}"

[ -f "$OUT" ] || echo "profile,guest,mode,placement,cores,run,wall_s,cpu_s" > "$OUT"

# measure <placement> -- CMD... ; echoes "wall cpu cores"
measure() {
  local placement="$1"; shift; [ "$1" = "--" ] && shift
  local tf; tf="$(mktemp)"; local cores="-"
  local unit="exp-$$-$RANDOM"
  if [ "$placement" = pinned1 ]; then
    [ -n "$CPUSET_RUN" ] || { echo "SKIP -"; rm -f "$tf"; return 3; }
    # allocator picks + pins the whole tree; it prints assigned cores to stderr.
    local ef; ef="$(mktemp)"
    $CPUSET_RUN /usr/bin/time -f '%e %U %S' -o "$tf" -- "$@" >/dev/null 2>"$ef"
    cores="$(grep -oiE 'core[s]?[ =:]+[0-9,-]+' "$ef" | head -1 | grep -oE '[0-9,-]+$')"; cores="${cores:-picked}"
    rm -f "$ef"
  else
    # 1-CPU BOX: CPUQuota=100% = one CPU of BANDWIDTH (not a cpuset pin). Native
    # and hermit share the same one-CPU box, so any hermit slowdown is
    # instrumentation cost, not lost parallelism (hermit's threads sequentialize
    # onto the single CPU). CPU-seconds stays truthful under a contended box.
    systemd-run --user --wait --collect --quiet --unit="$unit" \
      --property=CPUQuota=100% \
      --setenv=HOME="$HOME" --setenv=PATH="$PATH" \
      /usr/bin/time -f '%e %U %S' -o "$tf" -- "$@" >/dev/null 2>&1
  fi
  awk -v c="$cores" '{printf "%s %.3f %s", $1, $2+$3, c}' "$tf"
  rm -f "$tf"
}

emit() { # profile guest mode placement cores run wall cpu
  echo "$1,$2,$3,$4,$5,$6,$7,$8" >> "$OUT"
  echo "  $1/$2/$3/$4[$5] run$6: wall=${7}s cpu=${8}s"
}

for guest in $GUESTS; do
  G="$GBIN/$guest"
  [ -x "$G" ] || { echo "missing guest $G (make -C guests)"; exit 1; }
  for placement in unpinned pinned1; do
    # native pass only when hermit bin is '-' OR profile==release (native is
    # profile-independent; record it once under the 'release' invocation)
    if [ "$HERMIT" = "-" ]; then
      for r in $(seq 1 "$N"); do
        read -r w c cr < <(measure "$placement" -- "$G") || continue
        [ "$w" = "SKIP" ] && { echo "  native/$guest/$placement SKIPPED (no allocator)"; break; }
        emit native "$guest" native "$placement" "$cr" "$r" "$w" "$c"
      done
    else
      for r in $(seq 1 "$N"); do
        read -r w c cr < <(measure "$placement" -- "$HERMIT" run -- "$G") || continue
        [ "$w" = "SKIP" ] && { echo "  $PROFILE/$guest/hermit/$placement SKIPPED (no allocator)"; break; }
        emit "$PROFILE" "$guest" hermit "$placement" "$cr" "$r" "$w" "$c"
      done
    fi
  done
done
echo "wrote $OUT"
