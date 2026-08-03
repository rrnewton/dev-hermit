#!/usr/bin/env bash
# Auto-union rebase driver for the e2e-manifest PR bucket.
#
# For one PR branch: rebase onto current origin/main; resolve conflicts ONLY on
# managed append-only registries via scripts/e2e-union-resolve.py (pure additive
# union); regenerate the derived ci/expected-e2e-plan.json from the merged
# manifests; run the symmetry lint (hermit-manifest-plan) + harness audit to
# prove the union stayed self-consistent. Any conflict outside the managed set,
# or any non-additive collision, ABORTS the rebase and reports HUMAN (never
# hand-edits semantics, never drops a row).
#
# Usage: e2e-union-rebase.sh <hermit-worktree> <pr-branch> [--push]
# Prints one status line: CLEAN|UNIONED|HUMAN:<reason>|LINT-FAIL|REBASE-FAIL
set -uo pipefail

WT=${1:?hermit worktree path}
BR=${2:?pr branch (e.g. codex/e2e-foo)}
PUSH=${3:-}
RESOLVER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/e2e-union-resolve.py"
cd "$WT" || exit 9

MANAGED_PLAN="ci/expected-e2e-plan.json"
status() { echo "RESULT $BR $1"; }
cleanup_fail() { git rebase --abort >/dev/null 2>&1; git checkout -q codex/e2e-manifest-union-driver 2>/dev/null; git branch -D _union_wip >/dev/null 2>&1; }

with-proxy git fetch -q origin main "$BR" 2>/dev/null || { status "REBASE-FAIL:fetch"; exit 0; }
git branch -D _union_wip >/dev/null 2>&1
git checkout -q -B _union_wip "origin/$BR" || { status "REBASE-FAIL:checkout"; exit 0; }

UNIONED=0
GIT_EDITOR=true git rebase origin/main >/dev/null 2>&1
rc=$?
while [ $rc -ne 0 ]; do
  # collect unmerged paths
  mapfile -t U < <(git diff --name-only --diff-filter=U)
  if [ ${#U[@]} -eq 0 ]; then
    # rebase stopped for a non-conflict reason
    cleanup_fail; status "REBASE-FAIL:stopped-no-conflict"; exit 0
  fi
  for f in "${U[@]}"; do
    if [ "$f" = "$MANAGED_PLAN" ]; then
      # derived file: take main side now, regenerate after rebase completes
      git checkout -q --ours -- "$f" 2>/dev/null || git checkout -q --theirs -- "$f"
      git add -- "$f"; UNIONED=1; continue
    fi
    tmp=$(mktemp -d)
    git show ":1:$f" >"$tmp/base" 2>/dev/null || : >"$tmp/base"
    git show ":2:$f" >"$tmp/ours" 2>/dev/null || : >"$tmp/ours"
    git show ":3:$f" >"$tmp/theirs" 2>/dev/null || : >"$tmp/theirs"
    python3 "$RESOLVER" "$f" "$tmp/base" "$tmp/ours" "$tmp/theirs" "$tmp/out"
    prc=$?
    if [ $prc -ne 0 ]; then
      rm -rf "$tmp"; cleanup_fail
      case $prc in
        3) status "HUMAN:non-additive:$f" ;;
        4) status "HUMAN:unmanaged:$f" ;;
        *) status "HUMAN:resolve-err:$f" ;;
      esac
      exit 0
    fi
    cp "$tmp/out" "$f"; git add -- "$f"; rm -rf "$tmp"; UNIONED=1
  done
  GIT_EDITOR=true git rebase --continue >/dev/null 2>&1
  rc=$?
done

# --- regenerate derived plan from merged manifests, amend if changed ---
if ! ./ci/test_harness.sh plan --format json >/tmp/_uplan.$$ 2>/dev/null; then
  cleanup_fail; status "LINT-FAIL:plan-emit"; exit 0
fi
jq -S '{schema:1, cells:(.|sort_by(.category,.test,.mode,.backend))}' /tmp/_uplan.$$ >"$MANAGED_PLAN"
rm -f /tmp/_uplan.$$
if ! git diff --quiet -- "$MANAGED_PLAN"; then
  git add -- "$MANAGED_PLAN"
  GIT_EDITOR=true git commit -q --amend --no-edit
  UNIONED=1
fi

# --- prove self-consistency: symmetry lint + ratchet (the real CI gate) ---
# `hermit-manifest-plan --format harness-json` enforces the #1518 symmetry lint,
# the exact asymmetric/private-file ratchet, TOML schema, and backend partition.
# `test_harness.sh plan` (used above for regen) additionally proves cell
# enumeration succeeds, which is what ci-portable.yml runs. audit-inventory is
# intentionally NOT gated here: it is a local-only check already failing on main
# (untracked retarget_to_manifest.py has no inventory row) and is orthogonal to
# the union.
if ! cargo run --quiet -p hermit-manifest-plan -- --format harness-json >/dev/null 2>&1; then
  cleanup_fail; status "LINT-FAIL:symmetry"; exit 0
fi

if [ "$PUSH" = "--push" ]; then
  if ! with-proxy git push -q --force-with-lease "origin" "HEAD:$BR"; then
    status "REBASE-FAIL:push"; git checkout -q codex/e2e-manifest-union-driver; exit 0
  fi
fi

git checkout -q codex/e2e-manifest-union-driver
git branch -D _union_wip >/dev/null 2>&1
[ $UNIONED -eq 1 ] && status "UNIONED" || status "CLEAN"
