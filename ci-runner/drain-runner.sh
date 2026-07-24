#!/usr/bin/env bash
set -euo pipefail

# Asks an already-running runner container to exit its listener loop
# gracefully (SIGINT, the same signal Ctrl-C sends a foreground runner), then
# follows logs until it exits unless --no-follow is set. This does NOT
# deregister the runner from GitHub — ./start-runner.sh brings the same
# registration back later, or ./stop-runner.sh stops and deregisters it.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd -- "${RUNNER_CONFIG_DIR:-${SCRIPT_DIR}}" && pwd)"
RUNNER_SLOT="${RUNNER_SLOT:-}"
if [[ ! "${RUNNER_SLOT}" =~ ^[A-Za-z0-9_.-]*$ ]]; then
  echo "RUNNER_SLOT may only contain letters, digits, underscore, dot, and dash." >&2
  exit 2
fi
SLOT_SUFFIX="${RUNNER_SLOT:+-${RUNNER_SLOT}}"
ENV_FILE="${CONFIG_DIR}/.runner-registration${SLOT_SUFFIX}.env"
TAIL_LINES="${TAIL_LINES:-200}"
FOLLOW_LOGS="true"

usage() {
  cat >&2 <<'EOF'
Usage: ./drain-runner.sh [--no-follow] [--tail N]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tail) TAIL_LINES="${2:-}"; [ -n "${TAIL_LINES}" ] || { echo "--tail requires a line count." >&2; exit 2; }; shift 2 ;;
    --tail=*) TAIL_LINES="${1#--tail=}"; shift ;;
    --no-follow) FOLLOW_LOGS="false"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [ ! -f "${ENV_FILE}" ]; then
  echo "Missing ${ENV_FILE}; nothing to drain."
  exit 0
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

if ! "${CONTAINER_ENGINE}" ps --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  echo "Runner container ${CONTAINER_NAME} is not running."
  exit 0
fi

echo "Requesting graceful runner shutdown for ${CONTAINER_NAME}."
echo "The official runner leaves the polling loop on SIGINT; if a job is active, watch the logs for it to finish first."
"${CONTAINER_ENGINE}" kill --signal INT "${CONTAINER_NAME}" >/dev/null

if [ "${FOLLOW_LOGS}" != "true" ]; then
  echo "Graceful shutdown requested for ${CONTAINER_NAME}; not following logs."
  exit 0
fi

echo "Following ${CONTAINER_NAME} logs until it exits."
"${CONTAINER_ENGINE}" logs --follow --tail "${TAIL_LINES}" "${CONTAINER_NAME}" || true

if "${CONTAINER_ENGINE}" ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  status="$("${CONTAINER_ENGINE}" inspect --format '{{.State.Status}}' "${CONTAINER_NAME}" 2>/dev/null || true)"
  if [ "${status}" = "exited" ] || [ "${status}" = "created" ]; then
    "${CONTAINER_ENGINE}" rm "${CONTAINER_NAME}" >/dev/null
    echo "Runner container ${CONTAINER_NAME} exited and was removed. Registration remains configured."
  else
    echo "Runner container ${CONTAINER_NAME} status is ${status:-unknown}; leaving it in place."
  fi
fi
