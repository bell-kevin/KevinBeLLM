#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/../.." && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

enable_now=0
llama_dir="${HOME}/.local/opt/llama.cpp-b10451"
env_file=""
restart_active=0

usage() {
  cat <<'USAGE'
Usage: install-services.sh [options]

Options:
  --env-file PATH    Private env file (default ~/.config/kevinbellm-cluster/standalone.env)
  --llama-dir PATH   Pinned build root (default ~/.local/opt/llama.cpp-b10451)
  --enable-now       Enable and start the installed user unit

Installs Machine A's inference user unit and enables systemd lingering so it
starts after the encrypted machine has completed boot. There is one host and
one role: this deployment never starts or connects to llama.cpp RPC.
USAGE
}

while (($#)); do
  case "$1" in
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

# Fail closed. Stop and disable the inference unit before reading any
# env/model/build input, and disable any RPC unit left behind by an older
# two-node install whose Requires= dependency could otherwise resurrect a
# tunnel at the next boot. A successful install re-enables the safe server.
if systemctl --user is-active --quiet kevinbellm-llama.service; then
  restart_active=1
fi
shutdown_failed=0
for unsafe_unit in \
  kevinbellm-llama.service \
  kevinbellm-rpc-tunnel.service \
  kevinbellm-rpc-worker.service; do
  if systemctl --user cat "${unsafe_unit}" >/dev/null 2>&1; then
    systemctl --user disable --now "${unsafe_unit}" || shutdown_failed=1
  fi
done
((shutdown_failed == 0)) || \
  cluster_die "Could not disable every prior inference/RPC unit; inspect systemctl --user status."

# Named units are not the only way an RPC parser or SSH forward can exist.
# Refuse the install if an unknown process still owns either legacy RPC port;
# report only its bound endpoint and leave process ownership to the operator
# instead of killing an unrelated process.
cluster_require_command ss
listener_found=0
for rpc_port in 50052 50053; do
  rpc_listeners="$(ss -H -ltn "sport = :${rpc_port}" | awk '{ print $4 }')"
  if [[ -n "${rpc_listeners}" ]]; then
    cluster_warn "RPC listener remains on TCP/${rpc_port}: ${rpc_listeners//$'\n'/, }"
    listener_found=1
  fi
done
((listener_found == 0)) || \
  cluster_die "Refusing to install while an RPC port remains open. Stop the owning process and rerun."

config_dir="${HOME}/.config/kevinbellm-cluster"
unit_dir="${HOME}/.config/systemd/user"
install -d -m 700 "${config_dir}" "${HOME}/.cache/llama.cpp"
install -d -m 755 "${unit_dir}"

if [[ -z "${env_file}" ]]; then
  env_file="${config_dir}/standalone.env"
fi
if [[ ! -e "${env_file}" ]]; then
  install -m 600 "${project_dir}/infra/cluster/standalone.example.env" "${env_file}"
  sed -i "s|/home/CHANGE_ME|$(cluster_sed_escape_replacement "${HOME}")|g" "${env_file}"
  cluster_info "Created safe defaults in ${env_file}."
fi
[[ -f "${env_file}" ]] || cluster_die "Env path is not a regular file: ${env_file}"
chmod 600 "${env_file}"

[[ -d "${llama_dir}" ]] || cluster_die "Pinned llama.cpp directory not found: ${llama_dir}"
llama_dir="$(realpath -e "${llama_dir}")"
server_bin="${llama_dir}/build/bin/llama-server"
[[ -x "${server_bin}" ]] || cluster_die "Pinned build is missing llama-server; run install-llama-cpp.sh."
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

render_unit() {
  local template="$1"
  local destination="$2"
  local tmp_unit
  tmp_unit="$(mktemp)"
  sed \
    -e "s|@LLAMA_SERVER_BIN@|$(cluster_sed_escape_replacement "${server_bin}")|g" \
    -e "s|@STANDALONE_ENV@|$(cluster_sed_escape_replacement "${env_file}")|g" \
    "${template}" >"${tmp_unit}"
  install -m 644 "${tmp_unit}" "${destination}"
  rm -f -- "${tmp_unit}"
}

model_preset="$(cluster_env_value "${env_file}" MODEL_PRESET || true)"
model_preset="${model_preset:-27b-iq4_xs}"
model_path="$(cluster_env_value "${env_file}" MODEL_PATH || true)"
model_alias="$(cluster_env_value "${env_file}" LLAMA_MODEL_ALIAS || true)"
# Machine A runs either the 27B layer-split over both of its GPUs or the
# smaller, much faster 9B on the RTX 3060 alone.
[[ "${model_preset}" == '27b-iq4_xs' || "${model_preset}" == '9b-q6_k' ]] || \
  cluster_die "MODEL_PRESET must be 27b-iq4_xs or 9b-q6_k in ${env_file}."
[[ "${model_path}" == /* ]] || cluster_die "MODEL_PATH must be an absolute path in ${env_file}."
[[ "${model_path}" != *CHANGE_ME* ]] || cluster_die "Replace the MODEL_PATH placeholder in ${env_file}."
[[ "${model_alias}" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]] || \
  cluster_die "Set a safe LLAMA_MODEL_ALIAS in ${env_file}."
if [[ -f "${model_path}" ]]; then
  "${script_dir}/download-model.sh" --preset "${model_preset}" --output "${model_path}" --verify-only
else
  cluster_die "Model file not found: ${model_path}. Run download-model.sh --preset ${model_preset} first."
fi

render_unit "${project_dir}/systemd/cluster/llama-server.service.in" \
  "${unit_dir}/kevinbellm-llama.service"
units=(kevinbellm-llama.service)

if [[ "${linger_enabled}" != 'yes' ]]; then
  sudo loginctl enable-linger "${USER}"
fi
systemctl --user daemon-reload
systemctl --user enable "${units[@]}"
if ((enable_now || restart_active)); then
  # `enable --now` does not restart a running unit after its template changes.
  systemctl --user restart "${units[@]}"
fi

cluster_info "Installed units: ${units[*]}"
cluster_info "This deployment has no RPC dependency, argument, service, or listener."
