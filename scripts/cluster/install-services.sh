#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/../.." && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

role=""
enable_now=0
ack_flag=0
llama_dir="${HOME}/.local/opt/llama.cpp-b10451"
env_file=""
tunnel_key="${HOME}/.ssh/kevinbellm_rpc_tunnel_ed25519"
worker_known_hosts="${HOME}/.ssh/known_hosts.kevinbellm-worker"

usage() {
  cat <<'EOF'
Usage: install-services.sh --role coordinator|worker --acknowledge-rpc-risk [options]

Options:
  --env-file PATH          Private role env file (default under ~/.config)
  --llama-dir PATH         Pinned build root (default ~/.local/opt/llama.cpp-b10451)
  --enable-now             Enable and start the installed user unit(s)
  --tunnel-key PATH        Coordinator tunnel private key
  --worker-known-hosts P   Coordinator's dedicated pinned known_hosts file

This installs user services and enables systemd lingering so they start after
an encrypted machine has completed boot. The flag and the exact acknowledgment
inside the private env file are both required.
EOF
}

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || cluster_die "--role requires a value."
      role="$2"
      shift 2
      ;;
    --env-file)
      (($# >= 2)) || cluster_die "--env-file requires a value."
      env_file="$2"
      shift 2
      ;;
    --llama-dir)
      (($# >= 2)) || cluster_die "--llama-dir requires a value."
      llama_dir="$2"
      shift 2
      ;;
    --enable-now)
      enable_now=1
      shift
      ;;
    --acknowledge-rpc-risk)
      ack_flag=1
      shift
      ;;
    --tunnel-key)
      (($# >= 2)) || cluster_die "--tunnel-key requires a value."
      tunnel_key="$2"
      shift 2
      ;;
    --worker-known-hosts)
      (($# >= 2)) || cluster_die "--worker-known-hosts requires a value."
      worker_known_hosts="$2"
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
cluster_require_non_root
cluster_require_command systemctl
cluster_require_command sudo
[[ "${role}" == 'coordinator' || "${role}" == 'worker' ]] || cluster_die "--role must be coordinator or worker."
((ack_flag)) || cluster_die "Read the RPC warning, then pass --acknowledge-rpc-risk."

config_dir="${HOME}/.config/kevinbellm-cluster"
unit_dir="${HOME}/.config/systemd/user"
install -d -m 700 "${config_dir}" "${HOME}/.cache/llama.cpp"
install -d -m 755 "${unit_dir}"

if [[ -z "${env_file}" ]]; then
  env_file="${config_dir}/${role}.env"
fi
if [[ ! -e "${env_file}" ]]; then
  example_file="${project_dir}/infra/cluster/${role}.example.env"
  install -m 600 "${example_file}" "${env_file}"
  cluster_die "Created ${env_file}. Edit its placeholders and RPC acknowledgment, then rerun this command."
fi
[[ -f "${env_file}" ]] || cluster_die "Env path is not a regular file: ${env_file}"
chmod 600 "${env_file}"
cluster_require_rpc_ack "${env_file}"

[[ -d "${llama_dir}" ]] || cluster_die "Pinned llama.cpp directory not found: ${llama_dir}"
llama_dir="$(realpath -e "${llama_dir}")"
server_bin="${llama_dir}/build/bin/llama-server"
rpc_bin="${llama_dir}/build/bin/ggml-rpc-server"
bench_bin="${llama_dir}/build/bin/llama-bench"
[[ -x "${server_bin}" && -x "${rpc_bin}" && -x "${bench_bin}" ]] || \
  cluster_die "Pinned build is incomplete; run install-llama-cpp.sh."
grep -qx 'commit=10bf611e533d81f739128304991c5e133c6aebd8' "${llama_dir}/KEVINBELLM_BUILD_SPEC.txt" || \
  cluster_die "Build spec does not prove the pinned b10451 commit."

render_unit() {
  local template="$1"
  local destination="$2"
  local tmp_unit
  tmp_unit="$(mktemp)"
  sed \
    -e "s|@LLAMA_SERVER_BIN@|$(cluster_sed_escape_replacement "${server_bin}")|g" \
    -e "s|@LLAMA_BENCH_BIN@|$(cluster_sed_escape_replacement "${bench_bin}")|g" \
    -e "s|@RPC_SERVER_BIN@|$(cluster_sed_escape_replacement "${rpc_bin}")|g" \
    -e "s|@COORDINATOR_ENV@|$(cluster_sed_escape_replacement "${env_file}")|g" \
    -e "s|@WORKER_ENV@|$(cluster_sed_escape_replacement "${env_file}")|g" \
    -e "s|@TUNNEL_KEY@|$(cluster_sed_escape_replacement "${tunnel_key}")|g" \
    -e "s|@WORKER_KNOWN_HOSTS@|$(cluster_sed_escape_replacement "${worker_known_hosts}")|g" \
    "${template}" >"${tmp_unit}"
  install -m 644 "${tmp_unit}" "${destination}"
  rm -f -- "${tmp_unit}"
}

units=()
if [[ "${role}" == 'worker' ]]; then
  render_unit "${project_dir}/systemd/cluster/llama-rpc-worker.service.in" \
    "${unit_dir}/kevinbellm-rpc-worker.service"
  units+=(kevinbellm-rpc-worker.service)
else
  [[ -f "${tunnel_key}" ]] || cluster_die "Tunnel key not found; run generate-tunnel-key.sh."
  [[ -f "${worker_known_hosts}" ]] || cluster_die "Pinned worker host key not found; run pin-worker-host-key.sh."
  chmod 600 "${tunnel_key}" "${worker_known_hosts}"
  worker_target="$(cluster_env_value "${env_file}" WORKER_SSH_TARGET || true)"
  worker_port="$(cluster_env_value "${env_file}" WORKER_SSH_PORT || true)"
  model_path="$(cluster_env_value "${env_file}" MODEL_PATH || true)"
  model_alias="$(cluster_env_value "${env_file}" LLAMA_MODEL_ALIAS || true)"
  [[ "${worker_target}" =~ ^[a-z_][a-z0-9_-]*@[^[:space:]@]+$ ]] || cluster_die "Set a valid WORKER_SSH_TARGET=user@host in ${env_file}."
  [[ "${worker_target}" != *CHANGE_ME* ]] || cluster_die "Replace the WORKER_SSH_TARGET placeholder in ${env_file}."
  [[ "${worker_port}" =~ ^[0-9]+$ ]] && ((worker_port >= 1 && worker_port <= 65535)) || cluster_die "Set a valid WORKER_SSH_PORT in ${env_file}."
  [[ "${model_path}" == /* ]] || cluster_die "MODEL_PATH must be an absolute path in ${env_file}."
  [[ "${model_alias}" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]] || cluster_die "Set a safe LLAMA_MODEL_ALIAS in ${env_file}."
  if [[ ! -f "${model_path}" ]]; then
    if ((enable_now)); then
      cluster_die "Model file not found: ${model_path}"
    fi
    cluster_warn "Model file does not exist yet: ${model_path}"
  fi

  render_unit "${project_dir}/systemd/cluster/llama-rpc-tunnel.service.in" \
    "${unit_dir}/kevinbellm-rpc-tunnel.service"
  render_unit "${project_dir}/systemd/cluster/llama-server.service.in" \
    "${unit_dir}/kevinbellm-llama.service"
  units+=(kevinbellm-rpc-tunnel.service kevinbellm-llama.service)
fi

sudo loginctl enable-linger "${USER}"
systemctl --user daemon-reload
if ((enable_now)); then
  systemctl --user enable --now "${units[@]}"
else
  systemctl --user enable "${units[@]}"
fi

cluster_info "Installed ${role} units: ${units[*]}"
cluster_warn "RPC remains equivalent to code execution for any process that reaches its loopback socket. Keep TCP/50052 off the LAN."
