#!/usr/bin/env bash
# Install (or refresh) the hermit-dev ORC plugin into ~/.orc/plugins/.
#
# ORC's module sandbox rejects a *symlinked* plugin directory: the
# auto-generated orc_plugin_loader.js does `import "./index.ts"`, and through a
# symlink that resolves to a real path outside ~/.orc ("escapes managed module
# roots"), so the plugin fails to load. We therefore install a REAL COPY.
#
# Re-run this after editing the plugin source to refresh the installed copy.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST="${HOME}/.orc/plugins/hermit-dev"

mkdir -p "${HOME}/.orc/plugins"

# If a stale symlink is present, remove just the link (never its target).
if [ -L "${DST}" ]; then
  rm "${DST}"
fi

mkdir -p "${DST}"

# Copy the files ORC needs; skip stray *.orig backups and this installer.
for f in index.ts package.json gh-issue-create README.md; do
  cp -p "${SRC}/${f}" "${DST}/${f}"
done

echo "Installed hermit-dev plugin (real copy) -> ${DST}"
echo "Ensure ~/.orc/config.js contains: orc.loadPlugin(\"hermit-dev\");"
