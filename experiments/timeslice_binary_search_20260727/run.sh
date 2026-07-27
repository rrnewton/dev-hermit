#!/bin/bash
# Binary search over --max-timeslice for QEMU/TCG boot under hermit --strict.
set -u
SLOT=/home/newton/work/dev-hermit/worktrees/274/hermit
HERMIT="$SLOT/target/release/hermit"
KERNEL=/home/newton/work/dev-hermit/ignored/qemu-linux/bzImage
INITRD=/home/newton/work/dev-hermit/ignored/qemu-linux/initramfs-hermit.cpio.gz
EXPDIR=/home/newton/work/dev-hermit/experiments/timeslice_binary_search_20260727
LOGS="$EXPDIR/logs"
CSV="$EXPDIR/results.csv"
echo "max_timeslice,exit_code,wall_seconds,timeslice_count,reached_serial" > "$CSV"
VALUES="10000000 50000000 100000000 500000000 1000000000 2000000000"
for V in $VALUES; do
  LOG="$LOGS/max_${V}.log"
  echo "=== max-timeslice=$V start ===" | tee -a "$EXPDIR/progress.txt"
  START=$(date +%s.%N)
  timeout 300 "$HERMIT" run --strict --target-timeslice 100000 --max-timeslice "$V" -- \
    "$QEMU" -accel tcg,thread=single -smp 1 -icount shift=0,sleep=off \
    -kernel "$KERNEL" -initrd "$INITRD" -append 'console=ttyS0 panic=1' \
    -nographic -no-reboot -m 256 > "$LOG" 2>&1
  EC=$?
  END=$(date +%s.%N)
  WALL=$(echo "$END - $START" | bc)
  COUNT=$(grep -oE 'Timeslice stats:.*count=[0-9]+' "$LOG" | grep -oE 'count=[0-9]+' | grep -oE '[0-9]+' | tail -1)
  [ -z "$COUNT" ] && COUNT="NA"
  if grep -qiE 'Linux version|Booting|ttyS0|Freeing unused|console \[ttyS0\]|Kernel command line' "$LOG"; then REACHED=yes; else REACHED=no; fi
  printf '%s,%s,%.1f,%s,%s\n' "$V" "$EC" "$WALL" "$COUNT" "$REACHED" | tee -a "$CSV"
  echo "=== max-timeslice=$V done ec=$EC wall=${WALL}s count=$COUNT reached=$REACHED ===" | tee -a "$EXPDIR/progress.txt"
done
echo "ALL DONE" | tee -a "$EXPDIR/progress.txt"
