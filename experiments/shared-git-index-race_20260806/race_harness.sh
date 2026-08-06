#!/usr/bin/env bash
# Planting harness for the shared-git-index race in the dev-hermit parent repo.
#
# THE HAZARD (observed for real, 2026-08-06): every agent working in the parent
# shares one working tree and therefore one .git/index. `git add` is not private,
# so any other agent's `git commit` sweeps up your staged paths. Six staged files
# were swept into commit 0b40af7 that way.
#
# THIS RUNS IN A THROWAWAY REPO, NEVER THE PARENT. Planting a two-writer race on
# the live parent would put ~19 agents' real work at risk to test a hypothesis
# about losing work, which is self-defeating. Each scenario gets a fresh repo, so
# scenarios cannot contaminate each other either.
#
# Scenario naming: A and B are two agents. A stages and is SLOW to commit; B
# commits while A's paths are still staged. "swept" means B's commit contains A's
# file -- the bug.
set -uo pipefail

ROOT=$(mktemp -d "${TMPDIR:-/tmp}/git-index-race.XXXXXX")
trap 'rm -rf "$ROOT"' EXIT
PASS=0; FAIL=0

new_repo() {  # $1 = name -> echoes path
  local d="$ROOT/$1"
  mkdir -p "$d"; git -C "$d" init -q
  git -C "$d" config user.name t; git -C "$d" config user.email t@t
  git -C "$d" config commit.gpgsign false
  echo seed > "$d/seed.txt"; git -C "$d" add seed.txt
  git -C "$d" commit -q -m seed
  echo "$d"
}

files_in_head() { git -C "$1" show --pretty=format: --name-only HEAD | grep -v '^$' | sort | tr '\n' ' '; }

check() {  # $1=label $2=expected $3=actual
  if [[ "$(echo $2)" == "$(echo $3)" ]]; then
    printf '  %-52s PASS  [%s]\n' "$1" "$3"; PASS=$((PASS+1))
  else
    printf '  %-52s FAIL  expected[%s] got[%s]\n' "$1" "$2" "$3"; FAIL=$((FAIL+1))
  fi
}

echo "=== S1 BASELINE: does a bare commit really sweep another agent's staged file? ==="
d=$(new_repo s1)
echo a > "$d/a.txt"; git -C "$d" add a.txt            # agent A stages, does not commit
echo b > "$d/b.txt"; git -C "$d" add b.txt            # agent B stages
git -C "$d" commit -q -m "B: bare commit"             # agent B commits -- the bug
check "S1 bare commit sweeps A's file (reproduces bug)" "a.txt b.txt" "$(files_in_head "$d")"

echo
echo "=== S2 OPTION 1: git commit -- <paths> ==="
d=$(new_repo s2)
echo a > "$d/a.txt"; git -C "$d" add a.txt
echo b > "$d/b.txt"; git -C "$d" add b.txt
git -C "$d" commit -q -m "B: pathspec commit" -- b.txt
check "S2 B's commit contains ONLY b.txt" "b.txt" "$(files_in_head "$d")"
check "S2 A's file still staged afterwards" "A" "$(git -C "$d" diff --cached --name-only | grep -q '^a.txt$' && echo A || echo MISSING)"

echo
echo "=== S3 OPTION 1 both sides: A then B, both pathspec ==="
d=$(new_repo s3)
echo a > "$d/a.txt"; git -C "$d" add a.txt
echo b > "$d/b.txt"; git -C "$d" add b.txt
git -C "$d" commit -q -m "B" -- b.txt
git -C "$d" commit -q -m "A" -- a.txt
check "S3 A's commit contains ONLY a.txt" "a.txt" "$(files_in_head "$d")"
check "S3 B's commit still contains ONLY b.txt" "b.txt" "$(git -C "$d" show --pretty=format: --name-only HEAD~1 | grep -v '^$' | sort | tr '\n' ' ')"

