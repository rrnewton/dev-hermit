#!/usr/bin/env bash
#
# Bracket harness for the .githooks/pre-commit BINARY-TYPE guard.
#
# Brackets BOTH directions and prints counts on both sides, because a gate that
# only ever refuses is indistinguishable from a gate that refuses everything:
#   NEGATIVE  plant a compiled binary -> the commit must be REFUSED
#   POSITIVE  plant legitimate small text -> the commit must still SUCCEED
#
# ISOLATION. Every case runs in a throwaway `git init` repo under a temp dir.
# Nothing is ever staged in the real dev-hermit parent: that parent has ONE
# shared index across ~18 agents, so staging a test fixture there would be the
# exact hazard the shared-index guard exists to prevent. The temp repo also
# cannot authorize anything -- it has no remote, no policy files, no submodules.
#
# Usage:  .githooks/tests/test-binary-guard.sh            (from the parent root)
# Exit:   0 = all cases behaved as specified, 1 = at least one did not.

set -u

HOOK_SRC="${1:-.githooks/pre-commit}"
if [ ! -f "$HOOK_SRC" ]; then
  echo "FATAL: hook not found at '$HOOK_SRC' (run from the dev-hermit parent root)" >&2
  exit 2
fi
HOOK_SRC=$(cd "$(dirname "$HOOK_SRC")" && pwd)/$(basename "$HOOK_SRC")

TMPROOT=$(mktemp -d "${TMPDIR:-/tmp}/w10-binguard-XXXXXX") || exit 2
trap 'rm -rf "$TMPROOT"' EXIT

PASS=0; FAIL=0; NEG_OK=0; POS_OK=0
declare -a FAILURES=()

# Build the reference fixtures once, in the temp dir.
FIX="$TMPROOT/fixtures"; mkdir -p "$FIX"
HAVE_REAL_ELF=0
printf 'int main(void){return 0;}\n' > "$FIX/t.c"
if command -v gcc >/dev/null 2>&1 && gcc -O0 -o "$FIX/real_elf" "$FIX/t.c" 2>/dev/null; then
  HAVE_REAL_ELF=1
fi
if command -v gcc >/dev/null 2>&1 && command -v ar >/dev/null 2>&1 \
   && gcc -c -o "$FIX/t.o" "$FIX/t.c" 2>/dev/null && ar rcs "$FIX/real_ar.a" "$FIX/t.o" 2>/dev/null; then
  HAVE_REAL_AR=1
else
  HAVE_REAL_AR=0
fi
# Magic-only synthetics, so the harness still brackets without a toolchain.
printf '\177ELF\002\001\001\000synthetic-not-a-real-elf\n' > "$FIX/synth_elf"
printf '!<arch>\nsynthetic-not-a-real-archive\n'            > "$FIX/synth_ar"

# run_case <expect: refuse|allow> <name> <env-assignments...> -- <file:content-source>...
# Each fixture arg is "destname=srcpath" (copied) or "destname:@text" (written).
run_case() {
  local expect="$1" name="$2"; shift 2
  local -a envs=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  shift || true

  local repo="$TMPROOT/repo.$$.$RANDOM"; mkdir -p "$repo/.githooks"
  git -C "$repo" init -q 2>/dev/null
  git -C "$repo" config user.email w10@test.local
  git -C "$repo" config user.name  "binary guard bracket"
  git -C "$repo" config core.hooksPath .githooks
  cp "$HOOK_SRC" "$repo/.githooks/pre-commit"; chmod +x "$repo/.githooks/pre-commit"
  # Seed a commit so the hook compares against a real HEAD.
  echo seed > "$repo/seed.txt"; git -C "$repo" add seed.txt
  git -C "$repo" -c core.hooksPath=/dev/null commit -qm seed 2>/dev/null

  local spec dest src
  for spec in "$@"; do
    dest="${spec%%=*}"; src="${spec#*=}"
    mkdir -p "$repo/$(dirname "$dest")"
    case "$src" in
      @*) printf '%s\n' "${src#@}" > "$repo/$dest" ;;
      *)  cp "$src" "$repo/$dest" ;;
    esac
    git -C "$repo" add -- "$dest"
  done

  local out rc
  out=$(cd "$repo" && env HERMIT_SHARED_INDEX_GUARD=off "${envs[@]}" \
          git commit -m "bracket: $name" 2>&1); rc=$?

  local got; [ "$rc" -eq 0 ] && got=allow || got=refuse
  if [ "$got" = "$expect" ]; then
    PASS=$((PASS+1))
    [ "$expect" = refuse ] && NEG_OK=$((NEG_OK+1)) || POS_OK=$((POS_OK+1))
    printf '  ok    %-8s %s\n' "[$expect]" "$name"
  else
    FAIL=$((FAIL+1))
    FAILURES+=("$name (expected $expect, got $got)")
    printf '  FAIL  %-8s %s   <-- expected %s, got %s (rc=%d)\n' "[$expect]" "$name" "$expect" "$got" "$rc"
    printf '%s\n' "$out" | sed 's/^/          | /' | head -12
  fi
  rm -rf "$repo"
}

