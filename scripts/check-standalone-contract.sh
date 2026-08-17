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
rpc_unit='systemd/cluster/llama-server-rpc.service.in'
standalone_exec="$(grep '^ExecStart=' "${standalone_unit}")"
rpc_exec="$(grep '^ExecStart=' "${rpc_unit}")"

for user_unit in \
  "${standalone_unit}" \
  "${rpc_unit}" \
  systemd/cluster/llama-rpc-worker.service.in \
  systemd/cluster/llama-rpc-tunnel.service.in; do
  ! grep -Eq '^(CapabilityBoundingSet|ProtectClock|ProtectHostname|ProtectKernelModules|ProtectKernelLogs)=' "${user_unit}" || \
    fail "${user_unit} uses a hardening directive unsupported by Ubuntu's user manager"
done

[[ "${standalone_exec}" == 'ExecStart=/usr/bin/env -i CUDA_VISIBLE_DEVICES=0 '* ]] || fail 'standalone server does not launch with a cleared environment'
[[ "${standalone_exec}" != *'--rpc'* ]] || fail 'standalone ExecStart contains --rpc'
[[ "${standalone_exec}" == *'--host 127.0.0.1 --port 8080'* ]] || fail 'standalone endpoint is not fixed to IPv4 loopback'
for argument in \
  '--ctx-size ${KEVINBELLM_LLAMA_CTX_SIZE}' \
  '--offline' \
  '--no-agent' \
  '--batch-size ${KEVINBELLM_LLAMA_BATCH_SIZE}' \
  '--ubatch-size ${KEVINBELLM_LLAMA_UBATCH_SIZE}' \
  '--threads ${KEVINBELLM_LLAMA_THREADS}' \
  '--parallel ${KEVINBELLM_LLAMA_PARALLEL}' \
  '--n-gpu-layers ${KEVINBELLM_LLAMA_GPU_LAYERS}' \
  '--split-mode none' \
  '--device CUDA0' \
  '--cache-type-k ${KEVINBELLM_LLAMA_CACHE_TYPE_K}' \
  '--cache-type-v ${KEVINBELLM_LLAMA_CACHE_TYPE_V}' \
  '--flash-attn ${KEVINBELLM_LLAMA_FLASH_ATTN}' \
  '--load-mode mmap' \
  '--no-mmproj' \
  '--no-webui' \
  '--no-slots' \
  '--spec-type draft-mtp' \
  '--spec-draft-n-max ${KEVINBELLM_LLAMA_SPEC_DRAFT_N_MAX}'; do
  [[ "${standalone_exec}" == *"${argument}"* ]] || fail "standalone ExecStart is missing ${argument}"
done
! grep -Eq '^(Requires|After)=.*rpc' "${standalone_unit}" || fail 'standalone unit depends on RPC'
! grep -Fq 'ACKNOWLEDGE_LLAMA_RPC_RCE' "${standalone_unit}" || fail 'standalone unit has an RPC acknowledgment gate'
require_text "${standalone_unit}" 'UnsetEnvironment=XDG_CONFIG_HOME HOME LLAMA_ARG_RPC LLAMA_ARG_RPC_SERVERS LLAMA_ARG_HF_REPO LLAMA_ARG_HF_FILE LLAMA_ARG_MODEL_URL LLAMA_ARG_DOCKER_REPO LLAMA_ARG_MODELS_PRESET LLAMA_ARG_MODELS_DIR'
require_text "${standalone_unit}" 'InaccessiblePaths=-%h/.ssh -%h/.gnupg -%h/.config -/etc/llama.cpp'
for setting in \
  'Environment=KEVINBELLM_LLAMA_CTX_SIZE=32768' \
  'Environment=KEVINBELLM_LLAMA_BATCH_SIZE=2048' \
  'Environment=KEVINBELLM_LLAMA_UBATCH_SIZE=512' \
  'Environment=KEVINBELLM_LLAMA_THREADS=8' \
  'Environment=KEVINBELLM_LLAMA_PARALLEL=1' \
  'Environment=KEVINBELLM_LLAMA_GPU_LAYERS=all' \
  'Environment=KEVINBELLM_LLAMA_CACHE_TYPE_K=f16' \
  'Environment=KEVINBELLM_LLAMA_CACHE_TYPE_V=f16' \
  'Environment=KEVINBELLM_LLAMA_FLASH_ATTN=on' \
  'Environment=KEVINBELLM_LLAMA_SPEC_DRAFT_N_MAX=2'; do
  require_text "${standalone_unit}" "${setting}"
