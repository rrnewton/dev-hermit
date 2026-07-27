#!/usr/bin/env bash
# Second pass: re-run the 4 successful max-timeslice values WITH --summary-json
# to capture the deterministic "Timeslice stats: count=N" (the "PMU events" metric).
# All paths absolute so cwd does not matter.
set -u

EXP=/home/newton/work/dev-hermit/experiments/timeslice_binary_search_20260727
SLOT=/home/newton/work/dev-hermit/worktrees/274/hermit
HERMIT="$SLOT/target/release/hermit"
KERNEL=/home/newton/work/dev-hermit/ignored/qemu-linux/bzImage
INITRD=/home/newton/work/dev-hermit/ignored/qemu-linux/initramfs-hermit.cpio.gz
QEMU=/usr/local/bin/qemu-system-x86_64

OUT="$EXP/summary"
mkdir -p "$OUT"
PROG="$OUT/progress.txt"
CSV="$OUT/summary_counts.csv"
: > "$PROG"
echo "max_timeslice,exit_code,wall_seconds,timeslice_count" > "$CSV"

for V in 100000000 500000000 1000000000 2000000000; do
  LOG="$OUT/max_${V}.log"
  SJ="$OUT/max_${V}.summary.json"
  echo "$(date +%H:%M:%S) START max-timeslice=$V" >> "$PROG"
  START=$(date +%s.%N)
  timeout 300 "$HERMIT" run --strict --summary --summary-json="$SJ" \
    --target-timeslice 100000 --max-timeslice "$V" -- \
    "$QEMU" -accel tcg,thread=single -smp 1 -icount shift=0,sleep=off \
    -kernel "$KERNEL" -initrd "$INITRD" \
    -append 'console=ttyS0 panic=1' -nographic -no-reboot -m 256 \
    > "$LOG" 2>&1
  RC=$?
  END=$(date +%s.%N)
  WALL=$(awk "BEGIN{printf \"%.1f\", $END-$START}")

  # Prefer the machine-readable summary JSON; fall back to the log's Display line.
  COUNT=$(python3 - "$SJ" "$LOG" <<'PY'
import json, re, sys
sj, log = sys.argv[1], sys.argv[2]
def find_count(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "count" and isinstance(v, int):
                return v
            r = find_count(v)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = find_count(v)
            if r is not None:
                return r
    return None
c = None
try:
    with open(sj) as f:
        c = find_count(json.load(f))
except Exception:
    c = None
if c is None:
    try:
        txt = open(log, errors="replace").read()
        m = re.search(r"Timeslice stats:.*count=(\d+)", txt)
        if m:
            c = int(m.group(1))
    except Exception:
        c = None
print(c if c is not None else "NA")
PY
)
  echo "$V,$RC,$WALL,$COUNT" >> "$CSV"
  echo "$(date +%H:%M:%S) DONE  max-timeslice=$V rc=$RC wall=${WALL}s count=$COUNT" >> "$PROG"
done
echo "$(date +%H:%M:%S) ALL DONE" >> "$PROG"
