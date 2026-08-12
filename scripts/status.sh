#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Ollama"
systemctl --user --no-pager --full status ollama.service 2>/dev/null | sed -n '1,8p' || true
echo
curl --fail --silent http://127.0.0.1:11434/api/tags \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print("Models:"); [print("  -", item["name"]) for item in data.get("models", [])]' \
  || echo "Models: Ollama API unavailable"

echo
echo "Application containers"
"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${project_dir}/.env" \
  -p asus-kevin-bellm \
  ps

echo
echo "Remote tunnel container"
remote_env="${project_dir}/infra/cloudflare/.env"
if [[ ! -f "${remote_env}" ]]; then
  remote_env="${project_dir}/infra/cloudflare/.env.example"
fi
"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/infra/cloudflare/compose.yaml" \
  --env-file "${remote_env}" \
  -p asus-kevin-remote-access \
  ps

echo
echo "Search container"
"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/infra/search/compose.yaml" \
  --env-file "${project_dir}/infra/search/.env" \
  -p asus-kevin-search \
  ps
