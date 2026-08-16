#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${HOME}/.config/systemd/user"
mode="${1:-local}"
case "${mode}" in
  local)
    unit_name="kevinbellm.service"
    template="${project_dir}/systemd/kevinbellm.service.in"
    ;;
  remote)
    unit_name="kevinbellm-remote.service"
    template="${project_dir}/systemd/kevinbellm-remote.service.in"
    if [[ ! -s "${project_dir}/infra/cloudflare/secrets/tunnel-token" ]]; then
      echo "Configure the protected named tunnel before installing remote autostart." >&2
      exit 1
    fi
    ;;
  *) echo "Usage: $0 [local|remote]" >&2; exit 2 ;;
esac
unit_file="${unit_dir}/${unit_name}"

install -d -m 700 "${unit_dir}"
install -m 644 "${template}" "${unit_file}"
sed -i "s|@PROJECT_DIR@|${project_dir}|g" "${unit_file}"

systemctl --user daemon-reload
if [[ "${mode}" == "remote" ]]; then
  other_unit="kevinbellm.service"
else
  other_unit="kevinbellm-remote.service"
fi
if systemctl --user cat "${other_unit}" >/dev/null 2>&1; then
  systemctl --user disable --now "${other_unit}"
fi
systemctl --user enable "${unit_name}"

echo "KevinBeLLM (${mode}) will start with this user's systemd manager."
if systemctl --user is-active --quiet "${unit_name}"; then
  systemctl --user restart "${unit_name}"
  echo "Restarted the active service with the newly installed unit."
else
  echo "Start it now with: systemctl --user start ${unit_name}"
fi
if [[ "$(loginctl show-user "${USER}" -p Linger --value 2>/dev/null || true)" != "yes" ]]; then
  echo "To start after an encrypted boot without a desktop login, also run:"
  echo "  sudo loginctl enable-linger ${USER}"
else
  echo "User lingering is enabled; startup can occur after the encrypted disk is unlocked."
fi
