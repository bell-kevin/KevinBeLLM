#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

remote_env="${project_dir}/infra/cloudflare/.env"
if [[ ! -f "${remote_env}" ]]; then
  remote_env="${project_dir}/infra/cloudflare/.env.example"
fi
"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/infra/cloudflare/compose.yaml" \
  --env-file "${remote_env}" \
  -p asus-kevin-remote-access \
  down --remove-orphans

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${project_dir}/.env" \
  -p asus-kevin-bellm \
  down --remove-orphans

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/infra/search/compose.yaml" \
  --env-file "${project_dir}/infra/search/.env" \
  -p asus-kevin-search \
  down --remove-orphans

echo "Stopped the browser, tool, tunnel, and search services."
echo "Ollama remains available locally; stop it with: systemctl --user stop ollama.service"
