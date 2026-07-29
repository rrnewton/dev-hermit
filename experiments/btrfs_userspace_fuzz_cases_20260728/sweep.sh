#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERMIT=$ROOT/hermit/target/release/hermit
BTRFS=$ROOT/ignored/btrfs-progs-v7.1-bin/btrfs.box.static
EXP=$ROOT/experiments/btrfs_userspace_fuzz_cases_20260728
SCOPED=$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh
cd "$EXP"
SUB="$1"; shift          # subcmd label, e.g. chunkrec / check
CMD=("$@")               # btrfs args template; %IMG% replaced with scratch path
OUTD="runs/sweep_$SUB"; mkdir -p "$OUTD"
SUMMARY="$OUTD/summary.tsv"
printf 'image\tsha_s1\tsha_s2\tsha_s3\tsha_s1b\trepro_s1\tdivergent\tmax_ms\tworst_rc\n' > "$SUMMARY"
while read -r img; do
  SCR="$OUTD/$img.scratch"
  declare -A SHA; maxms=0; worstrc=0
  for spec in s1:1 s2:2 s3:3 s1b:1; do
    tag=${spec%%:*}; seed=${spec##*:}
    cp -f "corpus/$img" "$SCR"
    args=(); for a in "${CMD[@]}"; do [ "$a" = "%IMG%" ] && args+=("$SCR") || args+=("$a"); done
    "$SCOPED" --timeout 300 --output "$OUTD/$img.$tag" -- \
      "$HERMIT" run --strict --chaos --chaos-target-races --seed "$seed" -- \
      "$BTRFS" "${args[@]}" >/dev/null 2>&1
    SHA[$tag]=$(sha256sum "$OUTD/$img.$tag" | cut -c1-16)
    ms=$(sed -n 's/elapsed_ms=//p' "$OUTD/$img.$tag.status"); [ "${ms:-0}" -gt "$maxms" ] && maxms=$ms
    rc=$(sed -n 's/command_exit=//p' "$OUTD/$img.$tag.status")
    [ "$(sed -n 's/timed_out=//p' "$OUTD/$img.$tag.status")" = yes ] && rc=124
    [ "${rc:-0}" -gt "$worstrc" ] && worstrc=$rc
  done
  rm -f "$SCR"
  repro=$([ "${SHA[s1]}" = "${SHA[s1b]}" ] && echo YES || echo NO)
  uniq=$(printf '%s\n%s\n%s\n' "${SHA[s1]}" "${SHA[s2]}" "${SHA[s3]}" | sort -u | wc -l)
  div=$([ "$uniq" -gt 1 ] && echo "DIVERGENT($uniq)" || echo no)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$img" "${SHA[s1]}" "${SHA[s2]}" "${SHA[s3]}" "${SHA[s1b]}" "$repro" "$div" "$maxms" "$worstrc" >> "$SUMMARY"
  printf '  %-52s repro=%s %s ms=%s rc=%s\n' "$img" "$repro" "$div" "$maxms" "$worstrc"
done < corpus-keep.txt
echo "DONE $SUB"
