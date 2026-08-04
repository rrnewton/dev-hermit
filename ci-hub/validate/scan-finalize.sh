#!/usr/bin/env bash
# scan-finalize.sh -- consume-path wiring for finalize_receipt.py --scan.
#
# WHY: hermit's validate.sh writes a count-less schema-3 receipt whenever it
# cannot reach the parent count helper (DEV_HERMIT_PARENT unset in the slot /
# systemd-run producer env -> executed_tests/filtered_tests stay null). With the
# uncounted-receipt grandfather REMOVED (ci-hub/lib/validate_status.rs), such a
# receipt is NotValidated. This helper re-mints a count-backed schema-5 row for
# every count-less clean/full/pass green from that green's OWN durable log,
# BEFORE a landing consumer reads the ledger -- so a genuine green is never
# stranded merely because the producer failed to inline its counts.
#
# SAFETY: finalize_receipt.py --scan is APPEND-ONLY (O_APPEND) and idempotent (a
# sha already carrying a satisfied schema-5 row is skipped), so it races no
# concurrent validate.sh appender and can run on every landing. It NEVER
# fabricates: a row whose log is gone ("no-log") or whose manifest is not
# derivable at that sha ("no-manifest") is reported and skipped, not minted. It
# is best-effort: this script always exits 0 -- the authoritative gate remains
# validate-status, which fail-closes (NotValidated) if minting did not happen.
#
# Usage: scan-finalize.sh --hermit-checkout <path> [--ledger <path>]
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)

hermit_checkout=""
ledger="${CI_HUB_VALIDATE_LEDGER:-$ROOT/ignored/validate-run-ledger.jsonl}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --hermit-checkout) hermit_checkout=$2; shift 2 ;;
    --ledger) ledger=$2; shift 2 ;;
    *) echo "scan-finalize: unknown arg '$1'" >&2; exit 0 ;;
  esac
done

finalizer="$ROOT/ci-hub/validate/finalize_receipt.py"

if [ -z "$hermit_checkout" ]; then
  echo "scan-finalize: SKIP (no --hermit-checkout; cannot derive per-node manifests)"
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "scan-finalize: SKIP (python3 unavailable)"; exit 0
fi
if [ ! -r "$finalizer" ]; then
  echo "scan-finalize: SKIP (finalizer not found at $finalizer)"; exit 0
fi
if [ ! -f "$ledger" ]; then
  echo "scan-finalize: SKIP (ledger not found at $ledger)"; exit 0
fi
if [ ! -d "$hermit_checkout/.git" ] && [ ! -f "$hermit_checkout/.git" ]; then
  echo "scan-finalize: SKIP (hermit checkout $hermit_checkout is not a git repo)"; exit 0
fi

out=$(python3 "$finalizer" --scan --ledger "$ledger" --hermit-checkout "$hermit_checkout" 2>&1)
rc=$?
printf '%s\n' "$out" | sed 's/^/scan-finalize: /'
if [ "$rc" -ne 0 ]; then
  echo "scan-finalize: finalize_receipt --scan rc=$rc (best-effort; validate-status remains authoritative)"
fi
exit 0
