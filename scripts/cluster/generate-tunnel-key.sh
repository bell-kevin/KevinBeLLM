#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

key_file="${HOME}/.ssh/kevinbellm_rpc_tunnel_ed25519"

usage() {
  cat <<'EOF'
Usage: generate-tunnel-key.sh [--key-file PATH]

Creates Machine A's dedicated, unencrypted service key. It must be accepted on
Machine B only through install-worker-tunnel-key.sh, whose sshd and
authorized_keys restrictions prevent shell access and arbitrary forwarding.
EOF
}

while (($#)); do
  case "$1" in
    --key-file)
      (($# >= 2)) || cluster_die "--key-file requires a value."
      key_file="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      cluster_die "Unknown option: $1"
      ;;
  esac
done

cluster_require_non_root
cluster_require_command ssh-keygen
install -d -m 700 "$(dirname -- "${key_file}")"

if [[ -e "${key_file}" || -e "${key_file}.pub" ]]; then
  [[ -f "${key_file}" && -f "${key_file}.pub" ]] || cluster_die "Only one half of the tunnel keypair exists; refusing to replace it."
  derived_public="$(ssh-keygen -y -f "${key_file}")"
  recorded_public="$(awk 'NR == 1 { print $1, $2 }' "${key_file}.pub")"
  [[ "${derived_public}" == "${recorded_public}" ]] || cluster_die "Tunnel public/private key files do not match."
  cluster_info "Existing tunnel keypair is valid; leaving it unchanged"
else
  cluster_info "Generating Machine A's dedicated RPC tunnel key"
  ssh-keygen -q -t ed25519 -N '' -f "${key_file}" -C 'kevinbellm-rpc-tunnel'
fi

chmod 600 "${key_file}"
chmod 644 "${key_file}.pub"
ssh-keygen -lf "${key_file}.pub" -E sha256
printf '\nPublic key to transfer to Machine B (not a secret):\n'
cat "${key_file}.pub"