done

# The optional Machine B retrieval profile must leave the everyday inference
# unit completely alone: no extra resident model, no extra argument, and no
# ordering relationship that could delay Machine A's start.
for forbidden in '--embedding' '--reranking' 'retrieval' 'DOC_RETRIEVAL' '8091'; do
  ! grep -Fq -- "${forbidden}" "${standalone_unit}" || \
    fail "standalone unit references ${forbidden}; Machine A must not host or await retrieval"
done
! grep -Eq '^(Requires|Requisite|BindsTo|PartOf|After|Wants)=.*(retrieval|embedding|reranker)' "${standalone_unit}" || \
  fail 'standalone unit is ordered against a Machine B retrieval unit'

[[ "${rpc_exec}" == *'--rpc 127.0.0.1:50053'* ]] || fail 'optional coordinator lost its loopback RPC endpoint'
[[ "${rpc_exec}" == *'--no-agent'* ]] || fail 'optional coordinator does not disable llama.cpp agent mode'
[[ "${rpc_exec}" == *'--offline'* ]] || fail 'optional coordinator does not force offline mode'
[[ "${rpc_exec}" == *'--no-webui'* ]] || fail 'optional coordinator does not disable the embedded web UI'
require_text "${rpc_unit}" 'Requires=kevinbellm-rpc-tunnel.service'
require_text "${rpc_unit}" 'ACKNOWLEDGE_LLAMA_RPC_RCE'
require_text "${rpc_unit}" 'UnsetEnvironment=XDG_CONFIG_HOME HOME LLAMA_ARG_RPC LLAMA_ARG_RPC_SERVERS LLAMA_ARG_HF_REPO LLAMA_ARG_HF_FILE LLAMA_ARG_MODEL_URL LLAMA_ARG_DOCKER_REPO LLAMA_ARG_MODELS_PRESET LLAMA_ARG_MODELS_DIR LLAMA_ARG_MCP_SERVERS_CONFIG LLAMA_ARG_MCP_SERVERS_JSON LLAMA_ARG_TOOLS LLAMA_ARG_TOOLS_RUNTIME LLAMA_ARG_AGENT LLAMA_SERVER_ROUTER_PORT LLAMA_SERVER_CHILD_MODE LLAMA_APP_CMD AIP_MODE AIP_HEALTH_ROUTE AIP_PREDICT_ROUTE AIP_HTTP_PORT'
require_text "${rpc_unit}" 'InaccessiblePaths=-%h/.ssh -%h/.gnupg -%h/.config -/etc/llama.cpp'

require_text scripts/cluster/download-model.sh "preset='9b-q6_k'"
require_text scripts/cluster/download-model.sh "model_revision='182be2fd6c7bc44887d88a91cb03ff009cc9f549'"
require_text scripts/cluster/download-model.sh "model_bytes='7958818848'"
require_text scripts/cluster/download-model.sh "model_sha256='073a9275e65d9c8cd2819cf5f77b99fbaa6e87ba591da6bbaa86ec073a64bfef'"
require_text scripts/cluster/download-model.sh "model_revision='d7b113c40283f4d99f4eb0ec20d126ad653cc736'"
require_text scripts/cluster/download-model.sh 'chmod 600 "${output_file}"'
require_text scripts/cluster/download-model.sh 'Refusing model output-directory symlink:'
require_text scripts/cluster/install-services.sh "--role standalone|coordinator|worker"
require_text scripts/cluster/install-services.sh 'Standalone mode does not use RPC; remove --acknowledge-rpc-risk.'
require_text scripts/cluster/install-services.sh 'Standalone model file not found:'
require_text scripts/cluster/install-services.sh 'cluster_require_rpc_ack "${env_file}"'
require_text scripts/cluster/install-services.sh 'kevinbellm-rpc-tunnel.service'
require_text scripts/cluster/install-services.sh 'kevinbellm-rpc-worker.service'
require_text scripts/cluster/install-services.sh 'systemctl --user disable --now "${unsafe_unit}"'
require_text scripts/cluster/install-services.sh 'Could not disable every prior inference/RPC unit'
require_text scripts/cluster/install-services.sh 'for rpc_port in 50052 50053; do'
require_text scripts/cluster/install-services.sh 'ss -H -ltn "sport = :${rpc_port}"'
require_text scripts/cluster/install-services.sh 'Refusing standalone install while an RPC port remains open.'
require_text scripts/cluster/install-services.sh 'systemd/cluster/llama-server-rpc.service.in'
require_text scripts/cluster/install-services.sh 'linger_enabled="$(loginctl show-user "${USER}" -p Linger --value 2>/dev/null || true)"'
require_text scripts/cluster/install-services.sh 'sudo loginctl enable-linger "${USER}"'
require_text scripts/cluster/cluster-status.sh 'curl --silent --show-error --fail-with-body --max-time 5'

