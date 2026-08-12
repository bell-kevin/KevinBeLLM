#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"
marker_file="${project_dir}/.bootstrap-complete"

if [[ -f "${marker_file}" ]]; then
  exit 0
fi
if [[ ! -f "${env_file}" ]]; then
  echo "Run ./scripts/setup.sh before bootstrapping the account." >&2
  exit 1
fi

# Read only the three exact bootstrap keys. Values stay out of argv, Compose
# output, the long-running service environment, and the repository.
admin_name="$(sed -n 's/^ADMIN_NAME=//p' "${env_file}")"
admin_email="$(sed -n 's/^ADMIN_EMAIL=//p' "${env_file}")"
admin_password="$(sed -n 's/^ADMIN_PASSWORD=//p' "${env_file}")"
if [[ -z "${admin_name}" || -z "${admin_email}" || ${#admin_password} -lt 14 ]]; then
  echo "The private .env has invalid bootstrap account fields." >&2
  exit 1
fi

export ADMIN_NAME="${admin_name}" ADMIN_EMAIL="${admin_email}" ADMIN_PASSWORD="${admin_password}"
"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${env_file}" \
  -p asus-kevin-bellm \
  run --rm --no-deps \
  -e ADMIN_NAME -e ADMIN_EMAIL -e ADMIN_PASSWORD \
  assistant-web python -m app.bootstrap
unset ADMIN_NAME ADMIN_EMAIL ADMIN_PASSWORD admin_name admin_email admin_password

: > "${marker_file}"
chmod 600 "${marker_file}"
echo "Recorded the one-time account bootstrap. Normal starts carry no password."
