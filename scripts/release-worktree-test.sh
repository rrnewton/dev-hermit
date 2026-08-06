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
live_pid=
locked_proc_dir=
cleanup() {
  if [[ -n ${locked_proc_dir:-} ]]; then
    chmod 700 "$locked_proc_dir" 2>/dev/null || true
  fi
  if [[ -n ${live_pid:-} ]] && kill -0 "$live_pid" 2>/dev/null; then
    kill "$live_pid"
    wait "$live_pid" 2>/dev/null || true
  fi
  rm -rf -- "$suite_root"
}
trap cleanup EXIT

fail() {
  echo "release-worktree-test: FAIL: $*" >&2
  exit 1
}

# release-worktree uses `with-proxy git` for authoritative origin checks. The
# fixtures have file://-local origins, so shadow the fleet wrapper with a
# no-network adapter that preserves the exact git argv.
mkdir -p "$suite_root/bin"
RELEASE_TEST_REAL_GIT=$(command -v git)
export RELEASE_TEST_REAL_GIT
cat >"$suite_root/bin/with-proxy" <<'SH'
#!/usr/bin/env bash
if [[ -n ${RELEASE_TEST_RACE_TARGET:-} ]] && [[ " $* " == *' ls-remote '* ]]; then
  printf 'planted after the preliminary status\n' >"$RELEASE_TEST_RACE_TARGET"
fi
if [[ -n ${RELEASE_TEST_REASSIGN_STATE:-} ]] && [[ " $* " == *' ls-remote '* ]]; then
  cp "$RELEASE_TEST_REASSIGN_STATE" "$RELEASE_TEST_STATE_TARGET"
  cp "$RELEASE_TEST_REASSIGN_ACTIVE" "$RELEASE_TEST_ACTIVE_TARGET"
fi
if [[ -n ${RELEASE_TEST_LOCK_PATH:-} ]] && [[ " $* " == *' ls-remote '* ]]; then
  exec {lock_fd}>"$RELEASE_TEST_LOCK_PATH"
  if flock -n "$lock_fd"; then
    printf 'acquired\n' >"$RELEASE_TEST_LOCK_RESULT"
    flock -u "$lock_fd"
  else
    printf 'blocked\n' >"$RELEASE_TEST_LOCK_RESULT"
  fi
  exec {lock_fd}>&-
fi
exec "$@"
SH
cat >"$suite_root/bin/git" <<'SH'
#!/usr/bin/env bash
if [[ -n ${RELEASE_TEST_TRANSACTION_ADMIN:-} ]] \
    && [[ " $* " == *' worktree remove '* ]]; then
  if [[ -f "$RELEASE_TEST_TRANSACTION_ADMIN/release-worktree.in-progress" ]] \
      && [[ -d "$RELEASE_TEST_TRANSACTION_ADMIN/modules.release-worktree" ]] \
      && [[ ! -e "$RELEASE_TEST_TRANSACTION_ADMIN/modules" ]] \
      && [[ " $* " != *' --force '* ]]; then
    printf 'active-nonforce\n' >"$RELEASE_TEST_TRANSACTION_RESULT"
  else
    printf 'invalid\n' >"$RELEASE_TEST_TRANSACTION_RESULT"
  fi
fi
if [[ -n ${RELEASE_TEST_FORCE_ADMIN:-} ]] \
    && [[ " $* " == *' worktree remove '* ]]; then
  if [[ " $* " == *' --force '* ]] \
      && [[ ! -e "$RELEASE_TEST_FORCE_ADMIN/release-worktree.in-progress" ]] \
      && [[ ! -e "$RELEASE_TEST_FORCE_ADMIN/modules.release-worktree" ]]; then
    printf 'direct-force\n' >"$RELEASE_TEST_FORCE_RESULT"
  else
    printf 'invalid\n' >"$RELEASE_TEST_FORCE_RESULT"
  fi
fi
if [[ -n ${RELEASE_TEST_REMOVE_RACE_TARGET:-} ]] \
    && [[ " $* " == *' worktree remove '* ]]; then
  printf 'planted at worktree remove invocation\n' >"$RELEASE_TEST_REMOVE_RACE_TARGET"
fi
exec "$RELEASE_TEST_REAL_GIT" "$@"
SH
chmod +x "$suite_root/bin/with-proxy"
chmod +x "$suite_root/bin/git"
export PATH="$suite_root/bin:$PATH"

