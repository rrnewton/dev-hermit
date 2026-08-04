#!/usr/bin/env bash
# Adapter: maps nightly.sh's positional burst contract (<sha> <width> <timeout>
# <workload>) onto `ci-hub/stress/stress-burst --prebuilt <primary hermit>`. The primary
# checkout is kept on main HEAD, so its prebuilt target/ IS main HEAD — no build,
# no collision with multisect's per-SHA worktrees. <sha> is informational;
# stress-burst reports the checkout's true HEAD as ground truth.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/ci-hub/stress/stress-burst" --prebuilt "$ROOT/hermit" \
  --width "${2:-64}" --timeout "${3:-20}" --workload "$4"
