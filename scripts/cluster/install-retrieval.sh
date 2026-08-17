#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Machine B only. Installs the optional embedding, reranking, and document
# retrieval services. Nothing here runs on, depends on, or reconfigures
# Machine A: its standalone inference unit is not read, written, or restarted.
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/../.." && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

enable_now=0
llama_dir="${HOME}/.local/opt/llama.cpp-b10451"
env_file=""
venv_dir="${HOME}/.local/share/kevinbellm-retrieval/venv"
service_dir="${project_dir}/services/doc-retrieval"
restart_active=0

usage() {
  cat <<'EOF'
Usage: install-retrieval.sh [options]

Options:
  --env-file PATH   Private role env file (default ~/.config/kevinbellm-cluster/retrieval.env)
  --llama-dir PATH  Pinned build root (default ~/.local/opt/llama.cpp-b10451)
  --venv-dir PATH   Python environment for the retrieval API
  --enable-now      Enable and start the installed user units

Installs three Machine B user services:
  kevinbellm-embedding.service     llama-server --embedding    127.0.0.1:8081
  kevinbellm-reranker.service      llama-server --reranking    127.0.0.1:8082
  kevinbellm-doc-retrieval.service read-only search API        127.0.0.1:8091

None of them uses llama.cpp RPC, so no risk acknowledgment is required. All
three bind Machine B loopback only; Machine A reaches 8091 through an SSH
forward installed separately by install-retrieval-tunnel.sh.
EOF
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
    --venv-dir)
      (($# >= 2)) || cluster_die "--venv-dir requires a value."
      venv_dir="$2"
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
cluster_require_command python3
cluster_require_command ss
cluster_require_command curl
linger_enabled="$(loginctl show-user "${USER}" -p Linger --value 2>/dev/null || true)"
if [[ "${linger_enabled}" != 'yes' ]]; then
  cluster_require_command sudo
fi

# This helper must never be pointed at Machine A. Its standalone inference
# service owns TCP/8080 and its own GPU budget; adding two more resident models
# to that card would directly reduce everyday generation speed.
if systemctl --user cat kevinbellm-llama.service >/dev/null 2>&1; then
  cluster_die \
    "kevinbellm-llama.service exists on this host, so this looks like Machine A. Retrieval belongs on Machine B; installing it beside the everyday model would take VRAM from it."
fi

config_dir="${HOME}/.config/kevinbellm-cluster"
unit_dir="${HOME}/.config/systemd/user"
install -d -m 700 "${config_dir}" "${HOME}/.cache/llama.cpp"
install -d -m 755 "${unit_dir}"

if [[ -z "${env_file}" ]]; then
  env_file="${config_dir}/retrieval.env"
fi
if [[ ! -e "${env_file}" ]]; then
  install -m 600 "${project_dir}/infra/cluster/retrieval.example.env" "${env_file}"
  sed -i "s|/home/CHANGE_ME|$(cluster_sed_escape_replacement "${HOME}")|g" "${env_file}"
  cluster_die "Created ${env_file}. Review its model paths and source directory, then rerun this command."
fi
[[ -f "${env_file}" ]] || cluster_die "Env path is not a regular file: ${env_file}"
chmod 600 "${env_file}"

embedding_model="$(cluster_env_value "${env_file}" EMBEDDING_MODEL_PATH || true)"
reranker_model="$(cluster_env_value "${env_file}" RERANKER_MODEL_PATH || true)"
embedding_alias="$(cluster_env_value "${env_file}" EMBEDDING_MODEL_ALIAS || true)"
reranker_alias="$(cluster_env_value "${env_file}" RERANKER_MODEL_ALIAS || true)"
index_dir="$(cluster_env_value "${env_file}" RETRIEVAL_INDEX_DIR || true)"

for pair in \
  "EMBEDDING_MODEL_PATH:${embedding_model}" \
  "RERANKER_MODEL_PATH:${reranker_model}" \
  "RETRIEVAL_INDEX_DIR:${index_dir}"; do
  key="${pair%%:*}"
  value="${pair#*:}"
  [[ "${value}" == /* ]] || cluster_die "${key} must be an absolute path in ${env_file}."
  [[ "${value}" != *CHANGE_ME* ]] || cluster_die "Replace the ${key} placeholder in ${env_file}."
done
for pair in "EMBEDDING_MODEL_ALIAS:${embedding_alias}" "RERANKER_MODEL_ALIAS:${reranker_alias}"; do
  key="${pair%%:*}"
  value="${pair#*:}"
  [[ "${value}" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]] || \
    cluster_die "Set a safe ${key} in ${env_file}."
done

[[ -f "${embedding_model}" ]] || cluster_die \
  "Embedding model not found: ${embedding_model}. Run download-model.sh --preset embed-m3 first."
[[ -f "${reranker_model}" ]] || cluster_die \
  "Reranker model not found: ${reranker_model}. Run download-model.sh --preset rerank-m3 first."
"${script_dir}/download-model.sh" --preset embed-m3 --output "${embedding_model}" --verify-only
"${script_dir}/download-model.sh" --preset rerank-m3 --output "${reranker_model}" --verify-only

[[ -d "${llama_dir}" ]] || cluster_die "Pinned llama.cpp directory not found: ${llama_dir}"
llama_dir="$(realpath -e "${llama_dir}")"
server_bin="${llama_dir}/build/bin/llama-server"
[[ -x "${server_bin}" ]] || cluster_die "Pinned build is missing llama-server; run install-llama-cpp.sh."
build_spec_file="${llama_dir}/KEVINBELLM_BUILD_SPEC.txt"
[[ -f "${build_spec_file}" ]] || cluster_die "Pinned build-spec file is missing: ${build_spec_file}"
for required_build_spec in \
  'commit=10bf611e533d81f739128304991c5e133c6aebd8' \
  'CMAKE_CUDA_ARCHITECTURES=86' \
  'GGML_CUDA=ON'; do
  grep -qxF "${required_build_spec}" "${build_spec_file}" || \
    cluster_die "Build spec is missing required setting: ${required_build_spec}"
done

[[ -d "${service_dir}/app" ]] || cluster_die "Retrieval service source not found: ${service_dir}"
lock_file="${service_dir}/requirements.lock"
[[ -f "${lock_file}" ]] || cluster_die "Locked dependency file not found: ${lock_file}"

if ! python3 -m venv --help >/dev/null 2>&1; then
  cluster_die "python3 -m venv is unavailable. Install it with: sudo apt-get install -y python3-venv"
fi
install -d -m 700 "$(dirname -- "${venv_dir}")"
if [[ ! -x "${venv_dir}/bin/python" ]]; then
  cluster_info "Creating the retrieval virtual environment in ${venv_dir}"
  python3 -m venv "${venv_dir}"
fi
venv_python="${venv_dir}/bin/python"
[[ -x "${venv_python}" ]] || cluster_die "Virtual environment is incomplete: ${venv_python}"
# Deliberately no `pip install --upgrade pip` first: that would be an unpinned
# network install. Ubuntu 24.04's bundled pip already supports --require-hashes.
cluster_info "Installing hash-pinned retrieval dependencies"
"${venv_python}" -m pip install --quiet --require-hashes -r "${lock_file}"

install -d -m 700 "$(dirname -- "${index_dir}")"

render_unit() {
  local template="$1"
  local destination="$2"
  local tmp_unit
  tmp_unit="$(mktemp)"
  sed \
    -e "s|@LLAMA_SERVER_BIN@|$(cluster_sed_escape_replacement "${server_bin}")|g" \
    -e "s|@RETRIEVAL_ENV@|$(cluster_sed_escape_replacement "${env_file}")|g" \
    -e "s|@SERVICE_DIR@|$(cluster_sed_escape_replacement "${service_dir}")|g" \
    -e "s|@VENV_PYTHON@|$(cluster_sed_escape_replacement "${venv_python}")|g" \
    "${template}" >"${tmp_unit}"
  install -m 644 "${tmp_unit}" "${destination}"
  rm -f -- "${tmp_unit}"
}

units=(
  kevinbellm-embedding.service
  kevinbellm-reranker.service
  kevinbellm-doc-retrieval.service
)
for unit in "${units[@]}"; do
  if systemctl --user is-active --quiet "${unit}"; then
    restart_active=1
  fi
done
render_unit "${project_dir}/systemd/cluster/llama-embedding.service.in" \
  "${unit_dir}/kevinbellm-embedding.service"
render_unit "${project_dir}/systemd/cluster/llama-reranker.service.in" \
  "${unit_dir}/kevinbellm-reranker.service"
render_unit "${project_dir}/systemd/cluster/doc-retrieval.service.in" \
  "${unit_dir}/kevinbellm-doc-retrieval.service"

if [[ "${linger_enabled}" != 'yes' ]]; then
  sudo loginctl enable-linger "${USER}"
fi
systemctl --user daemon-reload
systemctl --user enable "${units[@]}"
if ((enable_now || restart_active)); then
  systemctl --user restart "${units[@]}"
fi

cluster_info "Installed Machine B retrieval units: ${units[*]}"
cluster_info "No RPC process, argument, or listener is used by this profile."
if ((enable_now || restart_active)); then
  cluster_info "Waiting for the model servers to finish loading"
  ready=0
  for _attempt in $(seq 1 60); do
    if curl --silent --fail --max-time 3 http://127.0.0.1:8081/health >/dev/null 2>&1 &&
      curl --silent --fail --max-time 3 http://127.0.0.1:8082/health >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  if ((ready == 0)); then
    cluster_warn "The embedding and reranking servers did not report healthy within 120 s."
    cluster_warn "Inspect: journalctl --user -u kevinbellm-embedding.service -u kevinbellm-reranker.service -e"
    cluster_warn "A rejected --pooling or --reranking flag appears here as an immediate exit."
    exit 1
  fi
  cluster_info "Both model servers are healthy"
  cluster_info "Build the first index with: ./scripts/cluster/index-documents.sh"
fi
