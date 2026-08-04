#!/usr/bin/env bash
# Static LiteInst straddler-rate measurement over a corpus of real ELF binaries.
#
# For every decoded `syscall` (0f 05) instruction in each binary we compute the
# instruction's virtual address modulo the 64-byte cache line. Because ELF load
# bases are page-aligned (4096 % 64 == 0), (runtime_addr % 64) == (vaddr % 64),
# so ASLR does not change the classification: the static vaddr is exact.
#
# Two distinct notions of "straddler" exist in the LiteInst code and we report
# BOTH, because they differ by ~25x and were being conflated:
#   * word8   (offset 57..63): the 8-byte WordPatch++ publication word crosses a
#             line -> classify_word_patch() == GuardedSplit. THIS is what the
#             runtime uses to decide bail-to-ptrace / needs-calibrated-wait.
#   * prefix2 (offset 63):     the 2-byte `syscall` instruction itself crosses a
#             line -> straddle_after == Some(1). THIS is what the reported
#             `cacheline_straddlers` end-of-run stat actually counts.
set -euo pipefail
CORPUS="${1:?usage: scan.sh <corpus-file> <out-dir>}"
OUT="${2:?usage: scan.sh <corpus-file> <out-dir>}"
MAXBYTES="${3:-41943040}"   # skip files larger than 40 MiB (giant internal monoliths, not guest syscall paths)
FILE_TIMEOUT="${4:-25}"     # per-objdump wall timeout (s)
mkdir -p "$OUT"
PER="$OUT/per_binary.csv"
HIST="$OUT/offset_histogram.csv"
SKIP="$OUT/skipped.csv"
echo "path,syscall_sites,word8_straddlers,prefix2_straddlers" > "$PER"
echo "path,reason,bytes" > "$SKIP"

# Global accumulators via a temp file of every offset (0..63), one per site.
OFFS="$OUT/.offsets.txt"
: > "$OFFS"

while IFS= read -r f; do
  [ -f "$f" ] || continue
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt "$MAXBYTES" ]; then
    echo "$f,oversize,$sz" >> "$SKIP"; continue
  fi
  # Decode; pull the leading hex address of every line whose mnemonic is `syscall`.
  if ! timeout "$FILE_TIMEOUT" objdump -d "$f" > "$OUT/.d.txt" 2>/dev/null; then
    echo "$f,timeout_or_error,$sz" >> "$SKIP"; continue
  fi
  cat "$OUT/.d.txt" \
    | awk '
        /^[[:space:]]+[0-9a-f]+:.*[[:space:]]syscall[[:space:]]*$/ {
          addr=$1; sub(":","",addr); print addr
        }' \
    | awk -v path="$f" -v offs="$OFFS" '
        { a=strtonum("0x" $1); m=a%64; print m >> offs; n++; if(m>=57&&m<=63)w++; if(m==63)p++ }
        END { if(n>0) printf "%s,%d,%d,%d\n", path, n, w, p }' >> "$PER"
done < "$CORPUS"
rm -f "$OUT/.d.txt"

# Offset histogram 0..63.
echo "offset_mod64,count" > "$HIST"
sort -n "$OFFS" | uniq -c | awk '{printf "%d,%d\n",$2,$1}' >> "$HIST"

# Totals.
awk -F, 'NR>1{s+=$2;w+=$3;p+=$4;b++} END{
  printf "binaries_with_syscall_sites=%d\n", b;
  printf "total_syscall_sites=%d\n", s;
  printf "word8_straddlers=%d (%.3f%%)  [operative: classify_word_patch==GuardedSplit, off 57..63]\n", w, (s?100*w/s:0);
  printf "prefix2_straddlers=%d (%.3f%%)  [reported cacheline_straddlers stat, off 63]\n", p, (s?100*p/s:0);
}' "$PER" | tee "$OUT/totals.txt"
echo "skipped_files=$(($(wc -l < "$SKIP")-1)) (see skipped.csv)" | tee -a "$OUT/totals.txt"
rm -f "$OFFS"
