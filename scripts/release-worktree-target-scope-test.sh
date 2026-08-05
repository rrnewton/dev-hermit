#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
subject="$script_dir/release-worktree.rs"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/release-worktree-scope.XXXXXX")"
trap 'rm -rf -- "$test_root"' EXIT

fail() {
  echo "release-worktree-target-scope-test: FAIL: $*" >&2
  exit 1
}

init_root() {
  local root=$1
  mkdir -p "$root/scripts" "$root/worktrees" "$root/moved"
  : >"$root/.gitmodules"
  cp "$subject" "$root/scripts/release-worktree.rs"
  chmod +x "$root/scripts/release-worktree.rs"
  printf '%s\n' '# fixture ACTIVE' >"$root/worktrees/ACTIVE.md"
  for product in hermit reverie liteinst2; do
    git init -q "$root/$product"
    git -C "$root/$product" config user.email test@example.invalid
    git -C "$root/$product" config user.name test
    printf '%s\n' seed >"$root/$product/seed"
    git -C "$root/$product" add seed
    git -C "$root/$product" commit -qm seed
    git -C "$root/$product" branch -M main
  done
}

add_target() {
  local root=$1 product=$2 slot=$3
  mkdir -p "$root/worktrees/$slot"
  git -C "$root/$product" worktree add -q --detach \
    "$root/worktrees/$slot/$product" HEAD
}

add_missing_unrelated() {
  local root=$1 product=$2 name=$3
  local stale="$root/worktrees/$name-$product"
  git -C "$root/$product" worktree add -q --detach "$stale" HEAD
  mv "$stale" "$root/moved/$name-$product"
  printf '%s\n' "$stale"
}

assert_registered() {
  local root=$1 product=$2 path=$3
  git -C "$root/$product" worktree list --porcelain \
    | rg -Fxq "worktree $path" \
    || fail "$product registry lost unrelated entry $path"
}

write_state() {
  local root=$1 slot=$2 hbranch=$3 rbranch=$4 lbranch=$5
  local hpath=${6:-worktrees/$slot/hermit}
  local rpath=${7:-worktrees/$slot/reverie}
  local lpath=${8:-worktrees/$slot/liteinst2}
  printf '%s\n' \
    '{' \
    '  "version": 3,' \
    '  "slots": {' \
    "    \"$slot\": {" \
    '      "agents": [{"name": "fixture-owner", "read_only": false}],' \
    "      \"hermit_branch\": \"$hbranch\"," \
    "      \"hermit_path\": \"$hpath\"," \
    "      \"reverie_branch\": \"$rbranch\"," \
    "      \"reverie_path\": \"$rpath\"," \
    "      \"liteinst2_branch\": \"$lbranch\"," \
    "      \"liteinst2_path\": \"$lpath\"," \
    '      "task": "fixture", "status": "active"' \
    '    }' \
    '  }' \
    '}' >"$root/worktree-state.json"
}

run_release() {
  local root=$1 slot=$2
  (cd "$root" && scripts/release-worktree.rs --slot "$slot" --clean)
}

# Positive: exact targets disappear while unrelated prunable admin entries in
# all three primaries remain. This brackets the original fleet-wide incident.
root="$test_root/exact"
init_root "$root"
for product in hermit reverie liteinst2; do
  add_target "$root" "$product" target
  eval "stale_$product=\$(add_missing_unrelated \"$root\" \"$product\" unrelated)"
done
write_state "$root" target detached detached detached
run_release "$root" target >/dev/null
[[ ! -e "$root/worktrees/target" ]] || fail 'exact target slot remains'
jq -e '.slots.target == null' "$root/worktree-state.json" >/dev/null \
  || fail 'released slot remains in state'
for product in hermit reverie liteinst2; do
  eval "stale=\$stale_$product"
  assert_registered "$root" "$product" "$stale"
done

# Positive: products recorded as '-' are outside the release authority and
# their unrelated stale registry entries remain untouched.
root="$test_root/dash"
init_root "$root"
add_target "$root" hermit target
stale_reverie="$(add_missing_unrelated "$root" reverie unrelated)"
stale_liteinst2="$(add_missing_unrelated "$root" liteinst2 unrelated)"
write_state "$root" target detached - -
run_release "$root" target >/dev/null
assert_registered "$root" reverie "$stale_reverie"
assert_registered "$root" liteinst2 "$stale_liteinst2"

