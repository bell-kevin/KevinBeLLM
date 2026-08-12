#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0
app_port="$(sed -n 's/^APP_PORT=//p' "${project_dir}/.env" 2>/dev/null || true)"
tools_port="$(sed -n 's/^LIVE_TOOLS_HOST_PORT=//p' "${project_dir}/.env" 2>/dev/null || true)"
search_port="$(sed -n 's/^SEARXNG_HOST_PORT=//p' "${project_dir}/infra/search/.env" 2>/dev/null || true)"
app_port="${app_port:-3000}"
tools_port="${tools_port:-8090}"
search_port="${search_port:-8888}"

check_url() {
  local name="$1"
  local url="$2"
  if curl --fail --silent --show-error --max-time 15 "${url}" >/dev/null; then
    printf 'PASS  %s\n' "${name}"
  else
    printf 'FAIL  %s (%s)\n' "${name}" "${url}" >&2
    failures=$((failures + 1))
  fi
}

check_url "Ollama API" "http://127.0.0.1:11434/api/version"
check_url "Structured tools" "http://127.0.0.1:${tools_port}/health"
check_url "OpenAPI schema" "http://127.0.0.1:${tools_port}/openapi.json"
check_url "SearXNG JSON" "http://127.0.0.1:${search_port}/search?q=OpenAI&format=json"
check_url "Weather data" "http://127.0.0.1:${tools_port}/weather?location=Boise%2C%20Idaho&forecast_days=1&units=imperial"
check_url "Hugging Face data" "http://127.0.0.1:${tools_port}/huggingface/models?query=Qwen&sort_by=trending&require_gguf=true&limit=2"

python3 - "${app_port}" <<'PY' || failures=$((failures + 1))
import json
import sys
import urllib.request

port = int(sys.argv[1])
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as response:
        payload = json.load(response)
except Exception as exc:
    raise SystemExit(f"FAIL  KevinBeLLM web health ({exc})") from exc
if payload != {"status": "ok", "service": "assistant-web"}:
    raise SystemExit(f"FAIL  wrong service on browser port: {payload!r}")
print("PASS  KevinBeLLM web health and identity")
PY

python3 - "${tools_port}" <<'PY' || failures=$((failures + 1))
import json
import pathlib
import sys
import urllib.request

port = int(sys.argv[1])

with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=10) as response:
    schema = json.load(response)

operation_ids = {
    operation["operationId"]
    for path in schema.get("paths", {}).values()
    for operation in path.values()
    if isinstance(operation, dict) and "operationId" in operation
}
required = {"get_weather_forecast", "search_huggingface_models"}
missing = required - operation_ids
if missing:
    raise SystemExit(f"FAIL  missing OpenAPI operations: {sorted(missing)}")
print("PASS  OpenAPI tool operation IDs")
PY

if (( failures > 0 )); then
  echo "${failures} check(s) failed." >&2
  exit 1
fi

echo "All service checks passed."
