#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
root="$(mktemp -d "${TMPDIR:-/tmp}/worktree-registry-test.XXXXXX")"
trap 'rm -rf "$root"' EXIT

# Make the fixture itself a parent Git checkout. A residue-only descendant can
# then reproduce the production bug where Git climbs to this parent `main`.
git -C "$root" init -q -b main
git -C "$root" config user.email test@example.invalid
git -C "$root" config user.name test
touch "$root/parent-seed"
git -C "$root" add parent-seed
git -C "$root" commit -q -m 'seed parent checkout'

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
"$script_dir/check-worktree-registry.rs" --root "$root" >"$pass" 2>&1 \
  || { cat "$pass" >&2; echo 'expected clean global registry to pass' >&2; exit 1; }
grep -Fq 'PASS rows=2 correct_rows=2 drift_rows=0 product_cells=6 drift_cells=0' "$pass"

# The established reconciliation front door must invoke the same predicate.
mkdir -p "$root/scripts" "$root/hermit" "$root/reverie" "$root/liteinst2"
touch "$root/.gitmodules"
cp "$script_dir/check-worktree-registry.rs" "$root/scripts/"
(cd "$root" && "$script_dir/allocate-worktree.rs" --check-only) \
  >"$root/check-only-pass.out" 2>&1
grep -Fq 'PASS rows=2 correct_rows=2 drift_rows=0 product_cells=6 drift_cells=0' \
  "$root/check-only-pass.out"

# The allocator must contend on the same canonical writer lock as release.
exec {registry_lock_fd}>"$root/worktree-state.lock"
flock "$registry_lock_fd"
(cd "$root" && "$script_dir/allocate-worktree.rs" --check-only) \
  >"$root/check-only-locked.out" 2>&1 && : >"$root/check-only-locked.done" &
locked_pid=$!
sleep 0.25
kill -0 "$locked_pid" 2>/dev/null \
  || { echo "allocate-worktree did not wait for the registry writer lock" >&2; exit 1; }
test ! -e "$root/check-only-locked.done" \
  || { echo "allocate-worktree crossed a held registry writer lock" >&2; exit 1; }
flock -u "$registry_lock_fd"
wait "$locked_pid"
test -e "$root/check-only-locked.done"
exec {registry_lock_fd}>&-

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

# LOCAL-SCOPE BRACKET: unrelated slot01 drift remains visible to the global
# report but cannot veto a slot02 operation. Selecting the drifting target must
# still refuse, and selecting a genuinely unused target must be a zero-row pass.
"$script_dir/check-worktree-registry.rs" --root "$root" --slot slot02 \
  >"$root/scoped-clean.out" 2>&1
grep -Fq 'PASS rows=1 correct_rows=1 drift_rows=0 product_cells=3 drift_cells=0' \
  "$root/scoped-clean.out"
if "$script_dir/check-worktree-registry.rs" --root "$root" --slot slot01 \
    >"$root/scoped-drift.out" 2>&1; then
  echo 'target-scoped verifier accepted drift in its own target' >&2
  exit 1
fi
grep -Fq 'DRIFT slot=slot01 hermit recorded=wrong-01 actual=correct-01' \
  "$root/scoped-drift.out"
"$script_dir/check-worktree-registry.rs" --root "$root" --slot unused03 \
  >"$root/scoped-unused.out" 2>&1
grep -Fq 'PASS rows=0 correct_rows=0 drift_rows=0 product_cells=0 drift_cells=0' \
  "$root/scoped-unused.out"

# FALSE-ASCENT NEGATIVE: the requested product directory has only generated
# residue and no .git. Git can see the fixture parent's `main`, but that parent
# identity must never satisfy the product row or become repair authority.
sed -i 's/wrong-01/correct-01/g' "$root/worktree-state.json" "$root/worktrees/ACTIVE.md"
mkdir -p "$root/worktrees/ascent/hermit/target"
printf 'generated\n' >"$root/worktrees/ascent/hermit/target/artifact"
python3 - "$root/worktree-state.json" "$root/worktrees/ACTIVE.md" <<'PY'
import json
import pathlib
import sys

state_path, active_path = map(pathlib.Path, sys.argv[1:])
state = json.loads(state_path.read_text())
state["slots"]["ascent"] = {
    "agents": [{"name": "agent-ascent", "read_only": False}],
    "hermit_branch": "main", "hermit_path": "worktrees/ascent/hermit",
    "reverie_branch": "-", "reverie_path": "worktrees/ascent/reverie",
    "liteinst2_branch": "-", "liteinst2_path": "worktrees/ascent/liteinst2",
    "task": "task-ascent", "status": "released",
}
state_path.write_text(json.dumps(state, indent=2) + "\n")
active = active_path.read_text()
row = "| ascent | agent-ascent | main | - | - | task-ascent | released | no |\n"
active_path.write_text(active.replace("<!-- END worktree-state -->", row + "<!-- END worktree-state -->"))
PY
if "$script_dir/check-worktree-registry.rs" --root "$root" >"$root/ascent.out" 2>&1; then
  echo 'expected residue-only descendant to fail exact-root binding' >&2
  exit 1
fi
grep -Fq "DRIFT slot=ascent hermit recorded=main actual=unreadable:git top-level $root does not equal requested checkout $root/worktrees/ascent/hermit" \
  "$root/ascent.out"
grep -Fq 'FAIL rows=3 correct_rows=2 drift_rows=1 product_cells=9 drift_cells=1' \
  "$root/ascent.out"

# PATH-ALIAS NEGATIVE: lexical aliases are not an alternate spelling of the
# registry authority, even when they canonicalize to the same checkout.
python3 - "$root/worktree-state.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
state = json.loads(path.read_text())
state["slots"]["slot02"]["hermit_path"] = "worktrees/slot02/./hermit"
path.write_text(json.dumps(state, indent=2) + "\n")
PY
if "$script_dir/check-worktree-registry.rs" --root "$root" >"$root/alias.out" 2>&1; then
  echo 'expected aliased registry path to fail exact path binding' >&2
  exit 1
fi
grep -Fq 'DRIFT slot=slot02 hermit path recorded=Some("worktrees/slot02/./hermit") expected=worktrees/slot02/hermit' \
  "$root/alias.out"
grep -Fq 'FAIL rows=3 correct_rows=1 drift_rows=2 product_cells=9 drift_cells=2' \
  "$root/alias.out"

echo "check-worktree-registry-test: PASS (global report preserved; local clean/unused targets accepted despite unrelated drift; local drifting target refused; parent-ascent/path-alias negatives preserved; writer lock serialized)"