shutdown_line="$(grep -n -m1 'for unsafe_unit in' scripts/cluster/install-services.sh | cut -d: -f1)"
env_check_line="$(grep -n -m1 'if \[\[ -z "${env_file}" \]\]' scripts/cluster/install-services.sh | cut -d: -f1)"
build_check_line="$(grep -n -m1 '\[\[ -d "${llama_dir}" \]\]' scripts/cluster/install-services.sh | cut -d: -f1)"
[[ -n "${shutdown_line}" && -n "${env_check_line}" && -n "${build_check_line}" ]] || \
  fail 'could not locate standalone fail-closed ordering checks'
((shutdown_line < env_check_line && shutdown_line < build_check_line)) || \
  fail 'standalone disables old RPC-capable units only after a fallible env/build check'

require_text .env.example 'DEFAULT_MODEL=kevinbellm-9b'
require_text .env.example 'PREFERRED_MODELS=kevinbellm-9b,kevinbellm-27b'
require_text .env.example 'CHAT_CONCURRENCY=1'
require_text compose.yaml 'DEFAULT_MODEL: "${DEFAULT_MODEL:-kevinbellm-9b}"'
require_text compose.yaml 'CHAT_CONCURRENCY: "${CHAT_CONCURRENCY:-1}"'
require_text infra/cluster/standalone.example.env 'MODEL_PRESET=9b-q6_k'
require_text infra/cluster/standalone.example.env 'LLAMA_MODEL_ALIAS=kevinbellm-9b'

if bash scripts/cluster/download-model.sh --preset definitely-invalid --verify-only >/dev/null 2>&1; then
  fail 'download helper accepted an unknown preset'
fi

if command -v systemd-analyze >/dev/null 2>&1; then
  verify_dir="$(mktemp -d)"
  cleanup_verify_dir() {
    rm -f -- \
      "${verify_dir}/standalone.env" \
      "${verify_dir}/coordinator.env" \
      "${verify_dir}/kevinbellm-standalone.service" \
      "${verify_dir}/kevinbellm-rpc-tunnel.service" \
      "${verify_dir}/kevinbellm-rpc.service"
    rmdir -- "${verify_dir}"
  }
  trap cleanup_verify_dir EXIT
  printf '%s\n' \
    'MODEL_PATH=/tmp/model.gguf' \
    'LLAMA_MODEL_ALIAS=kevinbellm-9b' >"${verify_dir}/standalone.env"
  sed \
    -e 's|@LLAMA_SERVER_BIN@|/usr/bin/true|g' \
    -e "s|@STANDALONE_ENV@|${verify_dir}/standalone.env|g" \
    "${standalone_unit}" >"${verify_dir}/kevinbellm-standalone.service"

  printf '%s\n' \
    'ACKNOWLEDGE_LLAMA_RPC_RCE=YES_I_ACCEPT_UNAUTHENTICATED_RCE_RISK' \
    'MODEL_PATH=/tmp/model.gguf' \
    'LLAMA_MODEL_ALIAS=kevinbellm-27b' >"${verify_dir}/coordinator.env"
  printf '%s\n' \
    '[Service]' \
    'Type=oneshot' \
    'ExecStart=/usr/bin/true' >"${verify_dir}/kevinbellm-rpc-tunnel.service"
  sed \
    -e 's|@LLAMA_SERVER_BIN@|/usr/bin/true|g' \
    -e "s|@COORDINATOR_ENV@|${verify_dir}/coordinator.env|g" \
    "${rpc_unit}" >"${verify_dir}/kevinbellm-rpc.service"

  systemd-analyze --user verify \
    "${verify_dir}/kevinbellm-standalone.service" \
    "${verify_dir}/kevinbellm-rpc-tunnel.service" \
    "${verify_dir}/kevinbellm-rpc.service"
fi

printf 'Standalone inference contract checks passed.\n'
