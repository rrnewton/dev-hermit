#!/usr/bin/env bash
set -euo pipefail

# Mints a short-lived GitHub Actions runner registration token and configures
# the runner inside its bind-mounted state directory. Run via `make init` (or
# `make start`, which calls this automatically) rather than directly, unless
# you know you want to skip the Makefile's "already configured" check.
#
# Requires: a `gh` CLI logged in with admin rights on OWNER/REPO_NAME (the
# registration-token API needs `repo` admin, not just write access), and a
# populated .env (copy .env.example -> .env first).

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "${SCRIPT_DIR}/.env" ]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/.env"
fi

OWNER="${OWNER:?Set OWNER in .env (see .env.example)}"
REPO_NAME="${REPO_NAME:?Set REPO_NAME in .env (see .env.example)}"
REPO="${OWNER}/${REPO_NAME}"
REPO_URL="https://github.com/${REPO}"

IMAGE="${IMAGE_TAG:-hermit-ci-runner:2.335.1}"
CONTAINER_ENGINE="${CONTAINER_ENGINE:-}"
RUNNER_CPUS="${RUNNER_CPUS:-4}"
RUNNER_MEMORY="${RUNNER_MEMORY:-16g}"
RUNNER_NAME="${RUNNER_NAME:-${REPO_NAME}-ci-$(hostname)}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,x64,${REPO_NAME}}"
CONTAINER_NAME="${CONTAINER_NAME:-${REPO_NAME}-ci-runner}"
ENV_FILE="${SCRIPT_DIR}/.runner-registration.env"
STATE_DIR="${SCRIPT_DIR}/state"

FORCE_RECONFIGURE="false"
if [ "${1:-}" = "--force-reconfigure" ]; then
  FORCE_RECONFIGURE="true"
  shift
fi

if [ -z "${CONTAINER_ENGINE}" ]; then
  if command -v podman >/dev/null 2>&1; then
    CONTAINER_ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then
    CONTAINER_ENGINE=docker
  else
    echo "No container engine found; install podman or docker." >&2
    exit 1
  fi
fi

mkdir -p "${STATE_DIR}"

# Mint the registration token. This is a plain `gh` call — swap in your own
# account-scoped wrapper here (e.g. if you run gh under several accounts on
# one machine and need to pin which one this uses) if you have one; it is
# not required.
token_json="$(gh api -X POST "repos/${REPO}/actions/runners/registration-token")"
RUNNER_TOKEN="$(printf '%s' "${token_json}" | jq -r '.token')"
RUNNER_TOKEN_EXPIRES_AT="$(printf '%s' "${token_json}" | jq -r '.expires_at')"

if [ -z "${RUNNER_TOKEN}" ] || [ "${RUNNER_TOKEN}" = "null" ]; then
  echo "Failed to mint a GitHub runner registration token for ${REPO}." >&2
  echo "Check that 'gh auth status' shows a login with admin rights on ${REPO}." >&2
  exit 1
fi

cat > "${ENV_FILE}" <<EOF
CONTAINER_ENGINE=${CONTAINER_ENGINE}
RUNNER_IMAGE=${IMAGE}
RUNNER_CPUS=${RUNNER_CPUS}
RUNNER_MEMORY=${RUNNER_MEMORY}
CONTAINER_NAME=${CONTAINER_NAME}
REPO=${REPO}
REPO_URL=${REPO_URL}
RUNNER_NAME=${RUNNER_NAME}
RUNNER_LABELS=${RUNNER_LABELS}
RUNNER_TOKEN=${RUNNER_TOKEN}
RUNNER_TOKEN_EXPIRES_AT=${RUNNER_TOKEN_EXPIRES_AT}
STATE_DIR=${STATE_DIR}
EOF
chmod 0600 "${ENV_FILE}"

"${CONTAINER_ENGINE}" run --rm \
  --cpus "${RUNNER_CPUS}" \
  --memory "${RUNNER_MEMORY}" \
  --volume "${STATE_DIR}:/runner-state:Z" \
  --workdir /runner-state \
  --env REPO_URL="${REPO_URL}" \
  --env RUNNER_NAME="${RUNNER_NAME}" \
  --env RUNNER_LABELS="${RUNNER_LABELS}" \
  --env RUNNER_TOKEN="${RUNNER_TOKEN}" \
  --env RUNNER_FORCE_RECONFIGURE="${FORCE_RECONFIGURE}" \
  "${IMAGE}" \
  bash -c '
    set -euo pipefail
    if [ ! -x ./config.sh ]; then
      cp -a /opt/actions-runner/. /runner-state/
    fi
    if [ "${RUNNER_FORCE_RECONFIGURE}" = "true" ]; then
      rm -f .runner .credentials*
    fi
    if [ -f .runner ]; then
      echo "Runner is already configured in /runner-state; remove it with stop-runner.sh first."
      exit 0
    fi
    ./config.sh \
      --url "${REPO_URL}" \
      --token "${RUNNER_TOKEN}" \
      --name "${RUNNER_NAME}" \
      --labels "${RUNNER_LABELS}" \
      --work _work \
      --unattended \
      --replace
  '

echo "Configured ${RUNNER_NAME} for ${REPO_URL} with labels ${RUNNER_LABELS}."
echo "Token expires at ${RUNNER_TOKEN_EXPIRES_AT}; ${ENV_FILE} holds it and must stay gitignored."
