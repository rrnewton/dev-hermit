#!/usr/bin/env bash
set -euo pipefail

# Starts the self-hosted GitHub Actions runner container.
#
# Default mode is --attach: the container runs in the background, then this
# script follows its logs (good for a tmux pane). Ctrl-C stops the log tail
# only; the runner keeps running. Use ./stop-runner.sh to stop and
# deregister it, or ./drain-runner.sh to ask it to finish its current job and
# exit without deregistering.
#
# --------------------------------------------------------------------------
# HERMIT CAVEAT — READ BEFORE FIRST RUN
#
# hermit intercepts the guest program's syscalls via ptrace and then installs
# its own seccomp-bpf filter on the traced process (see facebookexperimental/
# hermit's README, "How it works"). Both of those actions are blocked by a
# container engine's DEFAULT seccomp profile and unprivileged capability set.
# If hermit's own test suite (`cargo test`) runs on this runner, the
# container runs rootfully with --privileged. Rootless Podman cannot mount the
# fresh sysfs required by Hermit/Reverie, even with SYS_ADMIN and unmask=ALL.
#
# hermit's PMU-based instruction counting (perf_event_open, used to bound how
# long a thread runs before a deterministic context switch) may additionally
# need --cap-add=SYS_ADMIN, or a lowered /proc/sys/kernel/perf_event_paranoid
# on the HOST (outside any container) — that sysctl cannot be set from inside
# a container. If jobs fail with permission errors from perf_event_open even
# with the flags above, check that host sysctl first.
#
# Set the flags you need in .env as CONTAINER_EXTRA_ARGS (see .env.example);
# this script passes it straight through to `podman run` / `docker run`.
# hermit is x86_64-focused (aarch64 support is a work in progress per its
# README as of this writing) — do not expect this on an ARM host.
# --------------------------------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd -- "${RUNNER_CONFIG_DIR:-${SCRIPT_DIR}}" && pwd)"
ENV_FILE="${CONFIG_DIR}/.runner-registration.env"
MODE="attach"
TAIL_LINES="${TAIL_LINES:-200}"
RUN_ONCE="false"
VERIFY_ONLY="false"

usage() {
  cat >&2 <<'EOF'
Usage: ./start-runner.sh [--attach|--detach] [--once] [--verify-only] [--tail N]

--detach keeps the container running but does not follow its logs.
--once starts the official runner in one-job mode: it accepts one job,
  finishes it, and exits instead of returning to the idle polling loop. No
  restart policy or boot auto-start is applied in this mode.
--verify-only audits an already-running container's real cgroup limits
  without changing or restarting it; exits non-zero if the CPU/RAM hard
  caps, swap prohibition, or hard-cap-only memory policy are absent.

Set RUNNER_CPUS / RUNNER_MEMORY in .env or the environment to override the
container's resource limits (defaults: 4 CPUs, 16g RAM — the standard public
GitHub-hosted Linux runner shape).
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --attach) MODE="attach"; shift ;;
    --detach|-d) MODE="detach"; shift ;;
    --once) RUN_ONCE="true"; shift ;;
    --verify-only) VERIFY_ONLY="true"; MODE="detach"; shift ;;
    --tail) TAIL_LINES="${2:-}"; [ -n "${TAIL_LINES}" ] || { echo "--tail requires a line count." >&2; exit 2; }; shift 2 ;;
    --tail=*) TAIL_LINES="${1#--tail=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [ ! -f "${ENV_FILE}" ]; then
  echo "Missing ${ENV_FILE}; run ./init-runner.sh (or 'make init') first." >&2
  exit 1
fi

# Requested overrides must survive sourcing the registration env file, which
# also sets RUNNER_CPUS/RUNNER_MEMORY (the values init-runner.sh recorded).
REQUESTED_RUNNER_CPUS="${RUNNER_CPUS:-}"
REQUESTED_RUNNER_MEMORY="${RUNNER_MEMORY:-}"

# shellcheck disable=SC1090
source "${ENV_FILE}"
if [ -f "${CONFIG_DIR}/.env" ]; then
  # shellcheck disable=SC1091
  source "${CONFIG_DIR}/.env"
fi
STATE_DIR="${STATE_DIR:-${CONFIG_DIR}/state}"
RUNNER_CPUS="${REQUESTED_RUNNER_CPUS:-${RUNNER_CPUS:-4}}"
RUNNER_MEMORY="${REQUESTED_RUNNER_MEMORY:-${RUNNER_MEMORY:-16g}}"
# See the HERMIT CAVEAT comment above; the trusted runner configuration uses:
#   CONTAINER_EXTRA_ARGS="--dns=1.1.1.1 --privileged"
read -r -a EXTRA_RUN_ARGS <<< "${CONTAINER_EXTRA_ARGS:-}"

audit_container_limits() {
  "${CONTAINER_ENGINE}" exec -i \
    "${CONTAINER_NAME}" \
    python3 - \
      --expected-cpus "${RUNNER_CPUS}" \
      --expected-memory "${RUNNER_MEMORY}" \
      < "${SCRIPT_DIR}/verify_runner_limits.py"
}

ensure_durable_start() {
  CONTAINER_ENGINE="${CONTAINER_ENGINE}" CONTAINER_NAME="${CONTAINER_NAME}" \
    "${SCRIPT_DIR}/ensure-runner-autostart.sh"
}

