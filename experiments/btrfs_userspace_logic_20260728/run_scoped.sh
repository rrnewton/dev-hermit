#!/usr/bin/env bash
set -uo pipefail

if (($# < 4)) || [[ $1 != --timeout || $3 != --output ]]; then
  echo "usage: $0 --timeout SECONDS --output FILE -- COMMAND [ARG ...]" >&2
  exit 64
fi

timeout_seconds=$2
output=$4
shift 4
[[ ${1:-} == -- ]] || exit 64
shift
(($# > 0)) || exit 64
[[ $timeout_seconds =~ ^[1-9][0-9]*$ ]] || exit 64

mkdir -p -- "$(dirname -- "$output")"
printf '%q ' "$@" >"$output.command"
printf '\n' >>"$output.command"

start_ns=$(date +%s%N)
ready="$output.ready.$$"
setsid --wait bash -c '
  ready=$1
  shift
  printf "%s\n" "$$" >"$ready"
  kill -STOP "$$"
  exec "$@"
' scoped-child "$ready" "$@" >"$output" 2>&1 &
launcher_pid=$!

for _ in {1..100}; do
  [[ -s $ready ]] && break
  kill -0 "$launcher_pid" 2>/dev/null || break
  sleep 0.01
done
if [[ ! -s $ready ]]; then
  echo "scoped child failed before publishing its process group" >&2
  wait "$launcher_pid" 2>/dev/null || true
  exit 70
fi
child_pid=$(<"$ready")
child_pgid=$child_pid
kill -CONT -- "-$child_pgid"

child_live=yes
scoped_stop() {
  [[ $child_live == yes ]] || return 0
  kill -TERM -- "-$child_pgid" 2>/dev/null || true
  for _ in {1..20}; do
    kill -0 "$child_pid" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -KILL -- "-$child_pgid" 2>/dev/null || true
}
# shellcheck disable=SC2317  # Invoked indirectly by the trap below.
on_signal() {
  scoped_stop
  exit 130
}
trap on_signal INT TERM HUP

timed_out=no
deadline=$((SECONDS + timeout_seconds))
while kill -0 "$child_pid" 2>/dev/null; do
  if ((SECONDS >= deadline)); then
    timed_out=yes
    scoped_stop
    break
  fi
  sleep 0.2
done

set +e
wait "$launcher_pid"
command_exit=$?
set -e
child_live=no
trap - INT TERM HUP

end_ns=$(date +%s%N)
elapsed_ms=$(((end_ns - start_ns) / 1000000))
{
  echo "command_exit=$command_exit"
  echo "timed_out=$timed_out"
  echo "elapsed_ms=$elapsed_ms"
  echo "pid=$child_pid"
  echo "pgid=$child_pgid"
} >"$output.status"

if [[ $timed_out == yes ]]; then
  exit 124
fi
exit "$command_exit"
