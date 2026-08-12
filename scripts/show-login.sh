#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"

if [[ ! -f "${env_file}" ]]; then
  echo "Run ./scripts/setup.sh first." >&2
  exit 1
fi

email="$(sed -n 's/^ADMIN_EMAIL=//p' "${env_file}")"
password="$(sed -n 's/^ADMIN_PASSWORD=//p' "${env_file}")"
app_port="$(sed -n 's/^APP_PORT=//p' "${env_file}")"
app_port="${app_port:-3000}"

echo "URL:      http://127.0.0.1:${app_port}"
echo "Email:    ${email}"
echo "Password: ${password}"
echo
echo "This is the one-time bootstrap password from .env."
echo "After you change it in Settings > Account, this value is intentionally stale."
