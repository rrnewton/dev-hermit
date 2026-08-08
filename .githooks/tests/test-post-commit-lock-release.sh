#!/usr/bin/env bash
# Bracket the post-commit rescue hook against the LOCK-LEAK it caused on
# 2026-08-08, using /proc/locks as the observable.
#
# THE BUG: an flock belongs to the OPEN FILE DESCRIPTION, not to the process
# that took it. scripts/parent-main-write does `exec 9>LOCK; flock 9` and
# exports HERMIT_PARENT_MAIN_LOCK_FD=9. The hook backgrounded a long-running
# rescue that INHERITED fd 9, so the writer lock stayed held long after the
# commit was reported done -- four serialized writers queued behind a holder
# whose PID no longer existed.
#
# WHAT IS ASSERTED, and why each direction is needed:
#   POSITIVE  after the commit returns, the lock is IMMEDIATELY re-acquirable
#             even though the rescue child is still running. This is the fix.
#   A/B       the ORIGINAL hook is run through the identical harness and must
#             FAIL that assertion. Without this the positive proves nothing --
#             a harness that never reproduces the leak would pass on any hook.
#   CEILING   a rescue that hangs forever is terminated at its ceiling rather
#             than living until the box reboots.
#   PRESERVE  the hook still exits 0, still never fails a commit, still honours
#             HERMIT_SKIP_UNPUSHED_RESCUE, and still actually runs the rescue.
#
# Fully inert: its own temp repo, its own lock file, a stub rescue script. It
# never touches the real parent checkout, the real lock, tmux, or the network,
# and it signals only processes it started itself (Hard Invariant 15).
set -uo pipefail

hook_under_test="${1:-$(cd "$(dirname "$0")/.." && pwd)/post-commit}"
root="$(mktemp -d "${TMPDIR:-/tmp}/post-commit-lock-test.XXXXXX")"
trap 'rm -rf "$root"' EXIT

pass=0; fail=0
ok()  { printf '  PASS  %s\n' "$*"; pass=$((pass + 1)); }
bad() { printf '  FAIL  %s\n' "$*"; fail=$((fail + 1)); }

# ---------------------------------------------------------------- fixture ----
# A repo shaped enough for the hook: a git toplevel with the rescue script at
# the path the hook probes, plus ignored/ for its log.
build_fixture() { # <dir> <hook> <rescue-sleep-secs>
  local dir=$1 hook=$2 nap=$3
  mkdir -p "$dir/ci-hub/health" "$dir/ignored" "$dir/.githooks"
  cat >"$dir/ci-hub/health/unpushed_parent_commits.py" <<STUB
#!/usr/bin/env python3
# Stub rescue: stands in for the real herdr-run pushes. Records that it ran,
# then sleeps to model a slow remote so the lock window is observable.
import sys, time
open("$dir/ignored/rescue-ran", "a").write("ran " + " ".join(sys.argv[1:]) + "\n")
time.sleep($nap)
STUB
  chmod +x "$dir/ci-hub/health/unpushed_parent_commits.py"
  cp "$hook" "$dir/.githooks/post-commit"
  chmod +x "$dir/.githooks/post-commit"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.email t@example.invalid
  git -C "$dir" config user.name t
  git -C "$dir" config core.hooksPath .githooks
  : >"$dir/seed"
  git -C "$dir" add -- seed
}

# Commit while holding the writer lock exactly as parent-main-write does:
# fd 9 open on the lock file, flock taken, fd number exported. Then drop our own
# reference and POLL for release.
#
# Why poll rather than probe once: git and the hook shell take a fraction of a
# second to finish exiting, so a single probe at t=0 reports HELD even when
# nothing leaked. The real property is not "free at this instant", it is "freed
# long before the rescue finishes" -- the leak held it for the rescue's WHOLE
# duration. So we poll to a deadline that is far shorter than the rescue's
# runtime, which makes the two behaviours unambiguous rather than racy.
#
# Echoes: "<RELEASED@Ns|STILL-HELD> <commit-rc>"
commit_under_lock() { # <dir> <lockfile> <deadline-secs> [extra-env...]
  local dir=$1 lock=$2 deadline=$3; shift 3
  env "$@" \
    HERMIT_PARENT_MAIN_LOCK_FD=9 \
    bash -c '
      dir=$1; lock=$2; deadline=$3; shift 3
      exec 9>"$lock"
      flock -n 9 || { echo "SETUP-FAIL 90"; exit 90; }
      git -C "$dir" commit -q -m "trigger the hook" >/dev/null 2>&1
      rc=$?
      # Release OUR reference. Any remaining hold is an inherited one.
      exec 9>&-
      # TWO INDEPENDENT OBSERVABLES, both required to call it released:
      #   (a) a non-blocking flock retry succeeds, and
      #   (b) /proc/locks carries NO entry for this file'"'"'s inode.
      # (b) is the kernel'"'"'s own view, keyed on INODE rather than on any
      # process name or pid -- an inherited fd is held by an open file
      # description whose recorded owner pid may already be gone, so a
      # name-based or pid-based check cannot see it. (a) alone could in
      # principle race; (b) alone cannot distinguish "free" from "file gone".
      ino=$(stat -c %i "$lock")
      i=0
      while [ "$i" -le "$deadline" ]; do
        held=$(grep -cw "$ino" /proc/locks)
        if [ "$held" -eq 0 ] && flock -n "$lock" -c true 2>/dev/null; then
          echo "RELEASED@${i}s $rc"; exit 0
        fi
        i=$((i + 1)); sleep 1
      done
      echo "STILL-HELD@inode=$ino,proclocks=$(grep -cw "$ino" /proc/locks) $rc"
    ' _ "$dir" "$lock" "$deadline"
}

