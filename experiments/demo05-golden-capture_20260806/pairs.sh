#!/bin/bash
# Repeated CONTROLLED demo05 pairs through 05-qemu-boot.py, tabulating the three
# witnesses independently:
#   qcow2 sha256   <- the demo's PRIMARY determinism witness ("bitwise-reproducible")
#   serial sha256  <- guest console transcript
#   INFO log       <- the whole-process trace (what a prefix-depth golden would compare)
#
# One pair is not a rate. The first controlled pair matched on qcow2; the second did
# not, so the question is no longer "does it reproduce" but "how often".
set -u
ROOT=/home/newton/work/dev-hermit
ASSETS=$ROOT/ignored/w2-demo05/assets
OUT=$ROOT/ignored/w2-demo05/pairs
PAIRS=${PAIRS:-3}
mkdir -p "$OUT"
TSV=$OUT/pairs.tsv
[ -f "$TSV" ] || printf 'pair\tqcow2_run1\tqcow2_run2\tqcow2\tserial\tinfo_bytes_equal\tverdict\n' > "$TSV"

export QEMU_ASSETS="$ASSETS"
export HERMIT_RELEASE=$ROOT/ignored/det4-parity/hermit/target/release/hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib:${LD_LIBRARY_PATH:-}

for p in $(seq 1 "$PAIRS"); do
  rm -rf "$ASSETS/boot-anchor" "$ASSETS/.work" "$ASSETS/run-history"
  for i in 1 2; do
    ( cd "$ROOT" && timeout 1800 ./demos/05-qemu-boot.py ) > "$OUT/p$p-run$i.out" 2>&1
  done
  q1=$(python3 -c "import json;print(json.load(open('$ASSETS/boot-anchor/run-metadata.json'))['qcow2_sha256'][:16])" 2>/dev/null)
  hist=$(ls -d "$ASSETS"/run-history/*/ 2>/dev/null | head -1)
  q2=$(python3 -c "import json;print(json.load(open('$hist/run-metadata.json'))['qcow2_sha256'][:16])" 2>/dev/null)
  s1=$(python3 -c "import json;print(json.load(open('$ASSETS/boot-anchor/run-metadata.json'))['serial_sha256'][:16])" 2>/dev/null)
  s2=$(python3 -c "import json;print(json.load(open('$hist/run-metadata.json'))['serial_sha256'][:16])" 2>/dev/null)
  qv=DIFFER; [ "$q1" = "$q2" ] && qv=MATCH
  sv=DIFFER; [ "$s1" = "$s2" ] && sv=MATCH
  iv=DIFFER
  cmp -s "$ASSETS/boot-anchor/hermit-info.log" "$hist/hermit-info.log" && iv=MATCH
  verdict=$(grep -oE 'SUCCESS|PARTIAL' "$OUT/p$p-run2.out" | tail -1)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$p" "$q1" "$q2" "$qv" "$sv" "$iv" "$verdict" >> "$TSV"
  echo "pair $p: qcow2=$qv ($q1 vs $q2) serial=$sv info_raw=$iv -> $verdict"
done
echo; column -t -s$'\t' "$TSV"