# Negative: an allocated-but-missing target is refused before another product,
# state, ACTIVE, or an unrelated stale registry entry changes.
root="$test_root/missing"
init_root "$root"
add_target "$root" hermit target
add_target "$root" reverie target
add_target "$root" liteinst2 target
stale_hermit="$(add_missing_unrelated "$root" hermit unrelated)"
mv "$root/worktrees/target/reverie" "$root/moved/target-reverie"
write_state "$root" target detached detached detached
state_before="$(sha256sum "$root/worktree-state.json")"
active_before="$(sha256sum "$root/worktrees/ACTIVE.md")"
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'allocated missing target was accepted'
fi
rg -q 'clean preflight failed.*allocated reverie worktree is missing' "$root/release.out" \
  || fail 'missing-target refusal was not explicit'
[[ -e "$root/worktrees/target/hermit" ]] || fail 'preflight mutated an earlier target'
[[ "$(sha256sum "$root/worktree-state.json")" == "$state_before" ]] \
  || fail 'missing-target refusal changed state'
[[ "$(sha256sum "$root/worktrees/ACTIVE.md")" == "$active_before" ]] \
  || fail 'missing-target refusal changed ACTIVE'
assert_registered "$root" hermit "$stale_hermit"

# Negative: a recorded sibling path cannot widen the selected slot.
root="$test_root/mismatch"
init_root "$root"
add_target "$root" hermit target
add_target "$root" hermit sibling
write_state "$root" target detached - - worktrees/sibling/hermit
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'mismatched recorded path was accepted'
fi
rg -q "records hermit path 'worktrees/sibling/hermit'" "$root/release.out" \
  || fail 'mismatched-path refusal was not explicit'
[[ -e "$root/worktrees/sibling/hermit" ]] || fail 'sibling path was removed'
[[ -e "$root/worktrees/target/hermit" ]] || fail 'target changed on mismatch refusal'

# Negative: the recorded checkout identity must match the exact registered
# child, not merely its path.
root="$test_root/identity"
init_root "$root"
add_target "$root" hermit target
write_state "$root" target wrong-branch - -
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'checkout identity mismatch was accepted'
fi
rg -q "records hermit checkout 'wrong-branch'" "$root/release.out" \
  || fail 'checkout identity refusal was not explicit'
[[ -e "$root/worktrees/target/hermit" ]] || fail 'identity refusal removed target'

# Negative: only a safe lexical slot token can influence filesystem paths.
root="$test_root/absolute-slot"
init_root "$root"
mkdir -p "$root/escape"
write_state "$root" target - - -
jq --arg slot "$root/escape" '.slots = {($slot): .slots.target}' \
  "$root/worktree-state.json" >"$root/state.tmp"
mv "$root/state.tmp" "$root/worktree-state.json"
if run_release "$root" "$root/escape" >"$root/release.out" 2>&1; then
  fail 'absolute slot name was accepted'
fi
rg -q 'invalid slot name' "$root/release.out" \
  || fail 'absolute-slot refusal was not explicit'
[[ -d "$root/escape" ]] || fail 'absolute-slot refusal removed external directory'

# Negative: only an explicit string '-' skips a product. Missing/non-string
# allocation data fails closed.
root="$test_root/missing-branch"
init_root "$root"
add_target "$root" hermit target
write_state "$root" target detached - -
jq 'del(.slots.target.hermit_branch)' "$root/worktree-state.json" >"$root/state.tmp"
mv "$root/state.tmp" "$root/worktree-state.json"
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'missing branch field was accepted'
fi
rg -q 'missing/non-string hermit_branch' "$root/release.out" \
  || fail 'missing-branch refusal was not explicit'
[[ -e "$root/worktrees/target/hermit" ]] || fail 'missing-branch refusal removed target'

# Negative: a symlink at the canonical lexical path cannot alias a sibling
# registered worktree.
root="$test_root/symlink"
init_root "$root"
add_target "$root" hermit target
add_target "$root" hermit sibling
git -C "$root/hermit" worktree remove "$root/worktrees/target/hermit"
ln -s "$root/worktrees/sibling/hermit" "$root/worktrees/target/hermit"
write_state "$root" target detached - -
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'symlink alias was accepted'
fi
rg -q 'is a symlink; refusing alias' "$root/release.out" \
  || fail 'symlink refusal was not explicit'