echo "=== BINARY-TYPE GUARD BRACKET ==="
echo "hook: $HOOK_SRC"
echo "real ELF fixture available (gcc): $HAVE_REAL_ELF ; real ar fixture: $HAVE_REAL_AR"
echo

echo "-- NEGATIVE: compiled binaries must be REFUSED (any size) --"
if [ "$HAVE_REAL_ELF" = 1 ]; then
  run_case refuse "real compiled ELF ($(wc -c < "$FIX/real_elf") bytes) -- the clean_trivial shape" \
    -- "mutants/clean_trivial=$FIX/real_elf"
  run_case refuse "same ELF renamed .md -- type check, not extension" \
    -- "docs/looks_like_notes.md=$FIX/real_elf"
  run_case refuse "ELF is refused even under HERMIT_HYGIENE_OVERRIDE=1 (flags must not collapse)" \
    HERMIT_HYGIENE_OVERRIDE=1 -- "mutants/clean_trivial=$FIX/real_elf"
  run_case refuse "one ELF hidden among 3 legitimate text files" \
    -- "a.md=@fine" "b.csv=@x,y" "mutants/mut_addr=$FIX/real_elf" "c.txt=@also fine"
fi
[ "$HAVE_REAL_AR" = 1 ] && run_case refuse "real ar archive (.a static library)" \
  -- "lib/libt.a=$FIX/real_ar.a"
run_case refuse "synthetic \\x7fELF magic" -- "x/synth=$FIX/synth_elf"
run_case refuse "synthetic !<arch> magic"  -- "x/synth.a=$FIX/synth_ar"

echo
echo "-- POSITIVE: legitimate small text must still COMMIT (guard is not inert) --"
run_case allow "small markdown report"            -- "ai_docs/report.md=@# Findings"
run_case allow "small CSV results"                -- "experiments/e/results.csv=@case,rc"
run_case allow "text file NAMED .o"               -- "notes.o=@this is text, not an object file"
run_case allow "text file NAMED .a"               -- "notes.a=@also text"
run_case allow "text starting with the letters ELF (magic must be anchored on \\x7f)" \
  -- "elf.md=@ELF is a file format"
run_case allow "shell script (#!)"                -- "scripts/x.sh=@#!/bin/sh"
run_case allow "empty file"                       -- "empty.txt=@"
run_case allow "several text files at once"       -- "p.md=@one" "q.csv=@a,b" "r.json=@{}"

echo
echo "-- REGRESSION: the pre-existing SIZE gate must still behave --"
python3 - "$FIX/big.txt" <<'PY' 2>/dev/null || head -c 1200000 /dev/zero | tr '\0' 'x' > "$FIX/big.txt"
import sys
open(sys.argv[1], "w").write("x" * 1_200_000)
PY
run_case refuse "1.2 MB text file exceeds the size limit" -- "big.txt=$FIX/big.txt"
run_case allow  "...and HERMIT_HYGIENE_OVERRIDE=1 still releases the SIZE gate" \
  HERMIT_HYGIENE_OVERRIDE=1 -- "big.txt=$FIX/big.txt"

echo
echo "-- ESCAPE HATCH: the gate must be releasable, or 'refuse' proves nothing --"
if [ "$HAVE_REAL_ELF" = 1 ]; then
  run_case allow "HERMIT_BINARY_GUARD=off lets the same ELF through" \
    HERMIT_BINARY_GUARD=off -- "mutants/clean_trivial=$FIX/real_elf"
fi

echo
echo "======================================================================"
echo "negative cases passed (correctly REFUSED): $NEG_OK"
echo "positive cases passed (correctly ALLOWED): $POS_OK"
echo "total: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"
  echo "======================================================================"
  exit 1
fi
echo "ALL CASES BEHAVED AS SPECIFIED"
echo "======================================================================"
exit 0
