#!/usr/bin/env bash
set -euo pipefail

# Gives the runner container two independent persistence mechanisms and
# verifies both:
#   1. Container restart policy ("always") - the process-death safety net.
#      Handled by start-runner.sh at container-create time; this script only
#      verifies/repairs it.
#   2. A per-container systemd --user unit (Podman only) - the
#      machine-reboot safety net. `systemctl --user enable` makes the
#      container start again after the host reboots and the user logs in
#      (with `loginctl enable-linger` so it starts even without an active
#      login session).
#
# Docker has no equivalent to `podman generate systemd`; on Docker, only the
# restart policy applies, and you are responsible for your own reboot
# persistence (e.g. a cron @reboot line, or migrating to Podman).

CONTAINER_ENGINE="${CONTAINER_ENGINE:-}"
CONTAINER_NAME="${CONTAINER_NAME:-}"
MODE="enable"

if [ "${1:-}" = "--disable" ]; then
  MODE="disable"
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "Usage: ./ensure-runner-autostart.sh [--disable]" >&2
  exit 2
fi
if [ -z "${CONTAINER_ENGINE}" ] || [ -z "${CONTAINER_NAME}" ]; then
  echo "ERROR: CONTAINER_ENGINE and CONTAINER_NAME are required." >&2
  exit 2
fi

engine_name="$(basename -- "${CONTAINER_ENGINE}")"

host_loginctl() {
  if loginctl "$@" 2>/dev/null; then
    return 0
  fi
  systemd-run --quiet --user --wait --pipe --collect \
    --property=KillMode=process loginctl "$@"
}

read_restart_policy() {
  "${CONTAINER_ENGINE}" inspect "${CONTAINER_NAME}" \
    --format '{{.HostConfig.RestartPolicy.Name}}'
}

ensure_restart_policy() {
  local current_policy update_output
  if ! current_policy="$(read_restart_policy)"; then
    echo "ERROR: could not inspect restart policy for ${CONTAINER_NAME}." >&2
    return 1
  fi
  if [ "${current_policy}" = "always" ]; then
    echo "Verified restart policy always for ${CONTAINER_NAME}."
    return 0
  fi

  if ! update_output="$("${CONTAINER_ENGINE}" update --restart=always "${CONTAINER_NAME}" 2>&1)"; then
    echo "ERROR: ${CONTAINER_NAME} restart policy is '${current_policy:-<empty>}' and ${engine_name} could not apply --restart=always in place." >&2
    printf '%s\n' "${update_output}" >&2
    echo "Recreate the container through start-runner.sh; normal starts create it with --restart=always." >&2
    return 1
  fi

  if ! current_policy="$(read_restart_policy)"; then
    echo "ERROR: could not verify restart policy for ${CONTAINER_NAME} after update." >&2
    return 1
  fi
  if [ "${current_policy}" != "always" ]; then
    echo "ERROR: ${engine_name} update returned success but ${CONTAINER_NAME} restart policy is '${current_policy:-<empty>}', not 'always'." >&2
    return 1
  fi
  echo "Applied and verified restart policy always for ${CONTAINER_NAME}."
}

if [ "${MODE}" = "enable" ]; then
  ensure_restart_policy
fi

if [[ "${engine_name}" != podman* ]]; then
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1 || ! command -v loginctl >/dev/null 2>&1; then
  echo "ERROR: Podman reboot auto-start requires systemctl and loginctl." >&2
  exit 1
fi

unit_name="container-${CONTAINER_NAME}.service"
config_root="${XDG_CONFIG_HOME:-${HOME}/.config}"
unit_dir="${config_root}/systemd/user"
unit_path="${unit_dir}/${unit_name}"

if [ "${MODE}" = "disable" ]; then
  systemctl --user disable "${unit_name}" >/dev/null 2>&1 || true
  if [ -f "${unit_path}" ]; then
    rm -f "${unit_path}"
    systemctl --user daemon-reload
  fi
  echo "Disabled boot auto-start for ${CONTAINER_NAME}."
  exit 0
fi

if [ "$(host_loginctl show-user "${USER}" -p Linger --value)" != "yes" ]; then
  host_loginctl enable-linger "${USER}"
fi

mkdir -p "${unit_dir}"
temporary_unit="$(mktemp "${unit_dir}/.${unit_name}.XXXXXX")"
trap 'rm -f "${temporary_unit}"' EXIT
"${CONTAINER_ENGINE}" generate systemd --name --restart-policy on-failure \
  "${CONTAINER_NAME}" > "${temporary_unit}"
chmod 0644 "${temporary_unit}"
mv -f "${temporary_unit}" "${unit_path}"
trap - EXIT

systemctl --user daemon-reload
systemctl --user enable "${unit_name}" >/dev/null
echo "Enabled ${unit_name}; ${CONTAINER_NAME} will start at user-session boot."
