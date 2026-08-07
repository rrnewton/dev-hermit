#!/usr/bin/env bash
# Bracketed tests for the shared-branch rewind compare-and-swap guard.
#
# Every case runs in a throwaway repo under a tmpdir. Nothing here touches the
# shared parent, its primaries, or any real branch — a test for a destructive-op
# guard must not itself be the destructive op.
#
# The two halves matter equally. A guard that never fires is useless; a guard
# that fires on ordinary work gets disabled and is WORSE than none, because it
# also trains people to ignore it. So the positive half is not decoration.

set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/reference-transaction"
WRITER="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/parent-main-write"
[ -x "$HOOK" ] || { echo "FATAL: hook not executable at $HOOK"; exit 1; }
[ -x "$WRITER" ] || { echo "FATAL: writer not executable at $WRITER"; exit 1; }

pass=0; fail=0
ok()   { pass=$((pass+1)); echo "  PASS  $1"; }
bad()  { fail=$((fail+1)); echo "  FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; }

new_repo() {
  local d; d=$(mktemp -d)
  git init -q -b main "$d"
  mkdir -p "$d/.githooks"
  cp "$HOOK" "$d/.githooks/reference-transaction"
  chmod +x "$d/.githooks/reference-transaction"
  mkdir -p "$d/scripts"
  cp "$WRITER" "$d/scripts/parent-main-write"
  chmod +x "$d/scripts/parent-main-write"
  git -C "$d" config core.hooksPath .githooks
  git -C "$d" config user.name t
  git -C "$d" config user.email t@invalid
  printf '%s\n' "$d"
}
commit() { git -C "$1" commit -q --allow-empty -m "$2"; }
tip()    { git -C "$1" rev-parse HEAD; }

# ---------------------------------------------------------------- NEGATIVE 1
# The incident, replayed exactly: A commits, B commits on top, B resets twice.
# Reset 1 removes B's own commit. Reset 2 would remove A's — and must not.
r=$(new_repo)
commit "$r" base
commit "$r" "A: agent A's work"; A=$(tip "$r")
commit "$r" "B: agent B's work"; B=$(tip "$r")
out=$(HERMIT_RESET_EXPECT="$B" git -C "$r" reset --hard HEAD~1 2>&1)
if [ "$(tip "$r")" = "$A" ]; then
  ok "incident reset 1 (B removes B's OWN commit, tip declared) is ALLOWED"
else
  bad "incident reset 1 should be allowed" "tip=$(tip "$r") expected=$A"
fi
# B now resets again, still believing HEAD~1 is theirs. The tip is A's commit.
out=$(HERMIT_RESET_EXPECT="$B" git -C "$r" reset --hard HEAD~1 2>&1)
if [ "$(tip "$r")" = "$A" ]; then
  ok "incident reset 2 (would take agent A's commit) is REFUSED"
else
  bad "incident reset 2 must be refused" "tip moved to $(tip "$r")"
fi
# Grep the SUBJECT, not the SHA: in this reset the dropped commit IS the tip, so
# a SHA match is also satisfied by the "branch tip is <old>" line and would pass
# with the dropped-commit list empty. (A mutation sweep proved exactly that.)
if grep -q "agent A's work" <<<"$out"; then
  ok "refusal LISTS the commit it protected, by subject (${A:0:12})"
else
  bad "refusal must enumerate the dropped commits, not just the tip" "$out"
fi
if grep -q "git revert" <<<"$out" && grep -q "HERMIT_RESET_EXPECT" <<<"$out"; then
  ok "refusal states both safe forms"
else
  bad "refusal must tell the caller what to do instead"
fi
rm -rf "$r"

# ---------------------------------------------------------------- NEGATIVE 2
# No declaration at all — the default path an unaware caller takes.
# (An earlier draft of this block asserted both branches with bad(), i.e. it
# could only ever fail. Deleted rather than patched: a check that cannot pass
# is as worthless as one that cannot fail, and the surviving block below tests
# the property correctly.)
r=$(new_repo)
commit "$r" base; commit "$r" a; commit "$r" b; T=$(tip "$r")
git -C "$r" reset --hard HEAD~1 >/dev/null 2>&1
if [ "$(tip "$r")" = "$T" ]; then
  ok "a rewind with NO declaration is refused (fail-closed default)"
else
  bad "undeclared rewind slipped through" "tip=$(tip "$r")"
fi
rm -rf "$r"

# NEGATIVE 3 (branch deletion) REMOVED. git reports a deletion as
# `0000000 -> 0000000` with no old value, so the hook cannot see it and the
# arm that claimed to block it was unreachable. Removed from both the hook and
# this file rather than left as a test that can only pass vacuously.

# ---------------------------------------------------------------- POSITIVE 1
# The legitimate self-reset the task requires be preserved.
r=$(new_repo)
commit "$r" base; P=$(tip "$r"); commit "$r" mine; M=$(tip "$r")
out=$(HERMIT_RESET_EXPECT="$M" git -C "$r" reset --hard HEAD~1 2>&1)
if [ "$(tip "$r")" = "$P" ]; then
  ok "legitimate self-reset (tip IS the declared commit) is ALLOWED"
else
  bad "legitimate self-reset was blocked — this is the mute-the-guard failure" "$out"
fi
rm -rf "$r"

# ---------------------------------------------------------------- POSITIVE 2
# Ordinary work must be completely silent, or the guard gets turned off.
r=$(new_repo)
noise=$(commit "$r" base 2>&1; commit "$r" two 2>&1; git -C "$r" tag -a v1 -m v1 2>&1)
if [ -z "$noise" ]; then
  ok "ordinary commits and tagging produce NO output from the guard"
else
  bad "the guard is noisy on ordinary work" "$noise"
fi
rm -rf "$r"

# ---------------------------------------------------------------- POSITIVE 3
# Fast-forward: it remains allowed, but now only through the serialized sync
# path so another local writer cannot append between fetch and ref update.
r=$(new_repo); u=$(new_repo)
commit "$u" base
git -C "$r" remote add origin "$u" >/dev/null 2>&1
git -C "$r" fetch -q origin main 2>/dev/null
git -C "$r" checkout -q -B main FETCH_HEAD
commit "$u" ahead
git -C "$r" fetch -q origin main 2>/dev/null
out=$(cd "$r" && HERMIT_PARENT_MAIN_NO_PROXY=1 \
  HERMIT_PARENT_MAIN_LOCK_PATH="$r/parent-main.lock" \
  scripts/parent-main-write sync 2>&1)
if [ "$(tip "$r")" = "$(tip "$u")" ]; then
  ok "serialized fast-forward to the remote tip is allowed"
else
  bad "fast-forward was blocked" "$out"
fi
rm -rf "$r" "$u"

# ---------------------------------------------------------------- POSITIVE 4
# Feature branches are where agents actually work; they must not be gated.
r=$(new_repo)
commit "$r" base
git -C "$r" checkout -q -b feature/mine
commit "$r" x; commit "$r" y; Y=$(tip "$r")
git -C "$r" reset --hard HEAD~1 >/dev/null 2>&1
if [ "$(tip "$r")" != "$Y" ]; then
  ok "resetting a NON-shared branch is untouched"
else
  bad "a feature-branch reset was blocked; agents would disable the hook"
fi
rm -rf "$r"

# ---------------------------------------------------------------- POSITIVE 5
# Detached HEAD is the normal shape of the isolated publish worktrees.
r=$(new_repo)
commit "$r" base; commit "$r" a; commit "$r" b
git -C "$r" checkout -q --detach HEAD
git -C "$r" reset --hard HEAD~1 >/dev/null 2>&1
if [ "$(git -C "$r" rev-list --count HEAD)" = "2" ]; then
  ok "detached-HEAD reset is untouched (no branch ref moves)"
else
  bad "detached-HEAD reset was blocked"
fi
rm -rf "$r"

# ---------------------------------------------------------------- CONFIG
r=$(new_repo)
git -C "$r" config --add hermit.sharedbranch release
git -C "$r" checkout -q -b release
commit "$r" base; commit "$r" a; A2=$(tip "$r")
git -C "$r" reset --hard HEAD~1 >/dev/null 2>&1
if [ "$(tip "$r")" = "$A2" ]; then
  ok "an additional configured shared branch is protected too"
else
  bad "hermit.sharedbranch config was ignored"
fi
rm -rf "$r"

echo
echo "reset-CAS-guard: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
