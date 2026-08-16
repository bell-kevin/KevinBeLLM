#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

cd -- "$(git rev-parse --show-toplevel)"

fail() {
  printf 'Public-tree check failed: %s\n' "$*" >&2
  exit 1
}

tracked_files="$(git ls-files)"

while IFS= read -r path; do
  case "${path}" in
    *.env.example|*.example.env) ;;
    *.env|.runtime-engine|*/.runtime-engine|.bootstrap-complete|*/.bootstrap-complete)
      fail 'a private runtime file is tracked'
      ;;
  esac
done <<<"${tracked_files}"

if grep -Eiq '(^|/)(models?)/|\.(gguf|safetensors|db|sqlite|sqlite3|key|pem)$|(^|/)(id_ed25519|id_rsa)(\.pub)?$|(^|/)known_hosts([^/]*)$|(^|/)tunnel-token$' <<<"${tracked_files}"; then
  fail 'a model, database, key, host-trust file, or tunnel token is tracked'
fi

if git grep -q -I -E -- '-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----' -- .; then
  fail 'private-key material appears in tracked content'
fi

# A remotely managed Cloudflare Tunnel token is a long base64-encoded JSON
# object, usually beginning with "eyJ". It is not necessarily a three-part JWT.
# Short documentation placeholders such as "eyJ..." do not match.
if git grep -q -I -E -- 'eyJ[A-Za-z0-9_+/=-]{100,}' -- .; then
  fail 'a Cloudflare-tunnel-token-shaped credential appears in tracked content'
fi

printf 'Public-tree hygiene checks passed.\n'
