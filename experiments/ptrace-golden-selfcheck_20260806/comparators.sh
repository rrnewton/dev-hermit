#!/bin/bash
# Score each captured pair under THREE comparators, and control the two host-state
# inputs that N=300 showed leaking into the reference log.
#
# N=300 produced 3 divergent pairs in 3300, from TWO distinct causes, neither of
# which is guest nondeterminism:
#
#   echo-hello    1/300  pure REORDERING of the startup banner (line multisets equal)
#   fork-pipeline 2/300  GENUINE content diff: the guest stats "." and "$HOME" and the
#                        DIRECTORY SIZES CHANGED between the two runs, because 18 agents
#                        share this box (and because this script's own directory was
#                        being written to during the sweep).
#
# In all three, the COMMIT stream was byte-identical.
#
# Comparators:
#   WHOLE   whole timestamp-stripped log. What the golden uses today.
#   STREAM  DETLOG + COMMIT lines only (drops the startup banner region).
#   COMMIT  COMMIT-turn lines only (pure scheduling decisions).
#
# Controls: CWD (run from a quiescent directory nobody else writes) and EXTRA
# (--base-env minimal, which is the flag set the PINNED goldens used and may be why
# Set B never flaked).
set -u
BIN="${BIN:?set BIN}"
N="${N:-400}"
EXTRA="${EXTRA:-}"
TAG="${TAG:-cmp}"
RUNCWD="${RUNCWD:-/home/newton/work/dev-hermit/ignored/w2-selfcheck-deepen/quiet}"
D=/home/newton/work/dev-hermit/ignored/w2-selfcheck-deepen
W=$D/cmp-$TAG.d; mkdir -p "$W" "$RUNCWD"
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}:/home/newton/.local/hermit-deps/lu/usr/lib64

norm()   { sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z[[:space:]]*//' "$1"; }
stream() { grep -E 'DETLOG|COMMIT turn' "$1"; }
commit() { grep 'COMMIT turn' "$1"; }

whole=0; strm=0; cmt=0; reorder=0; pairs=0
for i in $(seq 1 "$N"); do
  a="$W/a.log"; b="$W/b.log"
  ( cd "$RUNCWD" && timeout 180 "$BIN" --log info --log-file "$a" run --strict $EXTRA -- "$@" >/dev/null 2>&1 )
  ( cd "$RUNCWD" && timeout 180 "$BIN" --log info --log-file "$b" run --strict $EXTRA -- "$@" >/dev/null 2>&1 )
  [ -s "$a" ] && [ -s "$b" ] || { rm -f "$a" "$b"; continue; }
  pairs=$((pairs+1))
  norm "$a" > "$a.n"; norm "$b" > "$b.n"
  if ! cmp -s "$a.n" "$b.n"; then
    whole=$((whole+1))
    if cmp -s <(sort "$a.n") <(sort "$b.n"); then
      reorder=$((reorder+1))
    else
      cp "$a.n" "$W/CONTENT-pair$i-a.n"; cp "$b.n" "$W/CONTENT-pair$i-b.n"
    fi
  fi
  cmp -s <(stream "$a.n") <(stream "$b.n") || strm=$((strm+1))
  cmp -s <(commit "$a.n") <(commit "$b.n") || cmt=$((cmt+1))
  rm -f "$a" "$b" "$a.n" "$b.n"
done
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$TAG" "$pairs" "$whole" "$strm" "$cmt" "$reorder" >> "$D/comparators.tsv"
printf '%-26s pairs=%-5s WHOLE=%-4s STREAM=%-4s COMMIT=%-4s (of WHOLE, reorder-only=%s)\n' \
  "$TAG" "$pairs" "$whole" "$strm" "$cmt" "$reorder"
