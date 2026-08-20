#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

cd -- "$(git rev-parse --show-toplevel)"

fail() {
  printf 'Standalone contract check failed: %s\n' "$*" >&2
  exit 1
}

require_text() {
  local file="$1"
  local text="$2"
  grep -Fq -- "${text}" "${file}" || fail "${file} is missing: ${text}"
}

standalone_unit='systemd/cluster/llama-server.service.in'
standalone_exec="$(grep '^ExecStart=' "${standalone_unit}")"

# Ubuntu's user manager rejects these with status 218/CAPABILITIES.
! grep -Eq '^(CapabilityBoundingSet|ProtectClock|ProtectHostname|ProtectKernelModules|ProtectKernelLogs)=' "${standalone_unit}" || \
  fail "${standalone_unit} uses a hardening directive the user manager rejects"

# The environment is cleared, and CUDA is pinned to PCI order. Without
# CUDA_DEVICE_ORDER, CUDA sorts devices fastest-first and CUDA0 silently becomes
# the smaller 8 GiB card, which cannot hold the model plus its KV cache.
[[ "${standalone_exec}" == 'ExecStart=/usr/bin/env -i CUDA_DEVICE_ORDER=PCI_BUS_ID '* ]] || \
  fail 'server does not launch with a cleared environment and PCI-ordered CUDA devices'
[[ "${standalone_exec}" == *'--host 127.0.0.1 --port 8080'* ]] || \
  fail 'endpoint is not fixed to IPv4 loopback'
for argument in \
  '--ctx-size ${KEVINBELLM_LLAMA_CTX_SIZE}' \
  '--offline' \
  '--no-agent' \
  '--batch-size ${KEVINBELLM_LLAMA_BATCH_SIZE}' \
  '--ubatch-size ${KEVINBELLM_LLAMA_UBATCH_SIZE}' \
  '--threads ${KEVINBELLM_LLAMA_THREADS}' \
  '--parallel ${KEVINBELLM_LLAMA_PARALLEL}' \
  '--n-gpu-layers ${KEVINBELLM_LLAMA_GPU_LAYERS}' \
  '--split-mode ${KEVINBELLM_LLAMA_SPLIT_MODE}' \
  '--device ${KEVINBELLM_LLAMA_DEVICE_LIST}' \
  '--tensor-split ${KEVINBELLM_LLAMA_TENSOR_SPLIT}' \
  '--cache-type-k ${KEVINBELLM_LLAMA_CACHE_TYPE_K}' \
  '--cache-type-v ${KEVINBELLM_LLAMA_CACHE_TYPE_V}' \
  '--flash-attn ${KEVINBELLM_LLAMA_FLASH_ATTN}' \
  '--load-mode mmap' \
  '--no-mmproj' \
  '--no-webui' \
  '--no-slots' \
  '--spec-type ${KEVINBELLM_LLAMA_SPEC_TYPE}' \
  '--spec-draft-n-max ${KEVINBELLM_LLAMA_SPEC_DRAFT_N_MAX}' \
  '--cache-reuse ${KEVINBELLM_LLAMA_CACHE_REUSE}'; do
  [[ "${standalone_exec}" == *"${argument}"* ]] || fail "ExecStart is missing ${argument}"
done
require_text "${standalone_unit}" 'UnsetEnvironment=XDG_CONFIG_HOME HOME LLAMA_ARG_HF_REPO LLAMA_ARG_HF_FILE LLAMA_ARG_MODEL_URL LLAMA_ARG_DOCKER_REPO LLAMA_ARG_MODELS_PRESET LLAMA_ARG_MODELS_DIR'
require_text "${standalone_unit}" 'InaccessiblePaths=-%h/.ssh -%h/.gnupg -%h/.config -/etc/llama.cpp'
for setting in \
  'Environment=KEVINBELLM_LLAMA_CTX_SIZE=32768' \
  'Environment=KEVINBELLM_LLAMA_BATCH_SIZE=2048' \
  'Environment=KEVINBELLM_LLAMA_UBATCH_SIZE=512' \
  'Environment=KEVINBELLM_LLAMA_THREADS=8' \
  'Environment=KEVINBELLM_LLAMA_PARALLEL=1' \
  'Environment=KEVINBELLM_LLAMA_GPU_LAYERS=all' \
  'Environment=KEVINBELLM_LLAMA_CACHE_TYPE_K=q8_0' \
  'Environment=KEVINBELLM_LLAMA_CACHE_TYPE_V=q8_0' \
  'Environment=KEVINBELLM_LLAMA_FLASH_ATTN=on' \
  'Environment=KEVINBELLM_LLAMA_SPEC_TYPE=draft-mtp' \
  'Environment=KEVINBELLM_LLAMA_SPEC_DRAFT_N_MAX=2' \
  'Environment=KEVINBELLM_LLAMA_CUDA_DEVICES=0,1' \
  'Environment=KEVINBELLM_LLAMA_SPLIT_MODE=layer' \
  'Environment=KEVINBELLM_LLAMA_DEVICE_LIST=CUDA0,CUDA1' \
  'Environment=KEVINBELLM_LLAMA_TENSOR_SPLIT=64,36' \
  'Environment=KEVINBELLM_LLAMA_CACHE_REUSE=0'; do
  require_text "${standalone_unit}" "${setting}"
done

