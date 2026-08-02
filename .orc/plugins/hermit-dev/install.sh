#!/usr/bin/env bash
set -euo pipefail

STALE_HOME_COPY="${HOME}/.orc/plugins/hermit-dev"
if [[ -e "$STALE_HOME_COPY" || -L "$STALE_HOME_COPY" ]]; then
    echo "ERROR: stale home plugin copy exists: $STALE_HOME_COPY" >&2
    echo "Remove it; ORC must load the version-controlled project plugin." >&2
    exit 1
fi

echo "No home plugin copy found. Start ORC from the dev-hermit workspace."
echo 'The tracked .orc/config.js imports ./plugins/hermit-dev/index.ts.'
