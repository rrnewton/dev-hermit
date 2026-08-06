#!/usr/bin/env bash
# Bracketed (Proxy-Binding) test for the single-writer REPAIR/SYNC mode of
# scripts/allocate-worktree.rs --repair.
#
# The mode reconciles the recorded {product}_branch cells in worktree-state.json
# (and the managed ACTIVE.md block) FROM physical submodule porcelain, WITHOUT
# ever running a git branch/checkout/delete. This test brackets that guarantee
# from both sides:
#
#   POSITIVE (mechanism fires): plant a slot whose recorded branch DISAGREES with
#     the physical checkout; run --repair; assert the verifier now reports
#     0 drift AND ACTIVE.md was rewritten to the physical branch.
#   NEGATIVE (mechanism is not destructive): assert that after repair the
#     UNPUSHED local branch that was recorded-but-not-checked-out STILL RESOLVES
#     (git rev-parse succeeds) — repair rewrote a string, it did not delete a ref.
#   CONTROL: a slot already in agreement is left byte-for-byte unchanged.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
root="$(mktemp -d "${TMPDIR:-/tmp}/allocate-repair-test.XXXXXX")"
trap 'rm -rf "$root"' EXIT

# dev-hermit root shape the scripts' find_root() looks for.
mkdir -p "$root/scripts" "$root/hermit" "$root/reverie" "$root/liteinst2" "$root/worktrees"
touch "$root/.gitmodules"
cp "$script_dir/allocate-worktree.rs" "$root/scripts/"
cp "$script_dir/check-worktree-registry.rs" "$root/scripts/"

new_repo() { # dir  checked-out-branch  [extra-local-branch]
  local dir=$1 branch=$2 extra=${3:-}
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.email test@example.invalid
  git -C "$dir" config user.name test
  touch "$dir/seed"; git -C "$dir" add seed; git -C "$dir" commit -q -m seed
  [[ -n $extra ]] && git -C "$dir" branch "$extra"   # local ref, never checked out
  git -C "$dir" switch -q -c "$branch"
}

# drift70:  physical hermit on physical-70, but state records an UNPUSHED local
#           branch stale-unpushed-70 that exists but is not checked out (the
#           slot-250 shape: repurposed slot, stale recorded branch).
# ok71:     physical == recorded (correct control; must stay untouched).
new_repo "$root/worktrees/drift70/hermit" "physical-70" "stale-unpushed-70"
new_repo "$root/worktrees/ok71/hermit"    "correct-71"

cat >"$root/worktree-state.json" <<'JSON'
{
  "version": 3,
  "slots": {
    "drift70": {
      "agents": [{"name":"agent-70","read_only":false}],
      "hermit_branch":"stale-unpushed-70", "hermit_path":"worktrees/drift70/hermit",
      "reverie_branch":"-", "reverie_path":"worktrees/drift70/reverie",
      "liteinst2_branch":"-", "liteinst2_path":"worktrees/drift70/liteinst2",
      "task":"task-70", "status":"active"
    },
    "ok71": {
      "agents": [{"name":"agent-71","read_only":false}],
      "hermit_branch":"correct-71", "hermit_path":"worktrees/ok71/hermit",
      "reverie_branch":"-", "reverie_path":"worktrees/ok71/reverie",
      "liteinst2_branch":"-", "liteinst2_path":"worktrees/ok71/liteinst2",
      "task":"task-71", "status":"active"
    }
  }
}
JSON

cat >"$root/worktrees/ACTIVE.md" <<'MD'
# Active Worktrees

Human notes above the managed block must survive verbatim.
<!-- BEGIN worktree-state (managed by scripts/allocate-worktree.rs; do not edit inside) -->
| Slot | Agent | HermitBranch | ReverieBranch | LiteInst2Branch | Task | Status | ReadOnly |
| --- | --- | --- | --- | --- | --- | --- | --- |
| drift70 | agent-70 | stale-unpushed-70 | - | - | task-70 | active | no |
| ok71 | agent-71 | correct-71 | - | - | task-71 | active | no |
<!-- END worktree-state -->

Human notes below must survive too.
MD

# --- Pre-state: verifier must SEE the planted drift (mechanism has something to fix).
if "$root/scripts/check-worktree-registry.rs" --root "$root" >"$root/pre.out" 2>&1; then
  echo "FAIL: verifier saw no drift before repair (test fixture is inert)" >&2; exit 1
fi
grep -Fq 'DRIFT slot=drift70 hermit recorded=stale-unpushed-70 actual=physical-70' "$root/pre.out" \
  || { echo "FAIL: expected drift70 hermit drift not reported" >&2; cat "$root/pre.out" >&2; exit 1; }
grep -Fq 'DRIFT slot=ok71' "$root/pre.out" \
  && { echo "FAIL: correct control ok71 was flagged before repair" >&2; exit 1; }

# --- POSITIVE: run the reconciler; it must FIRE and drive drift to zero.
(cd "$root" && "$root/scripts/allocate-worktree.rs" --repair) >"$root/repair.out" 2>&1 \
  || { echo "FAIL: --repair exited nonzero" >&2; cat "$root/repair.out" >&2; exit 1; }