echo
echo "=== S4 OPTION 1 LIMIT: does pathspec protect A from B's BARE commit? ==="
d=$(new_repo s4)
echo a > "$d/a.txt"; git -C "$d" add a.txt             # A follows the rule, but has staged
echo b > "$d/b.txt"; git -C "$d" add b.txt
git -C "$d" commit -q -m "B: bare (rule violator)"     # B does NOT follow the rule
check "S4 A is STILL swept by a rule-violating bare commit" "a.txt b.txt" "$(files_in_head "$d")"

echo
echo "=== S5 OPTION 1 CAVEAT: pathspec commits WORKTREE, not what you staged ==="
d=$(new_repo s5)
printf 'v1\n' > "$d/c.txt"; git -C "$d" add c.txt      # stage v1
printf 'v2\n' > "$d/c.txt"                             # worktree now v2, NOT staged
git -C "$d" commit -q -m "pathspec" -- c.txt
got=$(git -C "$d" show HEAD:c.txt)
check "S5 committed content is worktree v2, not staged v1" "v2" "$got"

echo
echo "=== S6 OPTION 3: private GIT_INDEX_FILE ==="
d=$(new_repo s6)
echo a > "$d/a.txt"; git -C "$d" add a.txt             # A stages in the SHARED index
echo b > "$d/b.txt"
IDX="$ROOT/s6.idx"
GIT_INDEX_FILE=$IDX git -C "$d" read-tree HEAD
GIT_INDEX_FILE=$IDX git -C "$d" add b.txt
GIT_INDEX_FILE=$IDX git -C "$d" commit -q -m "B: private index"
check "S6 B's commit contains ONLY b.txt" "b.txt" "$(files_in_head "$d")"
check "S6 A's file untouched in shared index" "A" "$(git -C "$d" diff --cached --name-only | grep -q '^a.txt$' && echo A || echo MISSING)"

echo
echo "=== S7 OPTION 3 HAZARD: stale read-tree silently REVERTS a concurrent commit ==="
d=$(new_repo s7)
printf 'orig\n' > "$d/shared.txt"; git -C "$d" add shared.txt; git -C "$d" commit -q -m base
IDX="$ROOT/s7.idx"
GIT_INDEX_FILE=$IDX git -C "$d" read-tree HEAD         # B snapshots HEAD...
printf 'THEIRS\n' > "$d/shared.txt"                    # ...then A changes and commits
git -C "$d" commit -q -m "A: edits shared.txt" -- shared.txt
echo b > "$d/b.txt"
GIT_INDEX_FILE=$IDX git -C "$d" add b.txt
GIT_INDEX_FILE=$IDX git -C "$d" commit -q -m "B: commits from stale snapshot"
after=$(git -C "$d" show HEAD:shared.txt)
# This is an EXPECTED-HAZARD check: the point is to demonstrate that option 3
# silently destroys concurrent work, so "orig" (A's change gone) is the result
# that confirms the hazard and disqualifies the option.
check "S7 HAZARD CONFIRMED: B's stale index REVERTED A" "orig" "$after"

echo
echo "=== S8 does pathspec commit work for a file that was never 'git add'ed? ==="
d=$(new_repo s8)
echo n > "$d/new.txt"
if git -C "$d" commit -q -m "untracked pathspec" -- new.txt 2>/dev/null; then
  check "S8 untracked file committable via pathspec alone" "new.txt" "$(files_in_head "$d")"
else
  check "S8 untracked needs git add first (expected)" "REFUSED" "REFUSED"
fi

echo
echo "=== S9 THE RECOMMENDED FORM: add + pathspec-commit as ONE step ==="
d=$(new_repo s9)
echo a > "$d/a.txt"; git -C "$d" add a.txt              # A staged, slow
echo b > "$d/b.txt"
git -C "$d" add b.txt && git -C "$d" commit -q -m "B: add+commit atomic" -- b.txt
check "S9 B's commit contains ONLY b.txt" "b.txt" "$(files_in_head "$d")"
check "S9 A survives" "A" "$(git -C "$d" diff --cached --name-only | grep -q '^a.txt$' && echo A || echo MISSING)"

