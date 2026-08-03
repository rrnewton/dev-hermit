#!/usr/bin/env bash
# build-seq.sh <build-worktree> <sha>...  — reuse ONE seeded worktree, checkout each
# SHA (detached) and build incrementally (avoids the jailer-blocked full-target cp).
# Copies each test binary to ignored/bins/<sha>. Appends status to results/seq-builds.txt.
set -uo pipefail
EXP="$(cd "$(dirname "$0")" && pwd)"
WT="$EXP/$1"; shift
OUT="$EXP/ignored/results/seq-builds.txt"; mkdir -p "$EXP/ignored/bins" "$EXP/ignored/results"
for SHA in "$@"; do
  if [ -f "$EXP/ignored/bins/$SHA" ]; then echo "SKIP $SHA (bin exists)" | tee -a "$OUT"; continue; fi
  git -C "$WT" checkout --detach "$SHA" >/dev/null 2>&1 || { echo "CO_FAIL $SHA" | tee -a "$OUT"; continue; }
  b0=$(date +%s)
  if ! ( cd "$WT" && with-proxy cargo test -p detcore --test tests_misc --no-run ) >"$EXP/ignored/logs/seq-$SHA.log" 2>&1; then
    echo "BUILD_FAIL $SHA $(( $(date +%s)-b0 ))s" | tee -a "$OUT"; continue
  fi
  BIN="$(ls -t "$WT"/target/debug/deps/tests_misc-* 2>/dev/null | grep -v '\.d$' | head -1)"
  if [ -z "$BIN" ] || ! "$BIN" --list 2>/dev/null | grep -q vfork_parent_resumes_after_child_exec; then
    echo "NOBIN/NOTEST $SHA" | tee -a "$OUT"; continue
  fi
  cp -f "$BIN" "$EXP/ignored/bins/$SHA"
  rev=$(git -C "$WT" show "$SHA:Cargo.lock" | grep -o 'reverie.git?rev=[0-9a-f]*' | head -1)
  echo "BUILT $SHA $(( $(date +%s)-b0 ))s $rev" | tee -a "$OUT"
done
echo "SEQ DONE $(date +%T)" | tee -a "$OUT"
