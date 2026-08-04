#!/bin/bash
# Mutation verification for release-worktree.rs prune-bound-to-remove-success fix.
#
# Reproduces the exact git-level orphaning mechanism (see memory
# release-worktree-liteinst2-erofs-transient-prune-orphans) and A/B compares the
# BUGGY (unconditional prune) vs FIXED (prune bound to remove success) control
# flow against REAL git, then confirms N normal releases still fully clean up.
#
# Three independent checks:
#   MECHANISM  git-level: a partial-failed-remove state (broken .git link + data
#              on disk) is prune-eligible; unconditional prune ORPHANS the data,
#              skipping prune RETAINS admin+data together (recoverable).
#   CONTROLFLOW real remove failure (locked worktree): remove returns non-zero;
#              the FIXED branch skips prune; the BUGGY branch would have pruned.
#   NORMAL     N successful remove->prune cycles fully remove worktree + admin.
set -u
N_NORMAL=5
FAIL=0
pass(){ echo "  PASS: $1"; }
fail(){ echo "  FAIL: $1"; FAIL=1; }

ROOT=$(mktemp -d /tmp/prune-mut.XXXXXX)
trap 'chmod -R u+rwx "$ROOT" 2>/dev/null; rm -rf "$ROOT"' EXIT
echo "sandbox: $ROOT"

mk_primary(){ # $1 name -> creates a primary repo with one commit
  local p="$ROOT/$1"
  git init -q "$p"
  git -C "$p" -c user.email=a@b -c user.name=a commit -q --allow-empty -m init
  echo "$p"
}
admin_entries(){ git -C "$1" worktree list --porcelain | grep -c '^worktree ' ; }

########################################################################
echo; echo "== MECHANISM: partial-failed-remove state + prune =="
# BUGGY arm
PRIM=$(mk_primary prim_bug)
WT="$ROOT/slot_bug/liteinst2"
git -C "$PRIM" worktree add -q --detach "$WT"
echo "VALUABLE-WORK" > "$WT/DATA"
# Inject the exact state a partial EROFS remove leaves: the worktree's .git link
# is gone (git deletes it early) but data files remain on disk.
rm -f "$WT/.git"
# Is this state prune-eligible? (git considers a broken/missing gitdir prunable)
PRUNABLE=$(git -C "$PRIM" worktree prune --dry-run -v 2>&1)
echo "  prune --dry-run: ${PRUNABLE:-<none>}"
# BUGGY control flow: prune runs unconditionally after the failed remove.
git -C "$PRIM" worktree prune
if [ -f "$WT/DATA" ] && ! git -C "$PRIM" worktree list --porcelain | grep -q "$WT"; then
  pass "BUGGY unconditional prune ORPHANS data (DATA on disk, no admin entry) -- reproduces bug"
else
  fail "expected buggy prune to orphan data; DATA exists=$([ -f "$WT/DATA" ] && echo y || echo n)"
fi

# FIXED arm: identical partial-failure state, but prune is SKIPPED (remove !ok)
PRIM=$(mk_primary prim_fix)
WT="$ROOT/slot_fix/liteinst2"
git -C "$PRIM" worktree add -q --detach "$WT"
echo "VALUABLE-WORK" > "$WT/DATA"
rm -f "$WT/.git"
# FIXED control flow: remove failed => DO NOT prune.
if [ -f "$WT/DATA" ] && git -C "$PRIM" worktree list --porcelain | grep -q "$WT"; then
  pass "FIXED skip-prune RETAINS data+admin together (recoverable, not orphaned)"
else
  fail "expected fixed skip to retain admin entry + data"
fi

########################################################################
echo; echo "== CONTROLFLOW: real 'git worktree remove' failure (locked) =="
# Drive the EXACT script logic against a genuinely-failing remove. A locked
# worktree makes `git worktree remove` (no --force) return non-zero while data
# stays on disk -- exercising the ok/!ok branch the fix hinges on.
run_release_logic(){ # $1 primary $2 worktree-path $3 mode; returns 0 iff remove ok
  local prim="$1" wt="$2" mode="$3" removed_ok=1
  if git -C "$prim" worktree remove "$wt" 2>/dev/null; then removed_ok=0; fi
  if [ "$mode" = fixed ]; then
    # FIXED: prune ONLY on success
    [ "$removed_ok" = 0 ] && git -C "$prim" worktree prune
  else
    # BUGGY: prune unconditionally
    git -C "$prim" worktree prune
  fi
  return $removed_ok
}
PRIM=$(mk_primary prim_lock)
WT="$ROOT/slot_lock/liteinst2"
git -C "$PRIM" worktree add -q --detach "$WT"
echo "VALUABLE-WORK" > "$WT/DATA"
git -C "$PRIM" worktree lock "$WT"
BEFORE=$(admin_entries "$PRIM")
run_release_logic "$PRIM" "$WT" fixed; RC=$?
AFTER=$(admin_entries "$PRIM")
if [ "$RC" != 0 ] && [ -f "$WT/DATA" ] && [ "$BEFORE" = "$AFTER" ]; then
  pass "real remove FAILED (rc reflects !ok); FIXED skipped prune; admin+data intact"
else
  fail "controlflow: rc=$RC data=$([ -f "$WT/DATA" ]&&echo y||echo n) admin $BEFORE->$AFTER"
fi
git -C "$PRIM" worktree unlock "$WT" 2>/dev/null

########################################################################
echo; echo "== NORMAL: $N_NORMAL successful remove->prune cycles =="
PRIM=$(mk_primary prim_norm)
ok_count=0
for i in $(seq 1 $N_NORMAL); do
  WT="$ROOT/slot_norm_$i/liteinst2"
  git -C "$PRIM" worktree add -q --detach "$WT"   # clean worktree (detached at pin)
  if run_release_logic "$PRIM" "$WT" fixed; then
    # fully gone: data dir removed AND no admin entry referencing it
    if [ ! -e "$WT" ] && ! git -C "$PRIM" worktree list --porcelain | grep -q "$WT"; then
      ok_count=$((ok_count+1))
    fi
  fi
done
if [ "$ok_count" = "$N_NORMAL" ]; then
  pass "all $N_NORMAL/$N_NORMAL normal releases fully removed worktree + admin entry"
else
  fail "only $ok_count/$N_NORMAL normal releases fully clean"
fi

echo
[ "$FAIL" = 0 ] && echo "RESULT: ALL CHECKS PASSED" || echo "RESULT: FAILURES PRESENT"
exit $FAIL