echo
echo "=== S10 can a HOOK tell a pathspec commit from a bare one? ==="
d=$(new_repo s10)
mkdir -p "$d/.git/hooks"
cat > "$d/.git/hooks/pre-commit" <<'HOOK'
#!/usr/bin/env bash
# git builds a TEMPORARY index for a pathspec ("partial") commit and points
# GIT_INDEX_FILE at it for hooks. A bare commit leaves the hook on the shared
# .git/index. That difference is observable, which is what makes enforcement a
# MECHANISM rather than another warning nobody follows.
printf 'HOOKSAW=%s\n' "${GIT_INDEX_FILE:-<unset>}" >> "$(git rev-parse --git-dir)/hooksaw"
HOOK
chmod +x "$d/.git/hooks/pre-commit"
echo a > "$d/a.txt"; git -C "$d" add a.txt
git -C "$d" commit -q -m bare
echo b > "$d/b.txt"; git -C "$d" add b.txt
git -C "$d" commit -q -m pathspec -- b.txt
bare_saw=$(sed -n '1p' "$d/.git/hooksaw"); path_saw=$(sed -n '2p' "$d/.git/hooksaw")
echo "     bare commit hook saw: $bare_saw"
echo "     pathspec commit hook saw: $path_saw"
if [[ "$path_saw" == *next-index* && "$bare_saw" != *next-index* ]]; then
  check "S10 hook CAN distinguish pathspec from bare" "DISTINGUISHABLE" "DISTINGUISHABLE"
else
  check "S10 hook CAN distinguish pathspec from bare" "DISTINGUISHABLE" "NOT-DISTINGUISHABLE"
fi

echo
echo "=== S11 enforcement: hook REFUSES bare, ALLOWS pathspec ==="
d=$(new_repo s11)
cat > "$d/.git/hooks/pre-commit" <<'HOOK'
#!/usr/bin/env bash
case "${GIT_INDEX_FILE:-}" in
  *next-index*) exit 0 ;;
esac
echo "refused: bare commit in a shared-index repo; use: git commit -m msg -- <paths>" >&2
exit 1
HOOK
chmod +x "$d/.git/hooks/pre-commit"
echo a > "$d/a.txt"; git -C "$d" add a.txt
echo b > "$d/b.txt"; git -C "$d" add b.txt
if git -C "$d" commit -q -m "bare" 2>/dev/null; then bare_res=ALLOWED; else bare_res=REFUSED; fi
check "S11 bare commit is REFUSED by the hook" "REFUSED" "$bare_res"
if git -C "$d" commit -q -m "pathspec" -- b.txt 2>/dev/null; then path_res=ALLOWED; else path_res=REFUSED; fi
check "S11 pathspec commit is ALLOWED" "ALLOWED" "$path_res"
check "S11 and it contains ONLY b.txt" "b.txt" "$(files_in_head "$d")"
check "S11 A's staged file survived" "A" "$(git -C "$d" diff --cached --name-only | grep -q '^a.txt$' && echo A || echo MISSING)"

echo
echo "PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]

