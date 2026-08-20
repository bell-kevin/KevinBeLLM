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
restart_active=0

usage() {
  cat <<'EOF'
Usage: install-services.sh --role standalone|coordinator|worker [options]

Options:
  --env-file PATH          Private role env file (default under ~/.config)
  --llama-dir PATH         Pinned build root (default ~/.local/opt/llama.cpp-b10451)
  --enable-now             Enable and start the installed user unit(s)
  --acknowledge-rpc-risk   Required only for coordinator and worker roles
  --tunnel-key PATH        Coordinator tunnel private key
  --worker-known-hosts P   Coordinator's dedicated pinned known_hosts file

This installs user services and enables systemd lingering so they start after
an encrypted machine has completed boot. --acknowledge-rpc-risk and the exact
acknowledgment inside the private env file are required only for the optional
coordinator and worker RPC roles. Standalone mode never starts or connects to
RPC and must not be given the acknowledgment flag.
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
cluster_require_command loginctl
linger_enabled="$(loginctl show-user "${USER}" -p Linger --value 2>/dev/null || true)"
if [[ "${linger_enabled}" != 'yes' ]]; then
  cluster_require_command sudo
fi
[[ "${role}" == 'standalone' || "${role}" == 'coordinator' || "${role}" == 'worker' ]] || \
  cluster_die "--role must be standalone, coordinator, or worker."
if [[ "${role}" == 'standalone' ]]; then
  ((ack_flag == 0)) || cluster_die "Standalone mode does not use RPC; remove --acknowledge-rpc-risk."
else
  ((ack_flag)) || cluster_die "Read the RPC warning, then pass --acknowledge-rpc-risk."
fi

# A standalone migration is fail-closed. Stop and disable the old server before
# reading any env/model/build input because it may still be an RPC coordinator
# whose Requires= dependency can bring the tunnel back at the next boot. If a
# later validation fails, inference stays down and every RPC-capable user unit
# stays disabled. A successful install re-enables the safe server below.
if [[ "${role}" == 'standalone' ]]; then
  if systemctl --user is-active --quiet kevinbellm-llama.service; then
    restart_active=1
  fi
  standalone_shutdown_failed=0
  for unsafe_unit in \
    kevinbellm-llama.service \
    kevinbellm-rpc-tunnel.service \
    kevinbellm-rpc-worker.service; do
    if systemctl --user cat "${unsafe_unit}" >/dev/null 2>&1; then
      systemctl --user disable --now "${unsafe_unit}" || standalone_shutdown_failed=1
    fi
  done
  ((standalone_shutdown_failed == 0)) || \
    cluster_die "Could not disable every prior inference/RPC unit; inspect systemctl --user status."

  # Named units are not the only way an RPC parser or SSH forward can exist.
  # Refuse the migration if an unknown process still owns either local RPC
  # port; report only its bound endpoint and leave process ownership to the
  # operator instead of killing an unrelated process.
  cluster_require_command ss
  standalone_listener_found=0
  for rpc_port in 50052 50053; do
    rpc_listeners="$(ss -H -ltn "sport = :${rpc_port}" | awk '{ print $4 }')"
    if [[ -n "${rpc_listeners}" ]]; then
      cluster_warn "RPC listener remains after standalone shutdown on TCP/${rpc_port}: ${rpc_listeners//$'\n'/, }"
      standalone_listener_found=1
    fi
  done
  ((standalone_listener_found == 0)) || \
    cluster_die "Refusing standalone install while an RPC port remains open. Stop the owning process and rerun."
fi

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
  if [[ "${role}" == 'standalone' ]]; then
    sed -i "s|/home/CHANGE_ME|$(cluster_sed_escape_replacement "${HOME}")|g" "${env_file}"
    cluster_info "Created safe standalone defaults in ${env_file}."
  else
    cluster_die "Created ${env_file}. Edit its placeholders and RPC acknowledgment, then rerun this command."
  fi
fi
[[ -f "${env_file}" ]] || cluster_die "Env path is not a regular file: ${env_file}"
chmod 600 "${env_file}"
if [[ "${role}" != 'standalone' ]]; then
  cluster_require_rpc_ack "${env_file}"
fi

[[ -d "${llama_dir}" ]] || cluster_die "Pinned llama.cpp directory not found: ${llama_dir}"
llama_dir="$(realpath -e "${llama_dir}")"
server_bin="${llama_dir}/build/bin/llama-server"
rpc_bin="${llama_dir}/build/bin/ggml-rpc-server"
bench_bin="${llama_dir}/build/bin/llama-bench"
[[ -x "${server_bin}" ]] || cluster_die "Pinned build is missing llama-server; run install-llama-cpp.sh."
if [[ "${role}" != 'standalone' ]]; then
  [[ -x "${rpc_bin}" && -x "${bench_bin}" ]] || \
    cluster_die "Pinned RPC build is incomplete; run install-llama-cpp.sh."
fi
build_spec_file="${llama_dir}/KEVINBELLM_BUILD_SPEC.txt"
[[ -f "${build_spec_file}" ]] || cluster_die "Pinned build-spec file is missing: ${build_spec_file}"
for required_build_spec in \
  'commit=10bf611e533d81f739128304991c5e133c6aebd8' \
  'CMAKE_CUDA_ARCHITECTURES=86' \
  'GGML_CUDA=ON' \
  'GGML_NATIVE=OFF' \
  'LLAMA_BUILD_UI=OFF' \
  'LLAMA_USE_PREBUILT_UI=OFF'; do
  grep -qxF "${required_build_spec}" "${build_spec_file}" || \
    cluster_die "Build spec is missing required setting: ${required_build_spec}"
done
if [[ "${role}" != 'standalone' ]]; then
  for required_rpc_spec in \
    'GGML_RPC=ON' \
    'GGML_AVX=OFF' \
    'GGML_AVX2=OFF' \
    'GGML_BMI2=OFF' \
    'GGML_FMA=OFF' \
    'GGML_F16C=OFF'; do
    grep -qxF "${required_rpc_spec}" "${build_spec_file}" || \
      cluster_die "RPC build spec is missing required setting: ${required_rpc_spec}"
  done
fi

render_unit() {
  local template="$1"
  local destination="$2"
  local tmp_unit
  tmp_unit="$(mktemp)"
  sed \
    -e "s|@LLAMA_SERVER_BIN@|$(cluster_sed_escape_replacement "${server_bin}")|g" \
    -e "s|@LLAMA_BENCH_BIN@|$(cluster_sed_escape_replacement "${bench_bin}")|g" \
    -e "s|@RPC_SERVER_BIN@|$(cluster_sed_escape_replacement "${rpc_bin}")|g" \
    -e "s|@STANDALONE_ENV@|$(cluster_sed_escape_replacement "${env_file}")|g" \
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
  if systemctl --user is-active --quiet kevinbellm-rpc-worker.service; then
    restart_active=1
  fi
  render_unit "${project_dir}/systemd/cluster/llama-rpc-worker.service.in" \
    "${unit_dir}/kevinbellm-rpc-worker.service"
  units+=(kevinbellm-rpc-worker.service)
elif [[ "${role}" == 'standalone' ]]; then
  model_preset="$(cluster_env_value "${env_file}" MODEL_PRESET || true)"
  model_preset="${model_preset:-9b-q6_k}"
  model_path="$(cluster_env_value "${env_file}" MODEL_PATH || true)"
  model_alias="$(cluster_env_value "${env_file}" LLAMA_MODEL_ALIAS || true)"
  # Machine A runs either the 9B on the 3060 alone or the 27B layer-split over
  # both of its GPUs; the two-node 27b-q4_k_m artifact stays coordinator-only.
  [[ "${model_preset}" == '9b-q6_k' || "${model_preset}" == '27b-iq4_xs' ]]     || cluster_die "Standalone mode requires MODEL_PRESET=9b-q6_k or 27b-iq4_xs in ${env_file}."
  [[ "${model_path}" == /* ]] || cluster_die "MODEL_PATH must be an absolute path in ${env_file}."
  [[ "${model_path}" != *CHANGE_ME* ]] || cluster_die "Replace the MODEL_PATH placeholder in ${env_file}."
  [[ "${model_alias}" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]] || cluster_die "Set a safe LLAMA_MODEL_ALIAS in ${env_file}."
  if [[ -f "${model_path}" ]]; then
    "${script_dir}/download-model.sh" --preset "${model_preset}" --output "${model_path}" --verify-only
  else
    cluster_die "Standalone model file not found: ${model_path}. Run download-model.sh --preset ${model_preset} first."
  fi
  render_unit "${project_dir}/systemd/cluster/llama-server.service.in" \
    "${unit_dir}/kevinbellm-llama.service"
  units+=(kevinbellm-llama.service)
else
  if systemctl --user is-active --quiet kevinbellm-llama.service; then
    restart_active=1
  fi
  [[ -f "${tunnel_key}" ]] || cluster_die "Tunnel key not found; run generate-tunnel-key.sh."
  [[ -f "${worker_known_hosts}" ]] || cluster_die "Pinned worker host key not found; run pin-worker-host-key.sh."
  chmod 600 "${tunnel_key}" "${worker_known_hosts}"
  worker_target="$(cluster_env_value "${env_file}" WORKER_SSH_TARGET || true)"
  worker_port="$(cluster_env_value "${env_file}" WORKER_SSH_PORT || true)"
  model_preset="$(cluster_env_value "${env_file}" MODEL_PRESET || true)"
  model_preset="${model_preset:-27b-q4_k_m}"
  model_path="$(cluster_env_value "${env_file}" MODEL_PATH || true)"
  model_alias="$(cluster_env_value "${env_file}" LLAMA_MODEL_ALIAS || true)"
  [[ "${worker_target}" =~ ^[a-z_][a-z0-9_-]*@[^[:space:]@]+$ ]] || cluster_die "Set a valid WORKER_SSH_TARGET=user@host in ${env_file}."
  [[ "${worker_target}" != *CHANGE_ME* ]] || cluster_die "Replace the WORKER_SSH_TARGET placeholder in ${env_file}."
  [[ "${worker_port}" =~ ^[0-9]+$ ]] && ((worker_port >= 1 && worker_port <= 65535)) || cluster_die "Set a valid WORKER_SSH_PORT in ${env_file}."
  [[ "${model_preset}" == '27b-q4_k_m' ]] || cluster_die "Coordinator mode requires MODEL_PRESET=27b-q4_k_m in ${env_file}."
  [[ "${model_path}" == /* ]] || cluster_die "MODEL_PATH must be an absolute path in ${env_file}."
  [[ "${model_alias}" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]] || cluster_die "Set a safe LLAMA_MODEL_ALIAS in ${env_file}."
  if [[ -f "${model_path}" ]]; then
    "${script_dir}/download-model.sh" --preset "${model_preset}" --output "${model_path}" --verify-only
  elif ((enable_now)); then
    cluster_die "Model file not found: ${model_path}"
  else
    cluster_warn "Model file does not exist yet: ${model_path}"
  fi

  render_unit "${project_dir}/systemd/cluster/llama-rpc-tunnel.service.in" \
    "${unit_dir}/kevinbellm-rpc-tunnel.service"
  render_unit "${project_dir}/systemd/cluster/llama-server-rpc.service.in" \
    "${unit_dir}/kevinbellm-llama.service"
  units+=(kevinbellm-rpc-tunnel.service kevinbellm-llama.service)
fi

if [[ "${linger_enabled}" != 'yes' ]]; then
  sudo loginctl enable-linger "${USER}"
fi
systemctl --user daemon-reload
systemctl --user enable "${units[@]}"
if ((enable_now || restart_active)); then
  # `enable --now` does not restart a running unit after its template changes.
  # An explicit restart ensures a mode switch cannot leave stale RPC arguments.
  systemctl --user restart "${units[@]}"
fi

cluster_info "Installed ${role} units: ${units[*]}"
if [[ "${role}" == 'standalone' ]]; then
  cluster_info "Standalone mode has no RPC dependency, argument, service, or listener."
else
  cluster_warn "RPC remains equivalent to code execution for any process that reaches its loopback socket. Keep TCP/50052 off the LAN."
fi