configure_repo() {
  local repo=$1
  git -C "$repo" config user.email release-worktree-test@example.invalid
  git -C "$repo" config user.name release-worktree-test
}

write_state() {
  local slot=$1 branch=$2 recorded_path=$3
  cat >"$fixture_root/worktree-state.json" <<JSON
{
  "version": 3,
  "slots": {
    "$slot": {
      "agents": [{"name": "fixture-$slot", "read_only": false}],
      "hermit_branch": "$branch",
      "hermit_path": "$recorded_path",
      "reverie_branch": "-",
      "reverie_path": "worktrees/$slot/reverie",
      "liteinst2_branch": "-",
      "liteinst2_path": "worktrees/$slot/liteinst2",
      "task": "fixture-$slot",
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
| $slot | fixture-$slot | $branch | - | - | fixture-$slot | active | no |
<!-- END worktree-state -->
MD
}

set_owner_schema() {
  local agents_json=$1 active_agent=$2
  python3 - "$fixture_root/worktree-state.json" \
    "$fixture_root/worktrees/ACTIVE.md" "$agents_json" "$active_agent" <<'PY'
import json
import pathlib
import sys

state_path, active_path, agents_json, active_agent = sys.argv[1:]
state = json.loads(pathlib.Path(state_path).read_text())
slot = next(iter(state["slots"]))
state["slots"][slot]["agents"] = json.loads(agents_json)
pathlib.Path(state_path).write_text(json.dumps(state, indent=2) + "\n")

lines = pathlib.Path(active_path).read_text().splitlines()
for index, line in enumerate(lines):
    if line.startswith(f"| {slot} |"):
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        cells[1] = active_agent
        lines[index] = "| " + " | ".join(cells) + " |"
pathlib.Path(active_path).write_text("\n".join(lines) + "\n")
PY
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
    "$fixture_root/proc" \
    "$fixture_root/scripts" \
    "$fixture_root/worktrees" \
    "$fixture_root/unrelated"
  : >"$fixture_root/.gitmodules"
  cp "$script_dir/check-worktree-registry.rs" "$fixture_root/scripts/"
  printf 'must survive\n' >"$fixture_root/unrelated/sentinel.txt"

  local leaf_origin="$fixture_root/remotes/leaf.git"
  local leaf_seed="$fixture_root/leaf-seed"
  git init -q --bare "$leaf_origin"
  git -C "$leaf_origin" symbolic-ref HEAD refs/heads/main
  git init -q -b main "$leaf_seed"
  configure_repo "$leaf_seed"
  printf 'leaf published content\n' >"$leaf_seed/leaf.txt"
  git -C "$leaf_seed" add leaf.txt
  git -C "$leaf_seed" commit -q -m 'seed leaf origin'
  git -C "$leaf_seed" remote add origin "$leaf_origin"
  git -C "$leaf_seed" push -q -u origin main

  local nested_origin="$fixture_root/remotes/nested.git"
  local nested_seed="$fixture_root/nested-seed"
  git init -q --bare "$nested_origin"
  git -C "$nested_origin" symbolic-ref HEAD refs/heads/main
  git init -q -b main "$nested_seed"
  configure_repo "$nested_seed"
  printf 'nested published content\n' >"$nested_seed/nested.txt"
  git -C "$nested_seed" -c protocol.file.allow=always submodule add -q \
    "$leaf_origin" deps/leaf
  git -C "$nested_seed" add nested.txt .gitmodules deps/leaf
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
  git -C "$target" -c protocol.file.allow=always submodule update -q --init --recursive

  git -C "$primary" worktree add -q -b "keep-$name" "$keep" origin/main

  write_state "$name" "$target_branch" "worktrees/$name/hermit"
}

run_release() {
  local slot=$1
  shift
  (cd "$fixture_root" && env HERMIT_RELEASE_TEST_PROC_ROOT="$fixture_root/proc" \
    "$@" "$script_dir/release-worktree.rs" --slot "$slot" --clean) \
    >"$output" 2>&1
}

run_release_force() {
  local slot=$1
  (cd "$fixture_root" && env HERMIT_RELEASE_TEST_PROC_ROOT="$fixture_root/proc" \
    "$script_dir/release-worktree.rs" \
    --slot "$slot" --clean --force) >"$output" 2>&1
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

assert_bad_owners_refused() {
  local name=$1 agents_json=$2 active_agent=$3
  make_fixture "$name"
  set_owner_schema "$agents_json" "$active_agent"
  if "$fixture_root/scripts/check-worktree-registry.rs" --root "$fixture_root" \
      >"$fixture_root/check-owners.out" 2>&1; then
    fail "canonical verifier accepted malformed owners: $name"
  fi
  grep -Fq 'OWNERS' "$fixture_root/check-owners.out" \
    || fail "canonical verifier did not attribute malformed owners: $name"
  if run_release_force "$name"; then
    fail "release accepted malformed owners: $name"
  fi
  grep -Fq 'invalid slot ownership' "$output" \
    || fail "release did not expose malformed ownership: $name"
  assert_target_retained "$name"
}

# POSITIVE: the outer repository plus two recursively initialized levels are
# clean and exactly published. Production must enumerate and verify all three.
make_fixture clean-submodule
test -z "$(git -C "$target" status --porcelain)" \
  || fail 'positive parent fixture is dirty before release'
test -z "$(git -C "$target/deps/nested" status --porcelain)" \
  || fail 'positive nested fixture is dirty before release'
test -z "$(git -C "$target/deps/nested/deps/leaf" status --porcelain)" \
  || fail 'positive recursively nested fixture is dirty before release'
target_head="$(git -C "$target" rev-parse HEAD)"
remote_target_head="$(git -C "$target" ls-remote origin "refs/heads/$target_branch" | cut -f1)"
test "$target_head" = "$remote_target_head" \
  || fail 'positive parent HEAD is not exactly published'
nested_head="$(git -C "$target/deps/nested" rev-parse HEAD)"
remote_nested_head="$(git -C "$target/deps/nested" ls-remote origin refs/heads/main | cut -f1)"
test "$nested_head" = "$remote_nested_head" \
  || fail 'positive nested HEAD is not exactly published'
leaf_head="$(git -C "$target/deps/nested/deps/leaf" rev-parse HEAD)"
remote_leaf_head="$(git -C "$target/deps/nested/deps/leaf" ls-remote origin refs/heads/main | cut -f1)"
test "$leaf_head" = "$remote_leaf_head" \
  || fail 'positive recursively nested HEAD is not exactly published'
clean_admin="$(git -C "$target" rev-parse --path-format=absolute --git-dir)"
transaction_result="$fixture_root/transaction-result"
if ! run_release clean-submodule \
    RELEASE_TEST_TRANSACTION_ADMIN="$clean_admin" \
    RELEASE_TEST_TRANSACTION_RESULT="$transaction_result"; then
  cat "$output" >&2
  fail 'clean, fully published parent+submodule worktree was refused'
fi
grep -Fq 'verified 3 outer/nested repository HEAD(s) on origin' "$output" \
  || fail 'production did not report all three recursive repository proofs'
grep -Fxq 'active-nonforce' "$transaction_result" \
  || fail 'positive cleanup did not carry its marker/quarantine into non-force removal'
test ! -e "$clean_admin" \
  || fail 'successful cleanup left its worktree admin transaction behind'
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

# EXPLICIT-FORCE POSITIVE: the dangerous override remains available only when
# the user requests it. It reaches Git force directly, never by consuming the
# normal path's marker or quarantined submodule admin.
make_fixture explicit-force
git -C "$target" commit -q --allow-empty -m 'force-only local commit'
printf 'force-only dirty work\n' >"$target/force-only.txt"
force_admin="$(git -C "$target" rev-parse --path-format=absolute --git-dir)"
force_result="$fixture_root/force-result"
if ! (cd "$fixture_root" && env \
    HERMIT_RELEASE_TEST_PROC_ROOT="$fixture_root/proc" \
    RELEASE_TEST_FORCE_ADMIN="$force_admin" \
    RELEASE_TEST_FORCE_RESULT="$force_result" \
    "$script_dir/release-worktree.rs" --slot explicit-force --clean --force) \
    >"$output" 2>&1; then
  cat "$output" >&2
  fail 'explicit force did not override dirty and unpublished work'
fi
grep -Fxq 'direct-force' "$force_result" \
  || fail 'explicit force consumed a normal-cleanup transaction artifact'
test ! -e "$target" || fail 'explicit-force target survived requested deletion'
if grep -Fq '"explicit-force"' "$fixture_root/worktree-state.json"; then
  fail 'explicit-force target survived in registry state after deletion'
fi
assert_unrelated_survives

# NEGATIVE: the registry remains the authority for which slot may be released.
make_fixture registry-authority
if run_release not-registered; then
  fail 'unregistered slot name was accepted'
fi
grep -Fq 'slot not-registered is not registered in worktree-state.json' "$output" \
  || fail 'unregistered refusal did not come from the registry guardrail'
assert_target_retained registry-authority

# OWNER-SCHEMA NEGATIVES: the canonical verifier and releaser must both reject
# missing fields, duplicate names, no mutating owner, and placeholder names.
assert_bad_owners_refused owner-missing-fields '[{}]' '-'
assert_bad_owners_refused owner-duplicate \
  '[{"name":"dup","read_only":false},{"name":"dup","read_only":true}]' \
  'dup (+ro: dup)'
assert_bad_owners_refused owner-no-mutator \
  '[{"name":"reader","read_only":true}]' '- (+ro: reader)'
assert_bad_owners_refused owner-invalid-name \
  '[{"name":"","read_only":false}]' ''

# REVIEW FIXTURE 1: the outer feature branch is clean and exactly pushed, but
# its initialized nested repository has a detached local-only commit. Production
# must verify the nested origin itself and retain its only copy.
make_fixture nested-undurable
configure_repo "$target/deps/nested"
git -C "$target/deps/nested" commit -q --allow-empty -m 'nested local-only commit'
nested_local_head="$(git -C "$target/deps/nested" rev-parse HEAD)"
git -C "$target" add deps/nested
git -C "$target" commit -q -m 'record unpublished nested commit'
git -C "$target" push -q origin "HEAD:refs/heads/$target_branch"
test -z "$(git -C "$target" status --porcelain)" \
  || fail 'nested-durability outer fixture is dirty'
if git -C "$target/deps/nested" ls-remote origin \
  | cut -f1 | grep -Fxq "$nested_local_head"; then
  fail 'nested-durability fixture is inert: local nested HEAD is already published'
fi
if run_release nested-undurable; then
  fail 'clean outer worktree with unpublished nested HEAD was removed'
fi
grep -Fq 'not reachable from any current origin ref' "$output" \
  || fail 'nested durability refusal did not come from recursive origin proof'
test "$nested_local_head" = "$(git -C "$target/deps/nested" rev-parse HEAD)" \
  || fail 'unpublished nested HEAD was lost'
assert_target_retained nested-undurable

# REVIEW FIXTURE 2: malformed committed .gitmodules makes Git inspection fail
# while the initialized nested repository holds untracked work. An inspection
# error must refuse rather than being interpreted as clean.
make_fixture malformed-inspection
printf '\n[submodule "unterminated"\n' >>"$target/.gitmodules"
git -C "$target" add .gitmodules
git -C "$target" commit -q --no-verify -m 'commit malformed submodule metadata'
git -C "$target" push -q origin "HEAD:refs/heads/$target_branch"
printf 'precious nested work\n' >"$target/deps/nested/precious.txt"
set +e
git -C "$target" status --porcelain >"$fixture_root/malformed-status.out" 2>&1
malformed_status_rc=$?
set -e
test "$malformed_status_rc" -ne 0 \
  || fail 'malformed inspection fixture is inert: git status unexpectedly succeeded'
if run_release_force malformed-inspection; then
  fail 'Git inspection failure was interpreted as authorization'
fi
grep -Eq 'could not enumerate initialized submodules|git .* status .* failed' "$output" \
  || fail 'malformed metadata refusal did not expose a fail-closed Git inspection'
grep -Fxq 'precious nested work' "$target/deps/nested/precious.txt" \
  || fail 'untracked work behind failed Git inspection was lost'
assert_target_retained malformed-inspection

# REVIEW FIXTURE 3: plant an untracked file from the legitimate ls-remote
# adapter after the preliminary statuses. The final recursive boundary must see
# it and refuse.
make_fixture cleanliness-race
if run_release cleanliness-race RELEASE_TEST_RACE_TARGET="$target/raced.txt"; then
  fail 'last-moment untracked write was deleted by force removal'
fi
grep -Fq 'final removal boundary refused' "$output" \
  || fail 'last-moment write did not reach the final removal boundary'
grep -Fxq 'planted after the preliminary status' "$target/raced.txt" \
  || fail 'last-moment write was lost'
assert_target_retained cleanliness-race

# REVIEW FIXTURE 3B: plant after every scripted status, at the actual worktree
# remove invocation. Ordinary cleanup must use Git's non-force boundary, which
# sees the new file and retains the worktree.
make_fixture remove-boundary-race
remove_admin="$(git -C "$target" rev-parse --path-format=absolute --git-dir)"
test -d "$remove_admin/modules" \
  || fail 'remove-race fixture has no initialized submodule admin to quarantine'
if run_release remove-boundary-race \
    RELEASE_TEST_REMOVE_RACE_TARGET="$target/remove-raced.txt"; then
  fail 'write planted at worktree remove invocation was deleted'
fi
grep -Fxq 'planted at worktree remove invocation' "$target/remove-raced.txt" \
  || fail 'remove-invocation write did not survive'
grep -Eq 'could not remove exact|modified or untracked files' "$output" \
  || fail 'remove-invocation write did not reach Git non-force refusal'
test -d "$remove_admin/modules" \
  || fail 'submodule admin was not restored after non-force removal refusal'
test ! -e "$remove_admin/modules.release-worktree" \
  || fail 'submodule-admin quarantine survived a handled removal refusal'
test -f "$remove_admin/release-worktree.in-progress" \
  || fail 'failed submodule cleanup lost its crash-recovery marker'
assert_target_retained remove-boundary-race

# CRASH-RECOVERY NEGATIVE: a transaction marker left before deinitialization is
# not a license to reuse the prior run's recursive proof. Even explicit force
# must retain the target for coordinator recovery.
make_fixture unfinished-cleanup
unfinished_admin="$(git -C "$target" rev-parse --path-format=absolute --git-dir)"
printf 'simulated interrupted cleanup\n' \
  >"$unfinished_admin/release-worktree.in-progress"
if run_release_force unfinished-cleanup; then
  fail 'unfinished submodule-cleanup transaction was deleted by a retry'
fi
grep -Fq 'unfinished submodule cleanup artifact' "$output" \
  || fail 'unfinished transaction did not reach the crash-recovery guardrail'
test -f "$unfinished_admin/release-worktree.in-progress" \
  || fail 'unfinished transaction marker was lost during refusal'
assert_target_retained unfinished-cleanup

# CRASH-RECOVERY NEGATIVE: a marker plus quarantined admin models termination
# after deinitialization. Neither artifact may be consumed by a retry.
make_fixture unfinished-quarantine
unfinished_admin="$(git -C "$target" rev-parse --path-format=absolute --git-dir)"
printf 'simulated interrupted cleanup\n' \
  >"$unfinished_admin/release-worktree.in-progress"
mv "$unfinished_admin/modules" "$unfinished_admin/modules.release-worktree"
if run_release_force unfinished-quarantine; then
  fail 'unfinished submodule-admin quarantine was deleted by a retry'
fi
grep -Fq 'unfinished submodule cleanup artifact' "$output" \
  || fail 'unfinished quarantine did not reach the crash-recovery guardrail'
test -f "$unfinished_admin/release-worktree.in-progress" \
  || fail 'unfinished transaction marker was lost during quarantine refusal'
test -d "$unfinished_admin/modules.release-worktree" \
  || fail 'unfinished quarantine was lost during refusal'
assert_target_retained unfinished-quarantine

# RECOVERY POSITIVE: restore the quarantined admin before removing the marker,
# then require the ordinary path to repeat all recursive proofs and use its own
# non-force transaction. The quarantine itself is never a deletion authority.
mv "$unfinished_admin/modules.release-worktree" "$unfinished_admin/modules"
rm -- "$unfinished_admin/release-worktree.in-progress"
if ! run_release unfinished-quarantine; then
  cat "$output" >&2
  fail 'explicitly restored submodule cleanup could not be re-proved and removed'
fi
grep -Fq 'verified 3 outer/nested repository HEAD(s) on origin' "$output" \
  || fail 'recovered cleanup did not repeat recursive durability proof'
test ! -e "$target" || fail 'recovered target survived successful cleanup'
assert_unrelated_survives

# REVIEW FIXTURE 4: the registered state key points at a symlink alias of an
# unrelated physical worktree. Exact canonical/state/registry binding must
# reject the alias and preserve the real checkout.
make_fixture real-target
real_target=$target
mkdir -p "$fixture_root/worktrees/alias"
ln -s ../real-target/hermit "$fixture_root/worktrees/alias/hermit"
write_state alias "$target_branch" 'worktrees/alias/hermit'
target=$real_target
if run_release_force alias; then
  fail 'symlink alias removed an unrelated physical worktree'
fi
grep -Fq 'refusing symlink/path alias' "$output" \
  || fail 'symlink alias was not rejected by exact target binding'
assert_target_retained alias

# REGISTRY-GENERATION NEGATIVE: an out-of-protocol writer swaps both registry
# files to a second internally valid owner during ls-remote. Full-record binding
# must detect the change even though the canonical verifier accepts the new row.
make_fixture ownership-race
cp "$fixture_root/worktree-state.json" "$fixture_root/replacement-state.json"
cp "$fixture_root/worktrees/ACTIVE.md" "$fixture_root/replacement-ACTIVE.md"
sed -i 's/fixture-ownership-race/replacement-owner/g' \
  "$fixture_root/replacement-state.json" "$fixture_root/replacement-ACTIVE.md"
if run_release ownership-race \
    RELEASE_TEST_REASSIGN_STATE="$fixture_root/replacement-state.json" \
    RELEASE_TEST_STATE_TARGET="$fixture_root/worktree-state.json" \
    RELEASE_TEST_REASSIGN_ACTIVE="$fixture_root/replacement-ACTIVE.md" \
    RELEASE_TEST_ACTIVE_TARGET="$fixture_root/worktrees/ACTIVE.md"; then
  fail 'valid concurrent registry reassignment was deleted'
fi
grep -Fq 'authorization changed before removal' "$output" \
  || fail 'full slot-record generation did not detect registry reassignment'
assert_target_retained ownership-race

# REGISTRY-LOCK POSITIVE: every network child observes the canonical writer
# lock as held; no second allocator/releaser can enter the mutation interval.
make_fixture registry-lock
lock_result="$fixture_root/lock-result"
if ! run_release registry-lock \
    RELEASE_TEST_LOCK_PATH="$fixture_root/worktree-state.lock" \
    RELEASE_TEST_LOCK_RESULT="$lock_result"; then
  cat "$output" >&2
  fail 'clean release failed while probing registry lock'
fi
grep -Fxq 'blocked' "$lock_result" \
  || fail 'release did not hold canonical registry lock across network proof'
test ! -e "$target" || fail 'registry-lock positive target survived release'
assert_unrelated_survives

# PROCESS-OWNERSHIP NEGATIVE: a specifically captured child process has its cwd
# inside the exact target. Cleanup must refuse it and must never signal it.
make_fixture live-owner
(cd "$target" && exec sleep 60) &
live_pid=$!
for _ in 1 2 3 4 5; do
  [[ $(readlink "/proc/$live_pid/cwd" 2>/dev/null || true) == "$target" ]] && break
  sleep 0.05
done
mkdir -p "$fixture_root/proc/$live_pid"
ln -s "$target" "$fixture_root/proc/$live_pid/cwd"
ln -s /bin/sleep "$fixture_root/proc/$live_pid/exe"
if run_release_force live-owner; then
  fail 'worktree used by a live process was removed'
fi
grep -Eq 'live process ownership|live processes use the slot' "$output" \
  || fail 'live process did not trigger the ownership refusal'
kill -0 "$live_pid" 2>/dev/null \
  || fail 'release signaled a process it did not own'
kill "$live_pid"
wait "$live_pid" 2>/dev/null || true
live_pid=
assert_target_retained live-owner

# PROCESS-UNCERTAINTY NEGATIVE: a same-UID synthetic pid whose cwd cannot be
# read must block cleanup, including explicit user force.
make_fixture hidden-owner
locked_proc_dir="$fixture_root/proc/424242"
mkdir -p "$locked_proc_dir"
ln -s "$target" "$locked_proc_dir/cwd"
ln -s /bin/sleep "$locked_proc_dir/exe"
chmod 000 "$locked_proc_dir"
if run_release_force hidden-owner; then
  chmod 700 "$locked_proc_dir"
  locked_proc_dir=
  fail 'PermissionDenied process ownership was treated as absent'
fi
chmod 700 "$locked_proc_dir"
locked_proc_dir=
grep -Eq 'could not inspect live process link|Permission denied' "$output" \
  || fail 'hidden process did not expose fail-closed ownership uncertainty'
assert_target_retained hidden-owner

# TOKEN NEGATIVE: path-like slot text must be rejected before state/path lookup.
make_fixture valid-token
if run_release '../valid-token'; then
  fail 'path-like slot token was accepted'
fi
grep -Fq "invalid slot name: '../valid-token'" "$output" \
  || fail 'invalid slot token did not reach the token guard'
assert_target_retained valid-token

echo 'release-worktree-test: PASS (22 fixtures: 4 clean/locked/recovered/explicit-force removals; 19 planted refusals; unrelated path+worktree survived)'
