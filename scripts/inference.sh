#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"

env_value() {
  local name="$1"
  if [[ -f "${env_file}" ]]; then
    sed -n "s/^${name}=//p" "${env_file}" | tail -n 1 | tr -d '\r'
  fi
}

backend="$(env_value INFERENCE_BACKEND)"
backend="${backend:-llamacpp}"

case "${backend}" in
  llamacpp)
    base_url="$(env_value INFERENCE_BASE_URL)"
    base_url="${base_url:-http://127.0.0.1:8080}"
    health_path="/health"
    unit_name="kevinbellm-llama.service"
    ;;
  ollama)
    base_url="$(env_value INFERENCE_BASE_URL)"
    base_url="${base_url:-$(env_value OLLAMA_URL)}"
    base_url="${base_url:-$(env_value OLLAMA_BASE_URL)}"
    base_url="${base_url:-http://127.0.0.1:11434}"
    health_path="/api/version"
    unit_name="ollama.service"
    ;;
  *)
    echo "INFERENCE_BACKEND must be 'llamacpp' or 'ollama', not '${backend}'." >&2
    exit 2
    ;;
esac

base_url="${base_url%/}"
case "${base_url}" in
  http://127.0.0.1:*|https://127.0.0.1:*|http://localhost:*|https://localhost:*|http://\[::1\]:*|https://\[::1\]:*) ;;
  *)
    echo "Refusing non-loopback INFERENCE_BASE_URL: ${base_url}" >&2
    exit 2
    ;;
esac

healthy() {
  curl --fail --silent --max-time 5 "${base_url}${health_path}" >/dev/null
}

start_service() {
  if healthy; then
    return 0
  fi
  if ! systemctl --user start "${unit_name}"; then
    echo "Could not start ${unit_name}." >&2
    if [[ "${backend}" == "llamacpp" ]]; then
      echo "Install the coordinator services described in docs/TWO_NODE_SETUP.md." >&2
    fi
    return 1
  fi
}

wait_for_service() {
  local attempts="${INFERENCE_WAIT_ATTEMPTS:-180}"
  local delay="${INFERENCE_WAIT_DELAY_SECONDS:-2}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if healthy; then
      printf '%s is ready at %s\n' "${backend}" "${base_url}"
      return 0
    fi
    sleep "${delay}"
  done
  printf '%s did not become ready at %s after %s attempts.\n' \
    "${backend}" "${base_url}" "${attempts}" >&2
  journalctl --user -u "${unit_name}" -n 30 --no-pager 2>/dev/null || true
  return 1
}

show_models() {
  local endpoint
  if [[ "${backend}" == "llamacpp" ]]; then
    endpoint="${base_url}/v1/models"
  else
    endpoint="${base_url}/api/tags"
  fi
  if ! python3 - "${backend}" "${endpoint}" <<'PY'
import json
import sys
import urllib.request

backend = sys.argv[1]
try:
    with urllib.request.urlopen(sys.argv[2], timeout=15) as response:
        payload = json.load(response)
except Exception as exc:
    raise SystemExit(f"invalid model response: {exc}") from exc

items = payload.get("data", []) if backend == "llamacpp" else payload.get("models", [])
print("Models:")
for item in items:
    if not isinstance(item, dict):
        continue
    model_id = item.get("id") if backend == "llamacpp" else (item.get("name") or item.get("model"))
    if isinstance(model_id, str):
        print(f"  - {model_id}")
PY
  then
    echo "Models: ${backend} API unavailable" >&2
    return 1
  fi
}

case "${1:-}" in
  backend) printf '%s\n' "${backend}" ;;
  url) printf '%s\n' "${base_url}" ;;
  health) healthy ;;
  start) start_service ;;
  wait) wait_for_service ;;
  ensure) start_service && wait_for_service ;;
  models) show_models ;;
  status)
    printf '%s (%s)\n' "${backend}" "${base_url}"
    systemctl --user --no-pager --full status "${unit_name}" 2>/dev/null | sed -n '1,10p' || true
    echo
    show_models || true
    ;;
  *)
    echo "Usage: $0 {backend|url|health|start|wait|ensure|models|status}" >&2
    exit 2
    ;;
esac