[[ -e "$root/worktrees/sibling/hermit" ]] || fail 'symlink refusal removed sibling'

# Negative: a stale primary admin record and a replacement repository at the
# same path are not reciprocal identity evidence.
root="$test_root/replacement-repo"
init_root "$root"
add_target "$root" hermit target
mv "$root/worktrees/target/hermit" "$root/moved/original-hermit"
git init -q "$root/worktrees/target/hermit"
git -C "$root/worktrees/target/hermit" config user.email test@example.invalid
git -C "$root/worktrees/target/hermit" config user.name test
printf '%s\n' replacement >"$root/worktrees/target/hermit/replacement"
git -C "$root/worktrees/target/hermit" add replacement
git -C "$root/worktrees/target/hermit" commit -qm replacement
git -C "$root/worktrees/target/hermit" checkout -q --detach
write_state "$root" target detached - -
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'replacement repository at stale registered path was accepted'
fi
rg -q 'child common-dir .* does not match primary' "$root/release.out" \
  || fail 'replacement-repo refusal was not explicit'
[[ -e "$root/worktrees/target/hermit/replacement" ]] \
  || fail 'replacement-repo refusal removed replacement data'

# Negative: unexpected slot-root residue is discovered before the exact child
# is removed, keeping the operation retryable.
root="$test_root/residue"
init_root "$root"
add_target "$root" hermit target
printf '%s\n' sentinel >"$root/worktrees/target/UNOWNED"
write_state "$root" target detached - -
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'unexpected slot residue was accepted'
fi
rg -q 'contains unexpected entry' "$root/release.out" \
  || fail 'slot residue refusal was not explicit'
[[ -e "$root/worktrees/target/hermit" ]] || fail 'residue refusal removed target'

# Negative: an exact registered but locked target fails without changing state
# or ACTIVE. This brackets the runtime failure path after all preflights pass.
root="$test_root/locked"
init_root "$root"
add_target "$root" hermit target
write_state "$root" target detached - -
git -C "$root/hermit" worktree lock "$root/worktrees/target/hermit"
state_before="$(sha256sum "$root/worktree-state.json")"
active_before="$(sha256sum "$root/worktrees/ACTIVE.md")"
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'locked target was removed'
fi
[[ "$(sha256sum "$root/worktree-state.json")" == "$state_before" ]] \
  || fail 'locked-target refusal changed state'
[[ "$(sha256sum "$root/worktrees/ACTIVE.md")" == "$active_before" ]] \
  || fail 'locked-target refusal changed ACTIVE'
[[ -e "$root/worktrees/target/hermit" ]] || fail 'locked-target refusal removed target'

# Negative plus recovery: if a later exact product removal fails, completed
# removals are recorded per product and retry resumes the remaining target.
root="$test_root/partial-retry"
init_root "$root"
add_target "$root" hermit target
add_target "$root" reverie target
write_state "$root" target detached detached -
git -C "$root/reverie" worktree lock "$root/worktrees/target/reverie"
if run_release "$root" target >"$root/release.out" 2>&1; then
  fail 'locked second target unexpectedly completed release'
fi
jq -e '.slots.target.hermit_branch == "-" and .slots.target.reverie_branch == "detached"' \
  "$root/worktree-state.json" >/dev/null \
  || fail 'partial release did not record exact per-product progress'
[[ ! -e "$root/worktrees/target/hermit" ]] || fail 'first exact target was not removed'
[[ -e "$root/worktrees/target/reverie" ]] || fail 'locked remaining target was removed'
git -C "$root/reverie" worktree unlock "$root/worktrees/target/reverie"
run_release "$root" target >/dev/null
jq -e '.slots.target == null' "$root/worktree-state.json" >/dev/null \
  || fail 'retry did not complete slot release'
[[ ! -e "$root/worktrees/target" ]] || fail 'retry left target slot directory'

if rg -n 'worktree[^\n]*prune|\["worktree", "prune"\]' "$subject" >/dev/null; then
  fail 'release script still contains a worktree prune call'
fi

echo 'release-worktree-target-scope-test: PASS (3 positive; 10 negative; 3/3 unrelated registries preserved)'