# Reap anything this test started that is still alive, by PID only.
reap() { # <dir>
  local p
  for p in $(cat "$1/ignored/pids" 2>/dev/null); do
    [ -d "/proc/$p" ] && kill "$p" 2>/dev/null
  done
}

# ======================================================= POSITIVE: fixed hook ==
echo "CASE 1  fixed hook: writer lock must be free the moment the commit returns"
fx1="$root/fixed"
build_fixture "$fx1" "$hook_under_test" 25
result=$(commit_under_lock "$fx1" "$root/lock1" 6)
state=${result%% *}; rc=${result##* }
echo "         observed: $result"
if [[ "$state" == RELEASED@* ]]; then
  ok "C1 lock RELEASED ${state#RELEASED@} after commit -- 0 /proc/locks entries for the inode AND flock re-acquired, while the 25s rescue still runs"
elif [[ "$state" == STILL-HELD* ]]; then
  bad "C1 lock STILL HELD after 6s by an inherited fd -- this is the 2026-08-08 wedge"
else
  bad "C1 harness could not take the lock: $result"
fi
[[ "$rc" == 0 ]] && ok "C1 commit still succeeded (rc=0)" \
                 || bad "C1 commit rc=$rc -- the hook must never fail a commit"
# The rescue must genuinely still be running, or 'released' is vacuous.
sleep 0.5
if grep -q "^ran" "$fx1/ignored/rescue-ran" 2>/dev/null; then
  ok "C1 rescue DID run (lock release is not just a skipped rescue)"
else
  bad "C1 rescue never ran -- lock release proves nothing"
fi
echo

# ============================================== A/B: the ORIGINAL hook leaks ==
# Reconstruct the pre-fix hook body. If this does NOT reproduce the leak, the
# harness cannot see the bug and CASE 1 is not evidence.
echo "CASE 2  A/B control: the ORIGINAL (pre-fix) hook must reproduce the leak"
legacy="$root/legacy-post-commit"
cat >"$legacy" <<'LEGACY'
#!/bin/sh
[ -n "$HERMIT_SKIP_UNPUSHED_RESCUE" ] && exit 0
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -x "$root/ci-hub/health/unpushed_parent_commits.py" ] || exit 0
( "$root/ci-hub/health/unpushed_parent_commits.py" --scope all --rescue \
    >> "$root/ignored/unpushed-parent-rescue.log" 2>&1 & ) >/dev/null 2>&1
exit 0
LEGACY
chmod +x "$legacy"
fx2="$root/legacy"
build_fixture "$fx2" "$legacy" 25
result2=$(commit_under_lock "$fx2" "$root/lock2" 6)
echo "         observed: $result2"
if [[ "${result2%% *}" == STILL-HELD* ]]; then
  ok "C2 original hook STILL holds it at 6s (${result2%% *}) -- leak reproduced in kernel state, harness can see the defect"
else
  bad "C2 original hook released at ${result2%% *}; harness cannot reproduce the leak, so C1 is not evidence"
fi
echo

# ================================================== CEILING: bounded mutation ==
echo "CASE 3  hard ceiling: an unbounded rescue is terminated, not left forever"
fx3="$root/ceiling"
build_fixture "$fx3" "$hook_under_test" 3600   # models a hung push
HERMIT_UNPUSHED_RESCUE_CEILING_SECS=2 \
  git -C "$fx3" commit -q -m "trigger" >/dev/null 2>&1
rc3=$?
[[ "$rc3" == 0 ]] && ok "C3 commit still succeeded (rc=0)" || bad "C3 commit rc=$rc3"
# Find the stub we just launched, by matching OUR fixture path only, and record
# its pid so reap() can clean up. We never pattern-kill.
sleep 1
mine=$(pgrep -f "$fx3/ci-hub/health/unpushed_parent_commits.py" 2>/dev/null | head -1)
echo "$mine" >"$fx3/ignored/pids"
if [[ -n "$mine" ]]; then
  ok "C3 rescue is running under the ceiling (pid $mine, ours by fixture path)"
  sleep 4   # ceiling 2s + margin
  if [[ -d "/proc/$mine" ]]; then
    bad "C3 rescue SURVIVED its 2s ceiling -- mutation is still unbounded"
    reap "$fx3"
  else
    ok "C3 rescue terminated at its ceiling (unbounded mutation is now bounded)"
  fi
else
  bad "C3 could not observe the rescue process; ceiling unverified"
fi
echo

# ================================================== PRESERVE: opt-out still works ==
echo "CASE 4  preserved behaviour: HERMIT_SKIP_UNPUSHED_RESCUE=1 still opts out"
fx4="$root/skip"
build_fixture "$fx4" "$hook_under_test" 5
HERMIT_SKIP_UNPUSHED_RESCUE=1 git -C "$fx4" commit -q -m "trigger" >/dev/null 2>&1
rc4=$?
[[ "$rc4" == 0 ]] && ok "C4 commit succeeded (rc=0)" || bad "C4 commit rc=$rc4"
sleep 0.5
if [[ -f "$fx4/ignored/rescue-ran" ]]; then
  bad "C4 rescue ran despite the opt-out"
else
  ok "C4 rescue correctly skipped"
fi
echo

# ------------------------------------------------------------------ result ----
reap "$fx1"; reap "$fx2"; reap "$fx3"
echo "======================================================================"
echo "lock observable: poll a non-blocking flock after the committer drops fd 9;"
echo "discriminator: fixed hook frees it in seconds, legacy hook holds it for the whole 25s rescue"
echo "assertions: $pass passed, $fail failed"
if (( fail )); then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
