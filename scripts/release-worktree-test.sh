#!/usr/bin/env bash
# Bracket the safety contract of release-worktree.rs --clean using only
# disposable local repositories. In particular, an initialized submodule makes
# plain `git worktree remove` refuse even when both the parent and submodule are
# clean; the release script must get past that Git implementation detail only
# after its own dirty, durability, and registry checks pass.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
suite_root="$(mktemp -d "${TMPDIR:-/tmp}/release-worktree-test.XXXXXX")"
case "$suite_root" in
  "${TMPDIR:-/tmp}"/release-worktree-test.*) ;;
  *) echo "unsafe temporary test root: $suite_root" >&2; exit 1 ;;
esac
trap 'rm -rf -- "$suite_root"' EXIT

fail() {
  echo "release-worktree-test: FAIL: $*" >&2
  exit 1
}

# release-worktree uses `with-proxy git` for authoritative origin checks. The
# fixtures have file://-local origins, so shadow the fleet wrapper with a
# no-network adapter that preserves the exact git argv.
mkdir -p "$suite_root/bin"
cat >"$suite_root/bin/with-proxy" <<'SH'
#!/usr/bin/env bash
exec "$@"
SH
chmod +x "$suite_root/bin/with-proxy"
export PATH="$suite_root/bin:$PATH"

configure_repo() {
  local repo=$1
  git -C "$repo" config user.email release-worktree-test@example.invalid
  git -C "$repo" config user.name release-worktree-test
}

# Set globals fixture_root, primary, target, keep, target_branch, and output.
make_fixture() {
  local name=$1
  fixture_root="$suite_root/$name"
  primary="$fixture_root/hermit"
  target="$fixture_root/worktrees/$name/hermit"
  keep="$fixture_root/worktrees/keep-$name/hermit"
  target_branch="published-$name"
  output="$fixture_root/release.out"

  mkdir -p \
    "$fixture_root/remotes" \
    "$fixture_root/reverie" \
    "$fixture_root/liteinst2" \
    "$fixture_root/worktrees" \
    "$fixture_root/unrelated"
  : >"$fixture_root/.gitmodules"
  printf 'must survive\n' >"$fixture_root/unrelated/sentinel.txt"

  local nested_origin="$fixture_root/remotes/nested.git"
  local nested_seed="$fixture_root/nested-seed"
  git init -q --bare "$nested_origin"
  git -C "$nested_origin" symbolic-ref HEAD refs/heads/main
  git init -q -b main "$nested_seed"
  configure_repo "$nested_seed"
  printf 'nested published content\n' >"$nested_seed/nested.txt"
  git -C "$nested_seed" add nested.txt
  git -C "$nested_seed" commit -q -m 'seed nested origin'
  git -C "$nested_seed" remote add origin "$nested_origin"
  git -C "$nested_seed" push -q -u origin main

  local parent_origin="$fixture_root/remotes/hermit.git"
  git init -q --bare "$parent_origin"
  git -C "$parent_origin" symbolic-ref HEAD refs/heads/main
  git init -q -b main "$primary"
  configure_repo "$primary"
  printf 'parent published content\n' >"$primary/parent.txt"
  git -C "$primary" -c protocol.file.allow=always submodule add -q \
    "$nested_origin" deps/nested
  git -C "$primary" add parent.txt .gitmodules deps/nested
  git -C "$primary" commit -q -m 'seed parent origin with submodule'
  git -C "$primary" remote add origin "$parent_origin"
  git -C "$primary" push -q -u origin main

  git -C "$primary" worktree add -q -b "$target_branch" "$target" origin/main
  configure_repo "$target"
  git -C "$target" commit -q --allow-empty -m 'publish target branch'
  git -C "$target" push -q -u origin "HEAD:refs/heads/$target_branch"
  git -C "$target" -c protocol.file.allow=always submodule update -q --init

  git -C "$primary" worktree add -q -b "keep-$name" "$keep" origin/main

  cat >"$fixture_root/worktree-state.json" <<JSON
{
  "version": 3,
  "slots": {
    "$name": {
      "agents": [{"name": "fixture-$name", "read_only": false}],
      "hermit_branch": "$target_branch",
      "hermit_path": "worktrees/$name/hermit",
      "reverie_branch": "-",
      "reverie_path": "worktrees/$name/reverie",
      "liteinst2_branch": "-",
      "liteinst2_path": "worktrees/$name/liteinst2",
      "task": "fixture-$name",
      "status": "active"
    }
  }
}
JSON
  cat >"$fixture_root/worktrees/ACTIVE.md" <<MD
# Fixture registry
<!-- BEGIN worktree-state (managed by scripts/allocate-worktree.rs; do not edit inside) -->
| Slot | Agent | HermitBranch | ReverieBranch | LiteInst2Branch | Task | Status | ReadOnly |
| --- | --- | --- | --- | --- | --- | --- | --- |
| $name | fixture-$name | $target_branch | - | - | fixture-$name | active | no |
<!-- END worktree-state -->
MD
}