require_text scripts/cluster/download-model.sh "preset='27b-iq4_xs'"
require_text scripts/cluster/download-model.sh "model_repo='unsloth/Qwen3.8-27B-GGUF'"
require_text scripts/cluster/download-model.sh "model_revision='4ca720788d1e01f1bff70c033e0d0028fd02e502'"
require_text scripts/cluster/download-model.sh "model_filename='Qwen3.8-27B-UD-IQ4_XS.gguf'"
require_text scripts/cluster/download-model.sh "model_bytes='14252845984'"
require_text scripts/cluster/download-model.sh "model_sha256='40fac4050e940397dbf13087afd50f4734a11805bf9d65ef8ddd7483470e6199'"
require_text scripts/cluster/download-model.sh 'chmod 600 "${output_file}"'
require_text scripts/cluster/download-model.sh 'Refusing model output-directory symlink:'
supported_preset_count="$(awk '
  /^case "\$\{preset\}" in$/ { in_preset_case = 1; next }
  in_preset_case && /^esac$/ { exit }
  in_preset_case && /^  [[:alnum:]_-]+\)$/ { count++ }
  END { print count + 0 }
' scripts/cluster/download-model.sh)"
[[ "${supported_preset_count}" == '1' ]] || \
  fail "download helper supports ${supported_preset_count} presets instead of exactly one"
require_text scripts/cluster/install-services.sh 'MODEL_PRESET must be 27b-iq4_xs'
require_text scripts/cluster/install-services.sh 'LLAMA_MODEL_ALIAS must be kevinbellm-27b'
require_text scripts/cluster/install-services.sh 'Model file not found:'
require_text scripts/cluster/install-llama-cpp.sh '-DGGML_RPC=OFF'
require_text scripts/cluster/install-llama-cpp.sh "'GGML_RPC=OFF'"
require_text scripts/cluster/install-llama-cpp.sh 'for binary in llama-server llama-cli llama-bench; do'
require_text scripts/cluster/install-llama-cpp.sh "'GGML_RPC:BOOL=ON'"
require_text scripts/cluster/install-llama-cpp.sh '[[ ! -e "${build_dir}/bin/ggml-rpc-server" ]]'
! grep -Fq -- '-DGGML_RPC=ON' scripts/cluster/install-llama-cpp.sh || \
  fail 'llama.cpp build enables RPC'
! grep -Eq 'cmake --build .*--target.*ggml-rpc-server' scripts/cluster/install-llama-cpp.sh || \
  fail 'llama.cpp build targets the RPC server binary'
require_text scripts/cluster/install-services.sh "'GGML_RPC=OFF'"
require_text scripts/cluster/install-services.sh '[[ ! -e "${llama_dir}/build/bin/ggml-rpc-server" ]]'
require_text scripts/cluster/install-services.sh 'sudo loginctl enable-linger "${USER}"'
require_text scripts/cluster/cluster-status.sh 'curl --silent --show-error --fail-with-body --max-time 5'

for runtime_file in \
  "${standalone_unit}" \
  scripts/cluster/cluster-status.sh \
  scripts/cluster/harden-ssh.sh; do
  ! grep -Eqi -- '--rpc|ggml-rpc-server|50052|50053|rpc-(tunnel|worker)|LLAMA_ARG_RPC' "${runtime_file}" || \
    fail "${runtime_file} retains an RPC runtime reference"
done

require_text .env.example 'DEFAULT_MODEL=kevinbellm-27b'
require_text .env.example 'PREFERRED_MODELS=kevinbellm-27b'
require_text .env.example 'CHAT_CONCURRENCY=1'
require_text compose.yaml 'DEFAULT_MODEL: "${DEFAULT_MODEL:-kevinbellm-27b}"'
require_text compose.yaml 'PREFERRED_MODELS: "${PREFERRED_MODELS:-kevinbellm-27b}"'
require_text compose.yaml 'CHAT_CONCURRENCY: "${CHAT_CONCURRENCY:-1}"'
require_text infra/cluster/standalone.example.env 'MODEL_PRESET=27b-iq4_xs'
require_text infra/cluster/standalone.example.env 'LLAMA_MODEL_ALIAS=kevinbellm-27b'

if bash scripts/cluster/download-model.sh --preset definitely-invalid --verify-only >/dev/null 2>&1; then
  fail 'download helper accepted an unknown preset'
fi

if command -v systemd-analyze >/dev/null 2>&1; then
  verify_dir="$(mktemp -d)"
  cleanup_verify_dir() {
    rm -f -- "${verify_dir}/standalone.env" "${verify_dir}/kevinbellm-standalone.service"
    rmdir -- "${verify_dir}"
  }
  trap cleanup_verify_dir EXIT
  printf '%s\n' \
    'MODEL_PATH=/tmp/model.gguf' \
    'LLAMA_MODEL_ALIAS=kevinbellm-27b' >"${verify_dir}/standalone.env"
  sed \
    -e 's|@LLAMA_SERVER_BIN@|/usr/bin/true|g' \
    -e "s|@STANDALONE_ENV@|${verify_dir}/standalone.env|g" \
    "${standalone_unit}" >"${verify_dir}/kevinbellm-standalone.service"
  systemd-analyze --user verify "${verify_dir}/kevinbellm-standalone.service"
fi

printf 'Standalone inference contract checks passed.\n'
