#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

env_value() {
  local file="$1"
  local name="$2"
  sed -n "s/^${name}=//p" "${file}" 2>/dev/null | tail -n 1 | tr -d '\r'
}

wait_for_http() {
  local name="$1"
  local url="$2"
  shift 2
  local attempts="${SERVICE_WAIT_ATTEMPTS:-90}"
  local delay="${SERVICE_WAIT_DELAY_SECONDS:-2}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --fail --silent --show-error --max-time 5 "$@" "${url}" >/dev/null 2>&1; then
      printf '%s is ready at %s\n' "${name}" "${url}"
      return 0
    fi
    sleep "${delay}"
  done
  printf '%s did not become ready at %s after %s attempts.\n' \
    "${name}" "${url}" "${attempts}" >&2
  return 1
}

"${project_dir}/scripts/setup.sh"

app_port="$(env_value "${project_dir}/.env" APP_PORT)"
tools_port="$(env_value "${project_dir}/.env" LIVE_TOOLS_HOST_PORT)"
search_port="$(env_value "${project_dir}/infra/search/.env" SEARXNG_HOST_PORT)"
app_port="${app_port:-3000}"
tools_port="${tools_port:-8090}"
search_port="${search_port:-8888}"

"${project_dir}/scripts/inference.sh" ensure

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/infra/search/compose.yaml" \
  --env-file "${project_dir}/infra/search/.env" \
  -p kevinbellm-search \
  up --detach --build

wait_for_http \
  "SearXNG" \
  "http://127.0.0.1:${search_port}/config" \
  -H "X-Real-IP: 127.0.0.1"

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
  up --detach --remove-orphans live-tools

wait_for_http "Structured tools" "http://127.0.0.1:${tools_port}/health"

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${project_dir}/.env" \
  -p kevinbellm \
  up --detach --remove-orphans assistant-web

wait_for_http "KevinBeLLM" "http://127.0.0.1:${app_port}/health"

echo
echo "KevinBeLLM is starting at http://127.0.0.1:${app_port}"
echo "Login is required; retrieve it with ./scripts/show-login.sh"
