#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
root="$(mktemp -d "${TMPDIR:-/tmp}/worktree-registry-test.XXXXXX")"
trap 'rm -rf "$root"' EXIT

mkdir -p "$root/worktrees"
for slot in slot01 slot02; do
  mkdir -p "$root/worktrees/$slot/hermit"
  git -C "$root/worktrees/$slot/hermit" init -q -b main
  git -C "$root/worktrees/$slot/hermit" config user.email test@example.invalid
  git -C "$root/worktrees/$slot/hermit" config user.name test
  touch "$root/worktrees/$slot/hermit/seed"
  git -C "$root/worktrees/$slot/hermit" add seed
  git -C "$root/worktrees/$slot/hermit" commit -q -m seed
  git -C "$root/worktrees/$slot/hermit" switch -q -c "correct-${slot#slot}"
done

cat >"$root/worktree-state.json" <<'JSON'
{
  "version": 3,
  "slots": {
    "slot01": {
      "agents": [{"name":"agent-01","read_only":false}],
      "hermit_branch":"correct-01", "hermit_path":"worktrees/slot01/hermit",
      "reverie_branch":"-", "reverie_path":"worktrees/slot01/reverie",
      "liteinst2_branch":"-", "liteinst2_path":"worktrees/slot01/liteinst2",
      "task":"task-01", "status":"active"
    },
    "slot02": {
      "agents": [{"name":"agent-02","read_only":false}],
      "hermit_branch":"correct-02", "hermit_path":"worktrees/slot02/hermit",
      "reverie_branch":"-", "reverie_path":"worktrees/slot02/reverie",
      "liteinst2_branch":"-", "liteinst2_path":"worktrees/slot02/liteinst2",
      "task":"task-02", "status":"active"
    }
  }
}
JSON

cat >"$root/worktrees/ACTIVE.md" <<'MD'
# Active Worktrees
<!-- BEGIN worktree-state (managed by scripts/allocate-worktree.rs; do not edit inside) -->
| Slot | Agent | HermitBranch | ReverieBranch | LiteInst2Branch | Task | Status | ReadOnly |
| --- | --- | --- | --- | --- | --- | --- | --- |
| slot01 | agent-01 | correct-01 | - | - | task-01 | active | no |
| slot02 | agent-02 | correct-02 | - | - | task-02 | active | no |
<!-- END worktree-state -->
MD

pass="$root/pass.out"
"$script_dir/check-worktree-registry.rs" --root "$root" >"$pass" 2>&1
grep -Fq 'PASS rows=2 correct_rows=2 drift_rows=0 product_cells=6 drift_cells=0' "$pass"

# The established reconciliation front door must invoke the same predicate.
mkdir -p "$root/scripts" "$root/hermit" "$root/reverie" "$root/liteinst2"
touch "$root/.gitmodules"
cp "$script_dir/check-worktree-registry.rs" "$root/scripts/"
(cd "$root" && "$script_dir/allocate-worktree.rs" --check-only) \
  >"$root/check-only-pass.out" 2>&1
grep -Fq 'PASS rows=2 correct_rows=2 drift_rows=0 product_cells=6 drift_cells=0' \
  "$root/check-only-pass.out"

sed -i 's/correct-01/wrong-01/g' "$root/worktree-state.json" "$root/worktrees/ACTIVE.md"
fail="$root/fail.out"
if "$script_dir/check-worktree-registry.rs" --root "$root" >"$fail" 2>&1; then
  echo "expected planted branch drift to fail" >&2
  exit 1
fi
grep -Fq 'DRIFT slot=slot01 hermit recorded=wrong-01 actual=correct-01' "$fail"
grep -Fq 'FAIL rows=2 correct_rows=1 drift_rows=1 product_cells=6 drift_cells=1' "$fail"
if grep -Fq 'DRIFT slot=slot02' "$fail"; then
  echo "correct control row slot02 was falsely flagged" >&2
  exit 1
fi
if (cd "$root" && "$script_dir/allocate-worktree.rs" --check-only) \
  >"$root/check-only-fail.out" 2>&1; then
  echo "allocate-worktree --check-only failed to propagate planted drift" >&2
  exit 1
fi
grep -Fq 'DRIFT slot=slot01 hermit recorded=wrong-01 actual=correct-01' \
  "$root/check-only-fail.out"

echo "check-worktree-registry-test: PASS (2/2 correct accepted; 1 planted drift reported; 1/1 remaining correct row not flagged; --check-only propagates both outcomes)"
