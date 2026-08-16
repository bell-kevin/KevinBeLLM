#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"
marker_file="${project_dir}/.bootstrap-complete"

if [[ -f "${marker_file}" ]]; then
  exit 0
fi
if [[ ! -f "${env_file}" ]]; then
  echo "Run ./scripts/setup.sh before bootstrapping the account." >&2
  exit 1
fi

# Read only the three exact bootstrap keys. Values stay out of argv, Compose
# output, the long-running service environment, and the repository.
admin_name="$(sed -n 's/^ADMIN_NAME=//p' "${env_file}")"
admin_email="$(sed -n 's/^ADMIN_EMAIL=//p' "${env_file}")"
admin_password="$(sed -n 's/^ADMIN_PASSWORD=//p' "${env_file}")"
if [[ -z "${admin_name}" || -z "${admin_email}" || ${#admin_password} -lt 14 ]]; then
  echo "The private .env has invalid bootstrap account fields." >&2
  exit 1
fi

# Feed the three values over the one-shot container's stdin. This keeps them
# out of argv, Compose output, and the long-running service environment while
# also avoiding podman-compose 1.6's broken Docker-compatible `-e NAME` path.
bootstrap_code='import os,runpy,sys; values=sys.stdin.buffer.read().split(b"\0"); assert len(values)==4 and values[-1]==b""; os.environ.update(dict(zip(("ADMIN_NAME","ADMIN_EMAIL","ADMIN_PASSWORD"),(value.decode("utf-8") for value in values[:3])))); runpy.run_module("app.bootstrap",run_name="__main__")'
printf '%s\0%s\0%s\0' "${admin_name}" "${admin_email}" "${admin_password}" |
"${project_dir}/scripts/compose.sh" \
  -f "${project_dir}/compose.yaml" \
  --env-file "${env_file}" \
  -p kevinbellm \
  run --rm --no-deps -T \
  assistant-web python -c "${bootstrap_code}"
unset admin_name admin_email admin_password bootstrap_code

: > "${marker_file}"
chmod 600 "${marker_file}"
echo "Recorded the one-time account bootstrap. Normal starts carry no password."
