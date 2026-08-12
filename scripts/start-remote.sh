#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"
remote_dir="${project_dir}/infra/cloudflare"
remote_env="${remote_dir}/.env"
token_file="${remote_dir}/secrets/tunnel-token"

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

"${project_dir}/scripts/start.sh"

"${project_dir}/scripts/compose.sh" \
  -f "${remote_dir}/compose.yaml" \
  --env-file "${remote_env}" \
  -p asus-kevin-remote-access \
  up --detach

echo "Authenticated remote tunnel started for the PUBLIC_URL in .env."
