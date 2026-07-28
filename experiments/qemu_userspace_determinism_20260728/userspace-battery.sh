#!/bin/sh
# Userspace program battery run as PID 1 (/init) inside the QEMU guest.
# Goal (milestone 1): demonstrate that a spread of ordinary userspace
# programs execute to completion INSIDE a QEMU Linux VM that is itself
# running as a userspace process under Hermit, and that their combined
# output is byte-for-byte deterministic across independent Hermit runs.
#
# The output between USERSPACE-BATTERY-BEGIN and USERSPACE-BATTERY-END is
# the determinism payload the harness diffs. Everything printed there must
# be a deterministic function of the guest image alone (no wall-clock, no
# host entropy, no host PIDs leaking through). Hermit virtualizes time,
# pid, and uid inside the guest process, so time/pid-derived output is
# stable run-to-run.
mount -t proc     none /proc  2>/dev/null
mount -t sysfs    none /sys   2>/dev/null
mount -t devtmpfs none /dev   2>/dev/null || mount -t tmpfs none /dev 2>/dev/null
[ -c /dev/ttyS0 ] || mknod /dev/ttyS0 c 4 64 2>/dev/null

echo "=========================================="
echo "HERMIT-QEMU-BASELINE-BOOT-OK"
echo "kernel: $(uname -r)"
echo "=========================================="

echo "USERSPACE-BATTERY-BEGIN"

# --- 1. Integer arithmetic (shell) ---------------------------------------
s=0; i=1
while [ "$i" -le 100 ]; do s=$((s + i)); i=$((i + 1)); done
echo "sum_1_100=$s"
echo "pow2_16=$((1 << 16))"
echo "mod=$((123456 % 789))"

# --- 2. seq + awk numeric pipeline ---------------------------------------
echo "seq_sum=$(seq 1 1000 | awk '{t+=$1} END{print t}')"
echo "seq_sq=$(seq 1 20 | awk '{t+=$1*$1} END{print t}')"

# --- 3. Text processing: sort / uniq / wc --------------------------------
printf 'banana\napple\ncherry\napple\nbanana\napple\n' > /tmp/words
echo "sorted_uniq=$(sort -u /tmp/words | tr '\n' ',')"
echo "top_word=$(sort /tmp/words | uniq -c | sort -rn | head -1 | awk '{print $2"="$1}')"
echo "wc=$(printf 'one two three\nfour five\n' | wc -w)"

# --- 4. Checksums of fixed content (determinism anchor) ------------------
printf 'The quick brown fox jumps over the lazy dog' > /tmp/fox
echo "md5_fox=$(md5sum /tmp/fox | awk '{print $1}')"
echo "sha256_fox=$(sha256sum /tmp/fox 2>/dev/null | awk '{print $1}')"
echo "cksum_fox=$(cksum /tmp/fox | awk '{print $1"_"$2}')"

# --- 5. Deterministic data generation + hash -----------------------------
seq 1 500 | awk '{print $1*7 % 251}' > /tmp/gen
echo "gen_md5=$(md5sum /tmp/gen | awk '{print $1}')"
echo "gen_lines=$(wc -l < /tmp/gen)"

# --- 6. Hermit-virtualized identity syscalls (must be stable) ------------
# Under Hermit the guest sees a virtualized pid/uid/time, so these are
# deterministic across runs even though on bare metal they would vary.
echo "pid=$$"
echo "uid=$(id -u)"
echo "date_utc=$(TZ=UTC date -u '+%Y-%m-%dT%H:%M:%S')"
echo "hostname=$(hostname 2>/dev/null || echo none)"

# --- 7. Filesystem round-trip -------------------------------------------
mkdir -p /tmp/d
for n in 3 1 2; do echo "row-$n" > "/tmp/d/f$n"; done
echo "fs_cat=$(cat /tmp/d/f1 /tmp/d/f2 /tmp/d/f3 | tr '\n' ',')"
echo "fs_ls=$(ls /tmp/d | sort | tr '\n' ',')"

echo "USERSPACE-BATTERY-END"

echo "HERMIT-QEMU-AUTOTEST-DONE"
poweroff -f
