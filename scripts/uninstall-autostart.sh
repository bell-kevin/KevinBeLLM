#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

systemctl --user disable --now kevinbellm.service 2>/dev/null || true
systemctl --user disable --now kevinbellm-remote.service 2>/dev/null || true
for unit_file in \
  "${HOME}/.config/systemd/user/kevinbellm.service" \
  "${HOME}/.config/systemd/user/kevinbellm-remote.service"; do
  if [[ -f "${unit_file}" ]]; then
    mv "${unit_file}" "${unit_file}.disabled"
    echo "Moved the user service to ${unit_file}.disabled"
  fi
done
systemctl --user daemon-reload