run_release() {
  local slot=$1
  (cd "$fixture_root" && "$script_dir/release-worktree.rs" --slot "$slot" --clean) \
    >"$output" 2>&1
}

assert_unrelated_survives() {
  grep -Fxq 'must survive' "$fixture_root/unrelated/sentinel.txt" \
    || fail "unrelated sentinel was changed for $fixture_root"
  test -d "$keep" || fail "unrelated worktree path was removed: $keep"
  git -C "$keep" status --porcelain >/dev/null \
    || fail "unrelated worktree is no longer usable: $keep"
  git -C "$primary" worktree list --porcelain \
    | grep -Fxq "worktree $keep" \
    || fail "unrelated worktree registration was removed: $keep"
}

assert_target_retained() {
  local slot=$1
  test -d "$target" || fail "refused target was removed: $target"
  git -C "$primary" worktree list --porcelain \
    | grep -Fxq "worktree $target" \
    || fail "refused target registration was removed: $target"
  grep -Fq "\"$slot\"" "$fixture_root/worktree-state.json" \
    || fail "refused target was removed from registry state: $slot"
  assert_unrelated_survives
}

# POSITIVE: both repositories and the target feature branch are published and
# clean. The initialized submodule must not turn that safe state into a refusal.
make_fixture clean-submodule
test -z "$(git -C "$target" status --porcelain)" \
  || fail 'positive parent fixture is dirty before release'
test -z "$(git -C "$target/deps/nested" status --porcelain)" \
  || fail 'positive nested fixture is dirty before release'
target_head="$(git -C "$target" rev-parse HEAD)"
remote_target_head="$(git -C "$target" ls-remote origin "refs/heads/$target_branch" | cut -f1)"
test "$target_head" = "$remote_target_head" \
  || fail 'positive parent HEAD is not exactly published'
nested_head="$(git -C "$target/deps/nested" rev-parse HEAD)"
remote_nested_head="$(git -C "$target/deps/nested" ls-remote origin refs/heads/main | cut -f1)"
test "$nested_head" = "$remote_nested_head" \
  || fail 'positive nested HEAD is not exactly published'
if ! run_release clean-submodule; then
  cat "$output" >&2
  fail 'clean, fully published parent+submodule worktree was refused'
fi
test ! -e "$target" || fail 'clean target path survived successful release'
if git -C "$primary" worktree list --porcelain | grep -Fxq "worktree $target"; then
  fail 'clean target registration survived successful release'
fi
if grep -Fq '"clean-submodule"' "$fixture_root/worktree-state.json"; then
  fail 'clean target survived in registry state after successful release'
fi
assert_unrelated_survives

# NEGATIVE: a tracked modification in the parent worktree must still refuse.
make_fixture dirty-parent
printf 'dirty parent content\n' >>"$target/parent.txt"
if run_release dirty-parent; then
  fail 'dirty parent worktree was removed'
fi
grep -Fq 'refusing --clean with uncommitted work' "$output" \
  || fail 'dirty parent refusal did not come from the dirty-work guardrail'
grep -Fq 'dirty parent content' "$target/parent.txt" \
  || fail 'dirty parent content was lost'
assert_target_retained dirty-parent

# NEGATIVE: untracked content inside the initialized nested submodule must make
# the parent status dirty and refuse without losing the nested content.
make_fixture dirty-nested
printf 'untracked nested content\n' >"$target/deps/nested/untracked.txt"
if run_release dirty-nested; then
  fail 'worktree with untracked nested-submodule content was removed'
fi
grep -Fq 'refusing --clean with uncommitted work' "$output" \
  || fail 'dirty nested refusal did not come from the dirty-work guardrail'
grep -Fxq 'untracked nested content' "$target/deps/nested/untracked.txt" \
  || fail 'untracked nested-submodule content was lost'
assert_target_retained dirty-nested

# NEGATIVE: a clean commit made after the last push must still refuse on the
# origin-durability guardrail.
make_fixture unpushed-branch
git -C "$target" commit -q --allow-empty -m 'local-only commit'
local_only_head="$(git -C "$target" rev-parse HEAD)"
if run_release unpushed-branch; then
  fail 'clean worktree with an unpushed commit was removed'
fi
grep -Fq 'committed work not on origin' "$output" \
  || fail 'unpushed refusal did not come from the origin-durability guardrail'
test "$local_only_head" = "$(git -C "$target" rev-parse HEAD)" \
  || fail 'unpushed branch HEAD changed during refusal'
assert_target_retained unpushed-branch

# NEGATIVE: the registry remains the authority for which slot may be released.
make_fixture registry-authority
if run_release not-registered; then
  fail 'unregistered slot name was accepted'
fi
grep -Fq 'slot not-registered is not registered in worktree-state.json' "$output" \
  || fail 'unregistered refusal did not come from the registry guardrail'
assert_target_retained registry-authority

echo 'release-worktree-test: PASS (1 clean published parent+initialized-submodule removed; 3 unsafe targets refused; 1 unregistered target refused; unrelated path+worktree survived all 5 fixtures)'
