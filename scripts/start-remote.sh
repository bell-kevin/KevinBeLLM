#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"
remote_dir="${project_dir}/infra/cloudflare"
remote_env="${remote_dir}/.env"
token_file="${remote_dir}/secrets/tunnel-token"

env_value() {
  local file="$1"
  local name="$2"
  sed -n "s/^${name}=//p" "${file}" 2>/dev/null | tail -n 1 | tr -d '\r'
}

wait_for_cloudflared() {
  local port="$1"
  local attempts="${CLOUDFLARED_WAIT_ATTEMPTS:-90}"
  local delay="${CLOUDFLARED_WAIT_DELAY_SECONDS:-2}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --fail --silent --show-error --max-time 5 \
      "http://127.0.0.1:${port}/ready" >/dev/null 2>&1; then
      printf 'Cloudflare Tunnel is ready on its loopback metrics endpoint.\n'
      return 0
    fi
    sleep "${delay}"
  done
  printf 'Cloudflare Tunnel did not become ready after %s attempts.\n' \
    "${attempts}" >&2
  return 1
}

remote_compose() {
  "${project_dir}/scripts/compose.sh" \
    -f "${remote_dir}/compose.yaml" \
    --env-file "${remote_env}" \
    -p kevinbellm-remote-access \
    "$@"
}

cleanup_failed_connector_start() {
  local status="$1"
  trap - EXIT INT TERM
  if (( connector_cleanup_armed )); then
    echo "Cloudflare Tunnel startup did not complete; removing the partial connector." >&2
    remote_compose down --remove-orphans || true
  fi
  exit "${status}"
}

if [[ ! -f "${env_file}" ]]; then
  echo "Run ./scripts/setup.sh first." >&2
  exit 1
fi

if ! grep -Eq '^PUBLIC_URL=https://.+' "${env_file}"; then
  echo "Remote access refused: set PUBLIC_URL to your protected HTTPS hostname in .env." >&2
  exit 1
fi

if [[ ! -f "${remote_env}" ]]; then
  echo "Remote access refused: copy infra/cloudflare/.env.example to infra/cloudflare/.env and review it." >&2
  exit 1
fi

if [[ ! -s "${token_file}" ]] || [[ "$(stat -c '%a' "${token_file}")" != "600" ]]; then
  echo "Remote access refused: infra/cloudflare/secrets/tunnel-token must exist, be nonempty, and have mode 600." >&2
  exit 1
fi

metrics_port="$(env_value "${remote_env}" CLOUDFLARED_METRICS_PORT)"
metrics_port="${metrics_port:-20241}"
if [[ ! "${metrics_port}" =~ ^[0-9]+$ ]] || \
   (( ${#metrics_port} > 5 )) || \
   (( 10#${metrics_port} < 1 || 10#${metrics_port} > 65535 )); then
  echo "Remote access refused: CLOUDFLARED_METRICS_PORT must be an integer from 1 to 65535." >&2
  exit 1
fi

"${project_dir}/scripts/start.sh"

connector_cleanup_armed=1
trap 'cleanup_failed_connector_start $?' EXIT
trap 'cleanup_failed_connector_start 130' INT
trap 'cleanup_failed_connector_start 143' TERM

remote_compose up --detach

wait_for_cloudflared "${metrics_port}"

connector_cleanup_armed=0
trap - EXIT INT TERM
echo "Authenticated remote tunnel started for the PUBLIC_URL in .env."
