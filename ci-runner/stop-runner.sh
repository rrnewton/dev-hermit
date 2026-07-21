#!/usr/bin/env bash
set -euo pipefail

# Stops the runner container and deregisters it from GitHub (mints a
# short-lived removal token via `gh api`, same account-swap note as
# init-runner.sh applies here). This is the "tear it down" path; use
# ./drain-runner.sh instead if you just want it to finish its current job and
# stop without losing the GitHub registration.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.runner-registration.env"

if [ ! -f "${ENV_FILE}" ]; then
  echo "Missing ${ENV_FILE}; nothing to stop."
  exit 0
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"
STATE_DIR="${STATE_DIR:-${SCRIPT_DIR}/state}"

CONTAINER_ENGINE="${CONTAINER_ENGINE}" CONTAINER_NAME="${CONTAINER_NAME}" \
  "${SCRIPT_DIR}/ensure-runner-autostart.sh" --disable

if "${CONTAINER_ENGINE}" ps --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  echo "Stopping ${CONTAINER_NAME}; allowing time for the runner process to exit cleanly."
  "${CONTAINER_ENGINE}" stop --time 120 "${CONTAINER_NAME}" >/dev/null
fi

if "${CONTAINER_ENGINE}" ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  "${CONTAINER_ENGINE}" rm "${CONTAINER_NAME}" >/dev/null
fi

if [ -f "${STATE_DIR}/.runner" ]; then
  remove_json="$(gh api -X POST "repos/${REPO}/actions/runners/remove-token")"
  RUNNER_REMOVE_TOKEN="$(printf '%s' "${remove_json}" | jq -r '.token')"
  if [ -z "${RUNNER_REMOVE_TOKEN}" ] || [ "${RUNNER_REMOVE_TOKEN}" = "null" ]; then
    echo "Failed to mint a GitHub runner removal token for ${REPO}." >&2
    exit 1
  fi

  "${CONTAINER_ENGINE}" run --rm \
    --volume "${STATE_DIR}:/runner-state:Z" \
    --workdir /runner-state \
    --env RUNNER_REMOVE_TOKEN="${RUNNER_REMOVE_TOKEN}" \
    "${RUNNER_IMAGE}" \
    bash -c '
      set -euo pipefail
      if [ -f .runner ]; then
        ./config.sh remove --unattended --token "${RUNNER_REMOVE_TOKEN}"
      else
        echo "Runner state is already unconfigured."
      fi
    '
fi

echo "Runner stopped and deregistered."
