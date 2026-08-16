#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

public_key_file=""
coordinator_source=""
tunnel_user='llama-rpc-tunnel'

usage() {
  cat <<'EOF'
Usage: sudo install-worker-tunnel-key.sh --public-key-file PATH --from ADDRESS

Installs Machine A's dedicated public key for a no-shell account on Machine B.
ADDRESS is Machine A's DHCP-reserved LAN IPv4 address (or a narrowly scoped
CIDR). The key may create only a client-local forward to 127.0.0.1:50052.
EOF
}

while (($#)); do
  case "$1" in
    --public-key-file)
      (($# >= 2)) || cluster_die "--public-key-file requires a value."
      public_key_file="$2"
      shift 2
      ;;
    --from)
      (($# >= 2)) || cluster_die "--from requires a value."
      coordinator_source="$2"
      shift 2
      ;;
    --tunnel-user)
      (($# >= 2)) || cluster_die "--tunnel-user requires a value."
      tunnel_user="$2"
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

cluster_require_ubuntu
cluster_require_root
cluster_require_command sshd
cluster_require_command ssh-keygen
cluster_require_command python3
[[ -f "${public_key_file}" ]] || cluster_die "Public key file not found: ${public_key_file}"
[[ -n "${coordinator_source}" ]] || cluster_die "--from is required; reserve Machine A's LAN address in the router first."
if ! python3 - "${coordinator_source}" <<'PY'
import ipaddress
import sys

try:
    network = ipaddress.ip_network(sys.argv[1], strict=False)
except ValueError:
    raise SystemExit(1)

# A literal address becomes /32 or /128. If a range is intentionally used,
# keep it to one home IPv4 subnet or one IPv6 subnet at most.
minimum_prefix = 24 if network.version == 4 else 64
if network.prefixlen < minimum_prefix:
    raise SystemExit(1)
PY
then
  cluster_die "--from must be a literal IP or a narrow CIDR (IPv4 /24+, IPv6 /64+)."
fi
[[ "${tunnel_user}" =~ ^[a-z_][a-z0-9_-]*$ ]] || cluster_die "Invalid tunnel account name."
[[ "$(grep -Evc '^[[:space:]]*(#|$)' "${public_key_file}")" -eq 1 ]] || cluster_die "Public key file must contain exactly one key."

read -r key_type key_blob _ <"${public_key_file}"
[[ "${key_type}" == 'ssh-ed25519' && "${key_blob}" =~ ^[A-Za-z0-9+/]+={0,3}$ ]] || \
  cluster_die "Expected a single ED25519 public key."
ssh-keygen -lf "${public_key_file}" -E sha256 >/dev/null || cluster_die "Invalid SSH public key."

if ! getent passwd "${tunnel_user}" >/dev/null; then
  useradd --system --user-group --create-home --home-dir "/var/lib/${tunnel_user}" --shell /usr/sbin/nologin "${tunnel_user}"
fi
tunnel_record="$(getent passwd "${tunnel_user}")"
tunnel_home="$(cut -d: -f6 <<<"${tunnel_record}")"
tunnel_shell="$(cut -d: -f7 <<<"${tunnel_record}")"
tunnel_group="$(id -gn "${tunnel_user}")"
[[ -n "${tunnel_home}" ]] || cluster_die "Could not find home directory for ${tunnel_user}."
[[ "${tunnel_shell}" == '/usr/sbin/nologin' ]] || cluster_die "Existing ${tunnel_user} account has an interactive shell; refusing to reuse it."

install -d -o "${tunnel_user}" -g "${tunnel_group}" -m 700 "${tunnel_home}/.ssh"
authorized_keys="${tunnel_home}/.ssh/authorized_keys"
touch "${authorized_keys}"
chown "${tunnel_user}:${tunnel_group}" "${authorized_keys}"
chmod 600 "${authorized_keys}"

restricted_line="restrict,port-forwarding,permitopen=\"127.0.0.1:50052\",from=\"${coordinator_source}\",command=\"/usr/bin/false\" ${key_type} ${key_blob} kevinbellm-rpc-tunnel"
tmp_keys="$(mktemp)"
tmp_sshd="$(mktemp)"
trap 'rm -f -- "${tmp_keys}" "${tmp_sshd}"' EXIT

# Keep any independently managed keys, replace only a line containing this key.
grep -Fv -- "${key_blob}" "${authorized_keys}" >"${tmp_keys}" || true
printf '%s\n' "${restricted_line}" >>"${tmp_keys}"
install -o "${tunnel_user}" -g "${tunnel_group}" -m 600 "${tmp_keys}" "${authorized_keys}"

cat >"${tmp_sshd}" <<EOF
# Managed by KevinBeLLM scripts/cluster/install-worker-tunnel-key.sh
Match User ${tunnel_user}
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:50052
    GatewayPorts no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PermitTunnel no
Match all
EOF
install -o root -g root -m 644 "${tmp_sshd}" /etc/ssh/sshd_config.d/70-kevinbellm-rpc-tunnel.conf

if ! sshd -t; then
  rm -f -- /etc/ssh/sshd_config.d/70-kevinbellm-rpc-tunnel.conf
  cluster_die "sshd rejected the tunnel-account fragment; it was removed."
fi
systemctl reload ssh.service

cluster_info "Installed restricted tunnel key for ${tunnel_user}"
ssh-keygen -lf "${public_key_file}" -E sha256
cat <<'EOF'
This key cannot request a shell or forward anywhere except worker loopback TCP
50052. Do not add the same public key to a normal user's authorized_keys.
EOF