resource_args=(
  --cpus "${RUNNER_CPUS}"
  --memory "${RUNNER_MEMORY}"
)
if [ "${RUN_ONCE}" != "true" ]; then
  # Container-level restart policy is the process-death safety net.
  # ensure-runner-autostart.sh adds the separate machine-reboot safety net
  # (a per-container systemd --user unit).
  resource_args+=(--restart=always)
fi
engine_name="$(basename -- "${CONTAINER_ENGINE}")"
if [[ "${engine_name}" == podman* ]]; then
  # Write cgroup-v2 policy directly: Podman's --memory-swap is confusingly
  # RAM+swap (must be larger than --memory) rather than "swap alone", so this
  # is the reliable way to get a genuinely swapless, non-throttled hard cap.
  resource_args+=(
    --cgroupns=private
    --cgroup-conf=memory.swap.max=0
    --cgroup-conf=memory.high=max
  )
else
  # Docker treats equal --memory and --memory-swap as "no swap".
  resource_args+=(--memory-swap "${RUNNER_MEMORY}")
fi
resource_args+=("${EXTRA_RUN_ARGS[@]}")

if "${CONTAINER_ENGINE}" ps --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  echo "Runner container ${CONTAINER_NAME} is already running."
  if ! audit_container_limits; then
    if [ "${VERIFY_ONLY}" = "true" ]; then
      exit 1
    fi
    echo "Existing runner limits are unsafe; stopping it so it can be recreated correctly." >&2
    "${CONTAINER_ENGINE}" stop "${CONTAINER_NAME}" >/dev/null
  else
    if [ "${VERIFY_ONLY}" = "true" ]; then
      exit 0
    fi
    if [ "${RUN_ONCE}" != "true" ]; then
      ensure_durable_start
    fi
    if [ "${MODE}" = "attach" ]; then
      echo "Following ${CONTAINER_NAME} logs; Ctrl-C stops the log tail, not the runner."
      exec "${CONTAINER_ENGINE}" logs --follow --tail "${TAIL_LINES}" "${CONTAINER_NAME}"
    fi
    exit 0
  fi
fi

if [ "${VERIFY_ONLY}" = "true" ]; then
  echo "ERROR: runner container ${CONTAINER_NAME} is not running; nothing to verify." >&2
  exit 1
fi

RUNNER_ARGS=()
if [ "${RUN_ONCE}" = "true" ]; then
  RUNNER_ARGS+=(--once)
fi

"${CONTAINER_ENGINE}" run -d \
  --name "${CONTAINER_NAME}" \
  --replace \
  "${resource_args[@]}" \
  --volume "${STATE_DIR}:/runner-state:Z" \
  --volume "${SCRIPT_DIR}/verify_runner_limits.py:/opt/ci-runner/verify_runner_limits.py:ro,Z" \
  --workdir /runner-state \
  --env RUNNER_EXPECTED_CPUS="${RUNNER_CPUS}" \
  --env RUNNER_EXPECTED_MEMORY="${RUNNER_MEMORY}" \
  "${RUNNER_IMAGE}" \
  bash -c \
    'python3 /opt/ci-runner/verify_runner_limits.py --expected-cpus "$RUNNER_EXPECTED_CPUS" --expected-memory "$RUNNER_EXPECTED_MEMORY" && exec ./run.sh "$@"' \
    bash "${RUNNER_ARGS[@]}" >/dev/null

for _ in $(seq 1 20); do
  if "${CONTAINER_ENGINE}" ps --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
    break
  fi
  sleep 0.25
done
if ! "${CONTAINER_ENGINE}" ps --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  echo "ERROR: ${CONTAINER_NAME} exited before becoming eligible; last logs follow." >&2
  echo "If this is hermit's test suite failing on ptrace/seccomp denial, see the" >&2
  echo "HERMIT CAVEAT comment at the top of this script and set CONTAINER_EXTRA_ARGS." >&2
  "${CONTAINER_ENGINE}" logs --tail 200 "${CONTAINER_NAME}" 2>&1 >&2 || true
  exit 1
fi

if ! audit_container_limits; then
  echo "ERROR: ${CONTAINER_NAME} failed its post-start cgroup audit; stopping it." >&2
  "${CONTAINER_ENGINE}" stop "${CONTAINER_NAME}" >/dev/null 2>&1 \
    || "${CONTAINER_ENGINE}" kill "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  "${CONTAINER_ENGINE}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  exit 1
fi

if [ "${RUN_ONCE}" != "true" ]; then
  ensure_durable_start
fi

echo "Started ${CONTAINER_NAME}."
echo "Container limits verified: ${RUNNER_CPUS} CPU(s), ${RUNNER_MEMORY} RAM, swap disabled, no memory.high throttle."
if [ "${RUN_ONCE}" = "true" ]; then
  echo "One-job mode enabled: runner exits after completing its next assigned job."
fi
echo "Runner diagnostics are also written under ${STATE_DIR}/_diag/."

if [ "${MODE}" = "attach" ]; then
  echo "Following ${CONTAINER_NAME} logs; Ctrl-C stops the log tail, not the runner."
  exec "${CONTAINER_ENGINE}" logs --follow --tail "${TAIL_LINES}" "${CONTAINER_NAME}"
fi
