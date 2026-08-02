#!/usr/bin/env bash
# Install the parent dev-hermit repo-hygiene git hooks for THIS clone.
# core.hooksPath is local config (not tracked), so each clone runs this once.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
echo "core.hooksPath -> .githooks (pre-commit hygiene + pre-push main-health warning active)."
echo "Policy: .githooks/hygiene-policy.md"
echo "Override an oversized-but-intended file with: HERMIT_HYGIENE_OVERRIDE=1 git commit ..."
echo "Every push polls GitHub current-main workflow truth; RED warns but does not block a fix."
echo "Every commit warns when a primary checkout is detached or stale; make checkout-fresh repairs clean primaries."
