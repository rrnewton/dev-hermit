#!/bin/bash
# Detect artifacts that are PRESENT ON DISK inside a durable artifact directory
# but SILENTLY EXCLUDED by a .gitignore rule.
#
# WHY THIS EXISTS. `git add` on an ignored path does nothing and says nothing
# useful. An agent stores evidence, commits, sees success, and the evidence is
# simply absent from the repository -- so any metric citing it measures against
# references nobody else has. The repo-root `*.log` rule (.gitignore:92) hits
# this constantly: it is right for transient run spools and wrong for the
# golden logs an experiment exists to publish.
#
# This does not decide intent. It makes the exclusion VISIBLE so a human can.
# The fix for a file that SHOULD be stored is a scoped negation next to it, as
# experiments/golden-logs-prefix-depth_20260806/.gitignore does:
#     !goldens/*.log
#
# Usage:
#   scripts/check-ignored-experiment-artifacts.sh            # report, exit 0
#   scripts/check-ignored-experiment-artifacts.sh --strict   # exit 1 if any
#   scripts/check-ignored-experiment-artifacts.sh --dir PATH # limit the scan
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

STRICT=0
DIRS=(experiments ai_docs compat-envelope)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --strict) STRICT=1; shift ;;
        --dir) DIRS=("$2"); shift 2 ;;
        -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

# Two classes of hit are CORRECT and must not be reported, or the signal drowns:
#   1. `ignored/` subtrees -- deliberately machine-local scratch.
#   2. build output -- target/debug/release/deps trees, caches, virtualenvs.
#      Scanning naively reports ~71k of these and the real set disappears.
# What remains is evidence an author plausibly believed was being stored.
BUILD_NOISE='/target/|/debug/|/release/|/deps/|/build/|/__pycache__/|/node_modules/|/\.venv/|/\.git/'
#   3. Hits from a PATH-SPECIFIC rule. If someone wrote a rule naming this
#      directory, the exclusion is deliberate and visible -- that is a decision,
#      not an accident. The silent class is a REPO-WIDE CONTENT rule (a pattern
#      with no `/`, e.g. `*.log`) catching a file nobody wrote a rule about.
#      This is the whole point: the author never expressed an intent here.
mapfile -t hits < <(
    find "${DIRS[@]}" -type f 2>/dev/null \
        | grep -v '/ignored/' \
        | grep -vE "$BUILD_NOISE" \
        | git check-ignore --stdin --verbose 2>/dev/null \
        | awk -F'[:\t]' '$3 !~ /\// { print $4 }'
)

if [[ ${#hits[@]} -eq 0 ]]; then
    echo "OK: no silently-excluded artifacts under ${DIRS[*]}"
    exit 0
fi

echo "SILENTLY EXCLUDED: ${#hits[@]} file(s) present on disk but ignored by git."
echo "These are NOT in the repository. Anything citing them cites a reference"
echo "no one else can resolve. Add a scoped negation where they should be kept."
echo

# Group by owning directory so an owner can act on their own experiment.
printf '%s\n' "${hits[@]}" | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn |
    while read -r count dir; do
        rule=$(git check-ignore -v "$(printf '%s\n' "${hits[@]}" | grep "^$dir/" | head -1)" 2>/dev/null | cut -d: -f1-2)
        printf '  %4d  %s\n        via %s\n' "$count" "$dir" "${rule:-unknown rule}"
    done

echo
echo "To keep a set of them, add a .gitignore beside them with a scoped negation, e.g.:"
echo "    !goldens/*.log"

[[ $STRICT -eq 1 ]] && exit 1
exit 0
