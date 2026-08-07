#!/usr/bin/env bash
set -u

label=$1
id=$2

ROOT=/home/newton/work/dev-hermit
HROOT=$ROOT/worktrees/w21/hermit
H=$HROOT/target/install_pkg/hermit
RSRCS=$HROOT/target/install_pkg/rsrcs
LU=$ROOT/ignored/lu-parity/usr/lib64
CORPUS_C=$ROOT/compat-envelope/corpus/corpus-c.tsv
CORPUS_NONC=$ROOT/compat-envelope/corpus/corpus-nonc.tsv
OUT=$ROOT/ignored/detlog-parity/current-0041130/corpus
key=${id//\//__}
cell=$OUT/runs/$key/$label
mkdir -p "$cell" "$OUT/fakehome"

backend=$label
[ "$label" = ptrace-control ] && backend=ptrace

guest_argv=()
if grep -Fq "$id|" "$CORPUS_C"; then
  guest_argv=("$OUT/build/$key/guest")
else
  line=$(grep -F "$id|" "$CORPUS_NONC" | head -1)
  rest=${line#*|}
  command=${rest#*|}
  command=${command//HERMITROOT/$HROOT}
  read -r -a guest_argv <<< "$command"
fi

if [ ! -x "${guest_argv[0]}" ]; then
  printf '%s\n' 125 > "$cell/rc"
  printf '%s\n' fixture_missing > "$cell/source"
  printf '%s\n' not_engaged > "$cell/engagement"
  exit 0
fi

timeout --signal=KILL 90 env -i \
  PATH="$RSRCS:/usr/bin:/bin" \
  HOME="$OUT/fakehome" \
  LC_ALL=C LANG=C TZ=UTC TERM=dumb SHELL=/bin/sh USER=newton LOGNAME=newton \
  LD_LIBRARY_PATH="$LU:$RSRCS" \
  HERMIT_INSTALL_DIR="$HROOT/target/install_pkg" \
  HERMIT_E9TOOL="$RSRCS/e9tool" \
  HERMIT_SABRE_BINARY="$RSRCS/sabre" \
  "$H" --log info --log-file "$cell/run.log" run \
    --backend "$backend" --strict --no-virtualize-cpuid --max-timeslice=disabled \
    --detlog-stack --detlog-heap -- "${guest_argv[@]}" \
    > "$cell/stdout" 2> "$cell/stderr"
rc=$?
printf '%s\n' "$rc" > "$cell/rc"

log_det=$(grep -c DETLOG "$cell/run.log" 2>/dev/null || true)
err_det=$(grep -c DETLOG "$cell/stderr" 2>/dev/null || true)
log_det=${log_det:-0}
err_det=${err_det:-0}
if [ "$err_det" -gt "$log_det" ]; then
  printf '%s\n' stderr_fallback > "$cell/source"
  cp "$cell/stderr" "$cell/events"
elif [ "$log_det" -gt 0 ]; then
  printf '%s\n' logfile > "$cell/source"
  cp "$cell/run.log" "$cell/events"
else
  printf '%s\n' none > "$cell/source"
  : > "$cell/events"
fi

case "$label" in
  e9patch)
    mapped=$(sed -n 's/.*mapped_sites=\([0-9][0-9]*\).*/\1/p' "$cell/stderr" | head -1)
    mapped=${mapped:-0}
    printf '%s\n' "$mapped" > "$cell/mapped_sites"
    if [ "$rc" -eq 0 ] && [ "$mapped" -gt 0 ] && [ $((log_det + err_det)) -gt 0 ]; then
      printf '%s\n' rewrite_engaged > "$cell/engagement"
    elif [ "$rc" -eq 0 ] && [ "$mapped" -eq 0 ]; then
      printf '%s\n' no_rewrite > "$cell/engagement"
    else
      printf '%s\n' not_engaged > "$cell/engagement"
    fi
    ;;
  liteinst)
    if [ "$rc" -eq 0 ] && grep -q 'activation verified' "$cell/stderr" && [ $((log_det + err_det)) -gt 0 ]; then
      printf '%s\n' preload_engaged > "$cell/engagement"
    else
      printf '%s\n' not_engaged > "$cell/engagement"
    fi
    ;;
  sabre)
    if [ "$rc" -eq 0 ] && [ "$err_det" -gt 1 ]; then
      printf '%s\n' rewrite_engaged_split_channel > "$cell/engagement"
    else
      printf '%s\n' not_engaged > "$cell/engagement"
    fi
    ;;
  dbi)
    if [ "$rc" -eq 0 ] && grep -q 'Detcore Tool active' "$cell/stderr" && [ "$err_det" -gt 0 ]; then
      printf '%s\n' dbt_engaged > "$cell/engagement"
    else
      printf '%s\n' not_engaged > "$cell/engagement"
    fi
    ;;
  *)
    if [ "$rc" -eq 0 ] && [ $((log_det + err_det)) -gt 0 ]; then
      printf '%s\n' detcore_engaged > "$cell/engagement"
    else
      printf '%s\n' not_engaged > "$cell/engagement"
    fi
    ;;
esac

exit 0
