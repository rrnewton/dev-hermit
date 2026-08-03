#!/usr/bin/env bash
# build-one.sh <sha> — create detached worktree under ignored/wt/<sha>, reflink-seed
# target/ from the primary head checkout (safe: only detcore+reverie recompile for
# `cargo test -p detcore --test tests_misc --no-run`; no DBI/DynamoRIO), build the
# test binary, and print "BUILT <sha> <short> <build_s> <binpath>" or a failure tag.
set -uo pipefail
EXP="$(cd "$(dirname "$0")" && pwd)"
HERMIT="$EXP/../../hermit"
SHA="$1"
WT="$EXP/ignored/wt/$SHA"
LOG="$EXP/ignored/logs/$SHA"; mkdir -p "$LOG"
if [ ! -d "$WT" ]; then
  git -C "$HERMIT" worktree add --detach "$WT" "$SHA" >>"$LOG/setup.log" 2>&1 || { echo "WT_FAIL $SHA"; exit 0; }
fi
[ ! -d "$WT/target" ] && cp -a --reflink=auto "$HERMIT/target" "$WT/target" 2>>"$LOG/setup.log"
SHORT="$(git -C "$WT" rev-parse --short HEAD)"
b0=$(date +%s)
if ! ( cd "$WT" && with-proxy cargo test -p detcore --test tests_misc --no-run ) >"$LOG/build.log" 2>&1; then
  echo "BUILD_FAIL $SHA $SHORT $(( $(date +%s)-b0 ))s (see $LOG/build.log)"; exit 0
fi
b1=$(date +%s)
BIN="$(ls -t "$WT"/target/debug/deps/tests_misc-* 2>/dev/null | grep -v '\.d$' | head -1)"
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then echo "NOBIN $SHA $SHORT"; exit 0; fi
if ! "$BIN" --list 2>/dev/null | grep -q 'vfork_parent_resumes_after_child_exec'; then echo "NOTEST $SHA $SHORT"; exit 0; fi
echo "BUILT $SHA $SHORT $((b1-b0))s $BIN"
