#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

cluster_require_command ssh-keygen
found=0
for public_key in /etc/ssh/ssh_host_*_key.pub; do
  [[ -f "${public_key}" ]] || continue
  found=1
  ssh-keygen -lf "${public_key}" -E sha256
done
((found)) || cluster_die "No OpenSSH host public keys were found. Is openssh-server installed?"

cat <<'EOF'

Read the ED25519 SHA256 fingerprint from this machine's physical console (or an
already trusted session), then compare it with what SSH shows the first time
the administration laptop connects. Never accept an unverified fingerprint.
EOF
