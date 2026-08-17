#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Machine A only. Installs the outbound SSH forward that lets the assistant
# reach Machine B's read-only retrieval API on Machine A loopback TCP 8091.
#
# This unit is deliberately independent: it is never a Requires= or After=
# dependency of kevinbellm-llama.service, the application, or the Cloudflare
# connector, and it starts no GPU work on Machine A.
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/../.." && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

enable_now=0
env_file=""
tunnel_key="${HOME}/.ssh/kevinbellm_retrieval_tunnel_ed25519"
retrieval_known_hosts="${HOME}/.ssh/known_hosts.kevinbellm-retrieval"
restart_active=0

usage() {
  cat <<'EOF'
Usage: install-retrieval-tunnel.sh [options]

Options:
  --env-file PATH             Private env file (default ~/.config/kevinbellm-cluster/retrieval-client.env)
  --tunnel-key PATH           Machine A's dedicated retrieval private key
  --retrieval-known-hosts P   Dedicated pinned known_hosts file for Machine B
  --enable-now                Enable and start the tunnel unit

Prerequisites, in order:
  ./scripts/cluster/generate-tunnel-key.sh --key-file ~/.ssh/kevinbellm_retrieval_tunnel_ed25519
  (on B) sudo ./scripts/cluster/install-retrieval-tunnel-key.sh --public-key-file ... --from <A's IP>
  ./scripts/cluster/pin-worker-host-key.sh --host <B> --fingerprint SHA256:... \
      --known-hosts-file ~/.ssh/known_hosts.kevinbellm-retrieval
EOF
}

while (($#)); do
  case "$1" in
    --env-file)
      (($# >= 2)) || cluster_die "--env-file requires a value."
      env_file="$2"
      shift 2
      ;;
    --tunnel-key)
      (($# >= 2)) || cluster_die "--tunnel-key requires a value."
      tunnel_key="$2"
      shift 2
      ;;
    --retrieval-known-hosts)
      (($# >= 2)) || cluster_die "--retrieval-known-hosts requires a value."
      retrieval_known_hosts="$2"
      shift 2
      ;;
    --enable-now)
      enable_now=1
      shift
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
cluster_require_non_root
cluster_require_command systemctl
cluster_require_command loginctl
cluster_require_command ss
cluster_require_command ssh
linger_enabled="$(loginctl show-user "${USER}" -p Linger --value 2>/dev/null || true)"
if [[ "${linger_enabled}" != 'yes' ]]; then
  cluster_require_command sudo
fi

config_dir="${HOME}/.config/kevinbellm-cluster"
unit_dir="${HOME}/.config/systemd/user"
install -d -m 700 "${config_dir}"
install -d -m 755 "${unit_dir}"

if [[ -z "${env_file}" ]]; then
  env_file="${config_dir}/retrieval-client.env"
fi
if [[ ! -e "${env_file}" ]]; then
  install -m 600 "${project_dir}/infra/cluster/retrieval-client.example.env" "${env_file}"
  cluster_die "Created ${env_file}. Replace its Machine B address placeholder, then rerun this command."
fi
[[ -f "${env_file}" ]] || cluster_die "Env path is not a regular file: ${env_file}"
chmod 600 "${env_file}"

retrieval_target="$(cluster_env_value "${env_file}" RETRIEVAL_SSH_TARGET || true)"
retrieval_port="$(cluster_env_value "${env_file}" RETRIEVAL_SSH_PORT || true)"
[[ "${retrieval_target}" =~ ^[a-z_][a-z0-9_-]*@[^[:space:]@]+$ ]] || \
  cluster_die "Set a valid RETRIEVAL_SSH_TARGET=user@host in ${env_file}."
[[ "${retrieval_target}" != *CHANGE_ME* ]] || \
  cluster_die "Replace the RETRIEVAL_SSH_TARGET placeholder in ${env_file}."
[[ "${retrieval_port}" =~ ^[0-9]+$ ]] && ((retrieval_port >= 1 && retrieval_port <= 65535)) || \
  cluster_die "Set a valid RETRIEVAL_SSH_PORT in ${env_file}."

[[ -f "${tunnel_key}" ]] || cluster_die \
  "Retrieval tunnel key not found: ${tunnel_key}. Run generate-tunnel-key.sh --key-file ${tunnel_key}"
[[ -f "${retrieval_known_hosts}" ]] || cluster_die \
  "Pinned Machine B host key not found: ${retrieval_known_hosts}. Run pin-worker-host-key.sh --known-hosts-file ${retrieval_known_hosts}"
chmod 600 "${tunnel_key}" "${retrieval_known_hosts}"

# The RPC tunnel key must not double as the retrieval key: one restricted key
# per forwarded service keeps revocation independent.
rpc_key="${HOME}/.ssh/kevinbellm_rpc_tunnel_ed25519"
if [[ -f "${rpc_key}" ]] && cmp -s "${tunnel_key}" "${rpc_key}"; then
  cluster_die "The retrieval tunnel must use its own key, not the RPC tunnel key."
fi

# Port 8091 must be free for the forward. Report the owner rather than killing it.
existing_listener="$(ss -H -ltn 'sport = :8091' | awk '{ print $4 }')"
if [[ -n "${existing_listener}" ]] && ! systemctl --user is-active --quiet kevinbellm-retrieval-tunnel.service; then
  cluster_die "TCP/8091 is already owned by another process (${existing_listener//$'\n'/, }). Stop it and rerun."
fi

if systemctl --user is-active --quiet kevinbellm-retrieval-tunnel.service; then
  restart_active=1
fi

tmp_unit="$(mktemp)"
trap 'rm -f -- "${tmp_unit}"' EXIT
sed \
  -e "s|@RETRIEVAL_CLIENT_ENV@|$(cluster_sed_escape_replacement "${env_file}")|g" \
  -e "s|@RETRIEVAL_TUNNEL_KEY@|$(cluster_sed_escape_replacement "${tunnel_key}")|g" \
  -e "s|@RETRIEVAL_KNOWN_HOSTS@|$(cluster_sed_escape_replacement "${retrieval_known_hosts}")|g" \
  "${project_dir}/systemd/cluster/retrieval-tunnel.service.in" >"${tmp_unit}"
install -m 644 "${tmp_unit}" "${unit_dir}/kevinbellm-retrieval-tunnel.service"

if [[ "${linger_enabled}" != 'yes' ]]; then
  sudo loginctl enable-linger "${USER}"
fi
systemctl --user daemon-reload
systemctl --user enable kevinbellm-retrieval-tunnel.service
if ((enable_now || restart_active)); then
  systemctl --user restart kevinbellm-retrieval-tunnel.service
fi

cluster_info "Installed kevinbellm-retrieval-tunnel.service"
cluster_info "This unit is not a dependency of inference, the app, or the tunnel connector."
cat <<'EOF'

Next, point the application at the forward by adding this to the private root
.env, then restarting the app:

    DOC_RETRIEVAL_URL=http://127.0.0.1:8091

Leaving that line out keeps retrieval fully disabled: with no URL configured the
assistant advertises no document tool and sends exactly the prompt it sends now.
EOF
