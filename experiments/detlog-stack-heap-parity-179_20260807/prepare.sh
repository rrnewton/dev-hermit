#!/usr/bin/env bash
set -u

ROOT=/home/newton/work/dev-hermit
HROOT=$ROOT/worktrees/w21/hermit
CORPUS=$ROOT/compat-envelope/corpus/corpus-c.tsv
DENOM=/tmp/detlog-canonical-179.txt
OUT=$ROOT/ignored/detlog-parity/current-0041130/corpus
BUILD=$OUT/build

mkdir -p "$BUILD"
failed=0
selected=0
while IFS='|' read -r id prog cflags extra lane cstate; do
  grep -Fxq "$id" "$DENOM" || continue
  selected=$((selected + 1))
  key=${id//\//__}
  cell=$BUILD/$key
  mkdir -p "$cell"
  cflags_argv=()
  extra_argv=()
  if [ -n "$cflags" ]; then read -r -a cflags_argv <<< "$cflags"; fi
  if [ -n "$extra" ]; then
    read -r -a extra_rel <<< "$extra"
    for item in "${extra_rel[@]}"; do extra_argv+=("$HROOT/$item"); done
  fi
  cc -std=c11 -O2 -g -Wall -Wextra -Werror "${cflags_argv[@]}" \
    "$HROOT/$prog" "${extra_argv[@]}" -o "$cell/guest" 2> "$cell/cc.err"
  rc=$?
  printf '%s\n' "$rc" > "$cell/cc.rc"
  if [ "$rc" -ne 0 ]; then
    printf 'BUILD_FAIL %s rc=%s\n' "$id" "$rc"
    failed=$((failed + 1))
  fi
done < "$CORPUS"

printf 'selected_c=%s build_failed=%s\n' "$selected" "$failed"
exit "$failed"