# --- S12: bracket the ACTUAL shipped guard extracted from .githooks/pre-commit ---
guard_test() {
  local parent=/home/newton/work/dev-hermit
  local d; d=$(new_repo s12)
  mkdir -p "$d/.git/hooks"
  { echo '#!/usr/bin/env bash'
    awk '/^shared_index_guard\(\) \{/,/^\}$/' "$parent/.githooks/pre-commit"
    echo 'if [ "${HERMIT_SHARED_INDEX_GUARD:-warn}" = "block" ]; then shared_index_guard || exit 1; else shared_index_guard || true; fi'
  } > "$d/.git/hooks/pre-commit"
  chmod +x "$d/.git/hooks/pre-commit"

  echo a > "$d/a.txt"; git -C "$d" add a.txt
  echo b > "$d/b.txt"; git -C "$d" add b.txt

  # warn: allows the bare commit but must name the foreign path
  local out; out=$(git -C "$d" commit -m "bare warn" 2>&1); local rc=$?
  check "S12 warn ALLOWS bare commit" "0" "$rc"
  [[ "$out" == *"a.txt"* ]] && check "S12 warn NAMES the foreign staged path" "named" "named" \
                            || check "S12 warn NAMES the foreign staged path" "named" "silent"

  # pathspec: must be silent
  local d2; d2=$(new_repo s12b); cp "$d/.git/hooks/pre-commit" "$d2/.git/hooks/pre-commit"
  echo a > "$d2/a.txt"; git -C "$d2" add a.txt
  echo b > "$d2/b.txt"; git -C "$d2" add b.txt
  out=$(git -C "$d2" commit -m "pathspec" -- b.txt 2>&1)
  [[ "$out" == *"SHARED INDEX"* ]] && check "S12 pathspec commit is SILENT" "silent" "warned" \
                                   || check "S12 pathspec commit is SILENT" "silent" "silent"

  # block: must refuse the bare commit
  local d3; d3=$(new_repo s12c); cp "$d/.git/hooks/pre-commit" "$d3/.git/hooks/pre-commit"
  echo a > "$d3/a.txt"; git -C "$d3" add a.txt
  if HERMIT_SHARED_INDEX_GUARD=block git -C "$d3" commit -q -m "bare block" 2>/dev/null; then
    check "S12 block REFUSES bare commit" "REFUSED" "ALLOWED"
  else
    check "S12 block REFUSES bare commit" "REFUSED" "REFUSED"
  fi

  # off: must be silent
  local d4; d4=$(new_repo s12d); cp "$d/.git/hooks/pre-commit" "$d4/.git/hooks/pre-commit"
  echo a > "$d4/a.txt"; git -C "$d4" add a.txt
  out=$(HERMIT_SHARED_INDEX_GUARD=off git -C "$d4" commit -m "off" 2>&1)
  [[ "$out" == *"SHARED INDEX"* ]] && check "S12 off is SILENT" "silent" "warned" \
                                   || check "S12 off is SILENT" "silent" "silent"
}
echo; echo "=== S12 bracket the SHIPPED guard (extracted from .githooks/pre-commit) ==="
guard_test
echo "FINAL PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]

# --- S13: the argv forms the THREE real parent-committing tool sites use ------
# ci-hub/ci-hub.rs x2 : git -C <root> commit -m MSG -o -- <path>
# scripts/primary_checkout.py : git -C <root> commit --only -m MSG -- <paths>
# If the guard does not treat `-o`/`--only` as a pathspec commit, flipping it to
# block would break `make checkout-fresh` and ci-hub's CI-mode/batch state -- an
# outage, which is exactly what "convert BEFORE you flip" is meant to prevent.
form_test() {
  local parent=/home/newton/work/dev-hermit form d out
  for form in "-o" "--only"; do
    d=$(new_repo "s13${form//-/}")
    mkdir -p "$d/.git/hooks"
    { echo '#!/usr/bin/env bash'
      awk '/^shared_index_guard\(\) \{/,/^\}$/' "$parent/.githooks/pre-commit"
      echo 'if [ "${HERMIT_SHARED_INDEX_GUARD:-warn}" = "block" ]; then shared_index_guard || exit 1; else shared_index_guard || true; fi'
    } > "$d/.git/hooks/pre-commit"
    chmod +x "$d/.git/hooks/pre-commit"
    echo a > "$d/a.txt"; git -C "$d" add a.txt      # foreign staged file
    echo b > "$d/b.txt"; git -C "$d" add b.txt
    if HERMIT_SHARED_INDEX_GUARD=block git -C "$d" commit -q -m msg "$form" -- b.txt 2>/dev/null; then
      check "S13 '$form -- <path>' ALLOWED under block" "ALLOWED" "ALLOWED"
    else
      check "S13 '$form -- <path>' ALLOWED under block" "ALLOWED" "REFUSED"
    fi
    check "S13 '$form' commit contains ONLY b.txt" "b.txt" "$(files_in_head "$d")"
    check "S13 '$form' left the foreign file staged" "A" "$(git -C "$d" diff --cached --name-only | grep -q '^a.txt$' && echo A || echo MISSING)"
  done
}
echo; echo "=== S13 argv forms used by the real parent-committing tool sites ==="
form_test
echo "FINAL PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
