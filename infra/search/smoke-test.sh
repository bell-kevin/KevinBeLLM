#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

search_url="${SEARXNG_URL:-http://127.0.0.1:${SEARXNG_HOST_PORT:-8888}}"
response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT

curl --fail --silent --show-error --get \
  --header 'X-Real-IP: 127.0.0.1' \
  --data-urlencode 'q=latest open source AI models' \
  --data 'categories=general' \
  --data 'format=json' \
  "${search_url}/search" >"$response_file"

python3 - "$response_file" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
if not isinstance(payload.get("results"), list):
    raise SystemExit("SearXNG response did not contain a results list")
print(f"SearXNG JSON API OK: {len(payload['results'])} results")
PY
