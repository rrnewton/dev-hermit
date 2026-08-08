#!/usr/bin/env bash
# Exact-head local-validation authority for the lander. The GitHub label is a
# cache of this result, never an independent authorization signal.
set -uo pipefail

usage() {
  echo "usage: local-validation-eligibility.sh <40-hex-sha> [observed-labels]" >&2
  echo "                                       [--pr <number> --repo <owner/repo>]" >&2
}

sha=
observed_labels=
pr=
repo=
positional=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --pr)   pr=${2:-};   shift 2 || { usage; exit 2; } ;;
    --repo) repo=${2:-}; shift 2 || { usage; exit 2; } ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "eligibility: unknown flag $1" >&2; usage; exit 2 ;;
    *)
      positional=$((positional + 1))
      case "$positional" in
        1) sha=$1 ;;
        2) observed_labels=$1 ;;
        *) usage; exit 2 ;;
      esac
      shift
      ;;
  esac
done
if [ "$positional" -lt 1 ]; then usage; exit 2; fi
if [ -n "$pr" ] && [ -z "$repo" ]; then
  echo "eligibility: --pr requires --repo" >&2; exit 2
fi
if [ -n "$repo" ] && [ -z "$pr" ]; then
  echo "eligibility: --repo requires --pr" >&2; exit 2
fi
case "$sha" in
  *[!0-9a-f]*|'')
    echo "eligibility: SHA must be lowercase hexadecimal" >&2
    exit 2
    ;;
esac
if [ "${#sha}" -ne 40 ]; then
  echo "eligibility: SHA must be exactly 40 hexadecimal characters" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$script_dir/../.." && pwd)

# ---------------------------------------------------------------- subject binding
#
# A receipt answers "is THIS SHA validated". It does NOT answer "is the SHA I was
# handed the one this PR would merge". Those are different questions, and until
# now only the first was ever asked: the caller supplied the subject and the
# predicate trusted it. hermit#1635 is what that costs -- a genuine
# `executed_tests = 862, delta +0` receipt carrying
# commit=61edbef4 (hermit MAIN's tip, produced in the PRIMARY slot) was quoted as
# evidence for a PR whose head was 291a2fd6. The number was real and attested
# nothing about the change. Every landing gate passed, because none of them
# compared the receipt's subject to the PR head.
#
# So bind the subject HERE, inside the verifier, where it is observable -- not in
# the caller. `land-pr.sh` derives its SHA from `git rev-parse origin/<branch>` in
# a local worktree, which is a proxy for the PR head, not the PR head.
if [ -n "$pr" ]; then
  if [ -n "${CI_HUB_PR_HEAD_OVERRIDE:-}" ]; then
    # Test seam only: lets the bracket plant a head without touching live state.
    live_head=$CI_HUB_PR_HEAD_OVERRIDE
    head_rc=0
  else
    live_head=$(with-proxy gh pr view "$pr" -R "$repo" \
      --json headRefOid -q .headRefOid 2>/dev/null)
    head_rc=$?
  fi
  live_head=$(printf '%s' "$live_head" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
  if [ "$head_rc" -ne 0 ] || [ "${#live_head}" -ne 40 ]; then
    # Fail CLOSED. An unresolvable head is "nothing proven", and nothing proven
    # is not a pass -- it must never fall through to the ledger lookup, because
    # the ledger would happily answer about whatever SHA it was handed.
    echo "ELIGIBILITY=ERROR cannot resolve live head for PR #$pr in $repo" >&2
    exit 2
  fi
  if [ "$live_head" != "$sha" ]; then
    printf 'SUBJECT_SHA=%s\n' "$sha"
    printf 'PR_HEAD=%s (PR #%s in %s)\n' "$live_head" "$pr" "$repo"
    echo "ELIGIBILITY=SUBJECT_MISMATCH the receipt subject is not this PR's head --" \
         "a receipt for $sha attests nothing about $live_head" >&2
    exit 5
  fi
  printf 'SUBJECT_BOUND=%s == PR #%s head\n' "$sha" "$pr"
else
  printf 'SUBJECT_UNBOUND=%s (no --pr; caller supplied the subject)\n' "$sha"
fi

status_bin=${CI_HUB_VALIDATE_STATUS_BIN:-$root/ci-hub/ci-hub}
status_args=(validate-status --sha "$sha")
if [ -n "${CI_HUB_VALIDATE_LEDGER:-}" ]; then
  status_args+=(--ledger "$CI_HUB_VALIDATE_LEDGER")
fi

status_output=$("$status_bin" "${status_args[@]}" 2>&1)
status_rc=$?

printf 'LABEL_CACHE=%s (observed only; non-authoritative)\n' \
  "${observed_labels:-<absent>}"
printf '%s\n' "$status_output"
case "$status_rc" in
  0)
    echo "ELIGIBILITY=VALIDATED"
    exit 0
    ;;
  3)
    echo "ELIGIBILITY=KNOWN_FAILED"
    exit 3
    ;;
  4)
    echo "ELIGIBILITY=NOT_VALIDATED"
    exit 4
    ;;
  *)
    echo "ELIGIBILITY=ERROR validate-status-rc=$status_rc" >&2
    exit 2
    ;;
esac
