#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app_port="$(sed -n 's/^APP_PORT=//p' "${project_dir}/.env" 2>/dev/null || true)"
app_port="${app_port:-3000}"

"${project_dir}/scripts/setup.sh"

"${project_dir}/scripts/inference.sh" ensure

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/infra/search/compose.yaml" \
  --env-file "${project_dir}/infra/search/.env" \
  -p kevinbellm-search \
  up --detach --build

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${project_dir}/.env" \
  -p kevinbellm \
  build live-tools assistant-web

"${project_dir}/scripts/bootstrap-account.sh"

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${project_dir}/.env" \
  -p kevinbellm \
  up --detach --remove-orphans live-tools assistant-web

echo
echo "KevinBeLLM is starting at http://127.0.0.1:${app_port}"
echo "Login is required; retrieve it with ./scripts/show-login.sh"