grep -Fq "reconcile drift70/hermit: 'stale-unpushed-70' -> 'physical-70'" "$root/repair.out" \
  || { echo "FAIL: repair did not reconcile drift70 hermit" >&2; cat "$root/repair.out" >&2; exit 1; }

# Verifier is now clean (mechanism actually fixed the recorded cells).
"$root/scripts/check-worktree-registry.rs" --root "$root" >"$root/post.out" 2>&1 \
  || { echo "FAIL: drift remains after repair" >&2; cat "$root/post.out" >&2; exit 1; }
grep -Fq 'PASS rows=2 correct_rows=2 drift_rows=0 product_cells=6 drift_cells=0' "$root/post.out" \
  || { echo "FAIL: post-repair verifier not fully clean" >&2; cat "$root/post.out" >&2; exit 1; }

# ACTIVE.md managed row now reflects the physical branch...
grep -Fq '| drift70 | agent-70 | physical-70 |' "$root/worktrees/ACTIVE.md" \
  || { echo "FAIL: ACTIVE.md not rewritten to physical branch" >&2; exit 1; }
# ...and human content outside the markers survived.
grep -Fq 'Human notes above the managed block must survive verbatim.' "$root/worktrees/ACTIVE.md" \
  || { echo "FAIL: human content above managed block was lost" >&2; exit 1; }
grep -Fq 'Human notes below must survive too.' "$root/worktrees/ACTIVE.md" \
  || { echo "FAIL: human content below managed block was lost" >&2; exit 1; }

# --- NEGATIVE: repair must NOT be destructive. The unpushed local branch that
#     used to be the recorded value must STILL EXIST (repair rewrote a string in
#     the registry, it never touched a git ref).
git -C "$root/worktrees/drift70/hermit" rev-parse --verify --quiet stale-unpushed-70 >/dev/null \
  || { echo "FAIL: repair DELETED the unpushed local branch stale-unpushed-70" >&2; exit 1; }

# --- CONTROL: the already-correct slot's recorded value is unchanged.
grep -Fq '"hermit_branch": "correct-71"' "$root/worktree-state.json" \
  || { echo "FAIL: correct control ok71 recorded branch was mutated" >&2; exit 1; }

# --- IDEMPOTENCE: a second repair reports zero changes.
(cd "$root" && "$root/scripts/allocate-worktree.rs" --repair) >"$root/repair2.out" 2>&1
grep -Fq 'repair: 0 branch cells needed reconciliation' "$root/repair2.out" \
  || { echo "FAIL: repair is not idempotent" >&2; cat "$root/repair2.out" >&2; exit 1; }

# --- RELEASE-JOURNAL ISOLATION: a fenced checkout is intentionally absent at
# its canonical path. Neither repair nor ordinary re-adoption may translate
# that transient absence into branch="-" or status=active.
fenced="$root/worktrees/ok71/.hermit.release-worktree-123-456"
mv "$root/worktrees/ok71/hermit" "$fenced"
python3 - "$root/worktree-state.json" "$fenced" <<'PY'
import json
import pathlib
import sys

state_path = pathlib.Path(sys.argv[1])
state = json.loads(state_path.read_text())
record = state["slots"]["ok71"]
record["status"] = "releasing"
record["release_journal"] = {
    "schema_version": 1,
    "label": "hermit",
    "original": str(state_path.parent / "worktrees/ok71/hermit"),
    "fenced": sys.argv[2],
}
state_path.write_text(json.dumps(state, indent=2) + "\n")
PY
journal_hash=$(sha256sum "$root/worktree-state.json")
if (cd "$root" && "$root/scripts/allocate-worktree.rs" --repair) \
    >"$root/releasing-repair.out" 2>&1; then
  echo 'FAIL: repair consumed an unfinished release journal' >&2; exit 1
fi
grep -Fq 'REFUSING repair while slot release transaction(s) require guarded recovery: ok71' \
  "$root/releasing-repair.out" \
  || { echo 'FAIL: repair did not identify the journaled slot' >&2; cat "$root/releasing-repair.out" >&2; exit 1; }
[[ $(sha256sum "$root/worktree-state.json") == "$journal_hash" ]] \
  || { echo 'FAIL: refused repair mutated the release journal' >&2; exit 1; }

if (cd "$root" && "$root/scripts/allocate-worktree.rs" \
    --agent agent-71 --slot ok71 --task task-71 --product hermit) \
    >"$root/releasing-adopt.out" 2>&1; then
  echo 'FAIL: re-adoption consumed an unfinished release journal' >&2; exit 1
fi
grep -Fq 'slot ok71 has an unfinished release transaction' "$root/releasing-adopt.out" \
  || { echo 'FAIL: re-adoption did not refuse the journaled slot' >&2; cat "$root/releasing-adopt.out" >&2; exit 1; }
[[ $(sha256sum "$root/worktree-state.json") == "$journal_hash" ]] \
  || { echo 'FAIL: refused re-adoption mutated the release journal' >&2; exit 1; }

echo "allocate-worktree-repair-test: PASS (POSITIVE: 1 planted drift reconciled -> 0 drift; NEGATIVE: unpushed branch ref preserved; CONTROL: 1/1 correct row untouched; release journal isolated from repair/re-adoption; human content preserved; idempotent)"
