#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"
env_example="${project_dir}/.env.example"
search_env="${project_dir}/infra/search/.env"
search_example="${project_dir}/infra/search/.env.example"

if [[ ! -f "${env_file}" ]]; then
  cp "${env_example}" "${env_file}"
  admin_password="$(openssl rand -hex 20)"
  sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=${admin_password}/" "${env_file}"
  chmod 600 "${env_file}"
  echo "Created ${env_file} with a private random bootstrap password."
else
  echo "Keeping existing ${env_file}."
  if grep -q '^ADMIN_PASSWORD=replace-with-random-admin-password$' "${env_file}"; then
    admin_password="$(openssl rand -hex 20)"
    sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=${admin_password}/" "${env_file}"
    echo "Generated the initial admin password."
  fi
  chmod 600 "${env_file}"
fi

if [[ ! -f "${search_env}" ]]; then
  cp "${search_example}" "${search_env}"
  search_secret="$(openssl rand -hex 32)"
  local_uid="$(id -u)"
  local_gid="$(id -g)"
  sed -i "s/^SEARXNG_SECRET=.*/SEARXNG_SECRET=${search_secret}/" "${search_env}"
  sed -i "s/^LOCAL_UID=.*/LOCAL_UID=${local_uid}/" "${search_env}"
  sed -i "s/^LOCAL_GID=.*/LOCAL_GID=${local_gid}/" "${search_env}"
  echo "Created ${search_env} with a private random SearXNG secret."
elif grep -q '^SEARXNG_SECRET=replace-with-a-random-64-character-hex-secret$' "${search_env}"; then
  search_secret="$(openssl rand -hex 32)"
  sed -i "s/^SEARXNG_SECRET=.*/SEARXNG_SECRET=${search_secret}/" "${search_env}"
  echo "Replaced the placeholder SearXNG secret."
fi
chmod 600 "${search_env}"

engine="$("${project_dir}/scripts/container-engine.sh")"
if ! "${engine}" info >/dev/null 2>&1; then
  echo "${engine} is installed but not available to this user." >&2
  exit 1
fi

if [[ -n "${CONTAINER_ENGINE:-}" ]] || [[ ! -f "${project_dir}/.runtime-engine" ]]; then
  printf '%s\n' "${engine}" > "${project_dir}/.runtime-engine"
fi
chmod 600 "${project_dir}/.runtime-engine"

data_volume="$(sed -n 's/^KEVINBELLM_DATA_VOLUME=//p' "${env_file}" | tail -n 1 | tr -d '\r')"
data_volume="${data_volume:-kevinbellm-data}"
[[ "${data_volume}" =~ ^[A-Za-z0-9_.-]+$ ]] || {
  echo "KEVINBELLM_DATA_VOLUME contains unsupported characters." >&2
  exit 1
}
legacy_volume='asus-kevin-bellm-data'
if [[ "${data_volume}" != "${legacy_volume}" ]] && \
   "${engine}" volume inspect "${legacy_volume}" >/dev/null 2>&1; then
  if ! "${engine}" volume inspect "${data_volume}" >/dev/null 2>&1; then
    cat >&2 <<EOF
Found the proof-of-concept data volume ${legacy_volume}, but the configured
volume is ${data_volume}. Refusing to create a new empty login database during
an in-place upgrade. To retain the existing account, set this in .env:

  KEVINBELLM_DATA_VOLUME=${legacy_volume}

Fresh Machine A deployments without that legacy volume use kevinbellm-data.
EOF
    exit 1
  fi
  cat >&2 <<EOF
Both ${legacy_volume} and ${data_volume} exist. Refusing to guess which account
database is authoritative. Set KEVINBELLM_DATA_VOLUME=${legacy_volume} to keep
using the proof-of-concept database, or explicitly archive/remove the legacy
volume only after verifying a completed migration.
EOF
  exit 1
fi

"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${env_file}" \
  -p kevinbellm \
  config --quiet

echo
echo "Setup is ready. Run ./scripts/start.sh and open http://127.0.0.1:3000"
echo "Retrieve the generated login with ./scripts/show-login.sh"
