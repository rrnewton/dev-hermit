#!/bin/bash
# Probes the namespace syscalls a nix build needs, from inside whatever
# environment this script is run in. Prints one `name=OK|EPERM|<errno text>` per
# line so the caller can diff environments.
for spec in "user:--user" "mount:--mount" "pid:--pid --fork"; do
  name=${spec%%:*}; flags=${spec#*:}
  # shellcheck disable=SC2086
  if out=$(unshare $flags true 2>&1); then echo "unshare_$name=OK"
  else echo "unshare_$name=${out##*: }"; fi
done
