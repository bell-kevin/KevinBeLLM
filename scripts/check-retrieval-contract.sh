#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Pins the optional Machine B retrieval profile. The load-bearing invariant is
# that this feature stays off by default and never becomes a dependency of
# Machine A's everyday inference, application, or remote-access path.
set -euo pipefail

cd -- "$(git rev-parse --show-toplevel)"

fail() {
  printf 'Retrieval contract check failed: %s\n' "$*" >&2
  exit 1
}

require_text() {
  local file="$1"
  local text="$2"
  grep -Fq -- "${text}" "${file}" || fail "${file} is missing: ${text}"
}

refuse_text() {
  local file="$1"
  local text="$2"
  ! grep -Fq -- "${text}" "${file}" || fail "${file} must not contain: ${text}"
}

embedding_unit='systemd/cluster/llama-embedding.service.in'
reranker_unit='systemd/cluster/llama-reranker.service.in'
api_unit='systemd/cluster/doc-retrieval.service.in'
tunnel_unit='systemd/cluster/retrieval-tunnel.service.in'

for unit in "${embedding_unit}" "${reranker_unit}" "${api_unit}" "${tunnel_unit}"; do
  [[ -f "${unit}" ]] || fail "missing unit template: ${unit}"
  ! grep -Eq '^(CapabilityBoundingSet|ProtectClock|ProtectHostname|ProtectKernelModules|ProtectKernelLogs)=' "${unit}" || \
    fail "${unit} uses a hardening directive unsupported by Ubuntu's user manager"
done

# --- Machine B model servers stay loopback-only and RPC-free ----------------
embedding_exec="$(grep '^ExecStart=' "${embedding_unit}")"
reranker_exec="$(grep '^ExecStart=' "${reranker_unit}")"

[[ "${embedding_exec}" == 'ExecStart=/usr/bin/env -i CUDA_VISIBLE_DEVICES=0 '* ]] || \
  fail 'embedding server does not launch with a cleared environment'
[[ "${reranker_exec}" == 'ExecStart=/usr/bin/env -i CUDA_VISIBLE_DEVICES=0 '* ]] || \
  fail 'reranking server does not launch with a cleared environment'
[[ "${embedding_exec}" == *'--host 127.0.0.1 --port 8081'* ]] || \
  fail 'embedding endpoint is not fixed to IPv4 loopback 8081'
[[ "${reranker_exec}" == *'--host 127.0.0.1 --port 8082'* ]] || \
  fail 'reranking endpoint is not fixed to IPv4 loopback 8082'
[[ "${embedding_exec}" == *'--embedding'*'--pooling cls'* ]] || \
  fail 'embedding server does not request CLS-pooled embeddings'
[[ "${reranker_exec}" == *'--reranking'*'--pooling rank'* ]] || \
  fail 'reranking server does not request rank pooling'

for exec_line in "${embedding_exec}" "${reranker_exec}"; do
  [[ "${exec_line}" != *'--rpc'* ]] || fail 'a retrieval model server carries an --rpc argument'
  for argument in '--offline' '--no-agent' '--no-webui' '--no-slots' '--split-mode none' '--device CUDA0'; do
    [[ "${exec_line}" == *"${argument}"* ]] || fail "a retrieval model server is missing ${argument}"
  done
done

for unit in "${embedding_unit}" "${reranker_unit}"; do
  require_text "${unit}" 'UnsetEnvironment=XDG_CONFIG_HOME HOME LLAMA_ARG_RPC LLAMA_ARG_RPC_SERVERS LLAMA_ARG_HF_REPO LLAMA_ARG_HF_FILE LLAMA_ARG_MODEL_URL LLAMA_ARG_DOCKER_REPO LLAMA_ARG_MODELS_PRESET LLAMA_ARG_MODELS_DIR'
  require_text "${unit}" 'InaccessiblePaths=-%h/.ssh -%h/.gnupg -%h/.config -/etc/llama.cpp'
  refuse_text "${unit}" 'ACKNOWLEDGE_LLAMA_RPC_RCE'
done

# Both models are non-causal encoders: a micro-batch smaller than the context
# makes llama.cpp refuse whole sequences at request time.
require_text "${embedding_unit}" 'Environment=KEVINBELLM_EMBED_CTX_SIZE=4096'
require_text "${embedding_unit}" 'Environment=KEVINBELLM_EMBED_UBATCH_SIZE=4096'
require_text "${reranker_unit}" 'Environment=KEVINBELLM_RERANK_CTX_SIZE=4096'
require_text "${reranker_unit}" 'Environment=KEVINBELLM_RERANK_UBATCH_SIZE=4096'

# --- The retrieval API is loopback-only and read-only ----------------------
api_exec="$(grep '^ExecStart=' "${api_unit}")"
[[ "${api_exec}" == *'--host 127.0.0.1 --port 8091'* ]] || \
  fail 'retrieval API is not fixed to IPv4 loopback 8091'
require_text "${api_unit}" 'Environment=EMBEDDING_BASE_URL=http://127.0.0.1:8081'
require_text "${api_unit}" 'Environment=RERANKER_BASE_URL=http://127.0.0.1:8082'
require_text "${api_unit}" 'ProtectHome=read-only'
require_text "${api_unit}" 'ProtectSystem=strict'
# Fixed endpoints must be declared after EnvironmentFile= so the private file
# cannot redirect either model backend off loopback.
environment_file_line="$(grep -n '^EnvironmentFile=' "${api_unit}" | cut -d: -f1)"
embedding_url_line="$(grep -n '^Environment=EMBEDDING_BASE_URL=' "${api_unit}" | cut -d: -f1)"
reranker_url_line="$(grep -n '^Environment=RERANKER_BASE_URL=' "${api_unit}" | cut -d: -f1)"
((environment_file_line < embedding_url_line && environment_file_line < reranker_url_line)) || \
  fail 'the private env file can override the retrieval API model endpoints'

# --- The Machine A tunnel is optional and never load-bearing ----------------
tunnel_exec="$(grep '^ExecStart=' "${tunnel_unit}")"
[[ "${tunnel_exec}" == *'-L 127.0.0.1:8091:127.0.0.1:8091'* ]] || \
  fail 'retrieval tunnel does not forward loopback 8091 to worker loopback 8091'
for option in \
  'BatchMode=yes' \
  'ExitOnForwardFailure=yes' \
  'IdentitiesOnly=yes' \
  'StrictHostKeyChecking=yes' \
  'UserKnownHostsFile=@RETRIEVAL_KNOWN_HOSTS@'; do
  [[ "${tunnel_exec}" == *"${option}"* ]] || fail "retrieval tunnel is missing ssh option ${option}"
done
[[ "${tunnel_exec}" != *':50052'* && "${tunnel_exec}" != *':50053'* ]] || \
  fail 'retrieval tunnel forwards an RPC port'
# Nothing on Machine A may wait on, require, or be ordered after this unit.
! grep -Eq '^(Requires|Requisite|BindsTo|PartOf|Before)=' "${tunnel_unit}" || \
  fail 'retrieval tunnel declares a dependency that could delay Machine A units'
for machine_a_unit in \
  systemd/cluster/llama-server.service.in \
  systemd/kevinbellm.service.in \
  systemd/kevinbellm-remote.service.in; do
  ! grep -q 'retrieval' "${machine_a_unit}" || \
    fail "${machine_a_unit} references retrieval; Machine A must not depend on Machine B"
done

# The model name the API sends must be the same value the unit passes to
# --alias, or llama-server is asked for a model it does not advertise.
require_text services/doc-retrieval/app/config.py '_model_alias("EMBEDDING_MODEL_ALIAS"'
require_text services/doc-retrieval/app/config.py '_model_alias("RERANKER_MODEL_ALIAS"'
require_text "${embedding_unit}" '--alias ${EMBEDDING_MODEL_ALIAS}'
require_text "${reranker_unit}" '--alias ${RERANKER_MODEL_ALIAS}'
require_text infra/cluster/retrieval.example.env 'EMBEDDING_MODEL_ALIAS='
require_text infra/cluster/retrieval.example.env 'RERANKER_MODEL_ALIAS='

# --- Pinned retrieval model artifacts --------------------------------------
require_text scripts/cluster/download-model.sh "model_repo='gpustack/bge-m3-GGUF'"
require_text scripts/cluster/download-model.sh "model_revision='2d48f1737679ad900d5c26c5aad5410e9c70fdca'"
require_text scripts/cluster/download-model.sh "model_bytes='634553760'"
require_text scripts/cluster/download-model.sh "model_sha256='950f4a8e5e19477a6d3c26d2f162233c20002c601f75e4b002e3239997821167'"
require_text scripts/cluster/download-model.sh "model_repo='gpustack/bge-reranker-v2-m3-GGUF'"
require_text scripts/cluster/download-model.sh "model_revision='3093af03b1a635e67b084b1d8c03c5f5e020fd05'"
require_text scripts/cluster/download-model.sh "model_bytes='635676416'"
require_text scripts/cluster/download-model.sh "model_sha256='a43c7c9b11a4c1517e5bf95151960e1621d1b72f7a493364b01e386cf1aaa1d3'"

if bash scripts/cluster/download-model.sh --preset embed-m3 --verify-only >/dev/null 2>&1; then
  fail 'the embedding preset verified a model that is not present'
fi

# --- The restricted SSH key reaches nothing but the retrieval port ----------
require_text scripts/cluster/install-retrieval-tunnel-key.sh 'permitopen=\"127.0.0.1:${retrieval_port}\"'
require_text scripts/cluster/install-retrieval-tunnel-key.sh 'PermitOpen 127.0.0.1:${retrieval_port}'
require_text scripts/cluster/install-retrieval-tunnel-key.sh "retrieval_port='8091'"
require_text scripts/cluster/install-retrieval-tunnel-key.sh 'command=\"/usr/bin/false\"'
require_text scripts/cluster/install-retrieval-tunnel-key.sh 'AllowTcpForwarding local'
require_text scripts/cluster/install-retrieval-tunnel-key.sh 'already authorized for the RPC tunnel account'
require_text scripts/cluster/install-retrieval-tunnel.sh 'must use its own key, not the RPC tunnel key'
# The Machine B installer must refuse to run on Machine A.
require_text scripts/cluster/install-retrieval.sh 'kevinbellm-llama.service'
require_text scripts/cluster/install-retrieval.sh 'Retrieval belongs on Machine B'
require_text scripts/cluster/install-retrieval.sh '--require-hashes'

# --- The application default is off ----------------------------------------
require_text compose.yaml 'DOC_RETRIEVAL_URL: "${DOC_RETRIEVAL_URL:-}"'
grep -Eq '^[[:space:]]*DOC_RETRIEVAL_URL=' .env.example && \
  fail '.env.example enables document retrieval by default'
require_text .env.example '# DOC_RETRIEVAL_URL=http://127.0.0.1:8091'

if command -v systemd-analyze >/dev/null 2>&1; then
  verify_dir="$(mktemp -d)"
  cleanup_verify_dir() {
    rm -rf -- "${verify_dir}"
  }
  trap cleanup_verify_dir EXIT
  printf '%s\n' \
    'EMBEDDING_MODEL_PATH=/tmp/embed.gguf' \
    'RERANKER_MODEL_PATH=/tmp/rerank.gguf' \
    'RETRIEVAL_INDEX_DIR=/tmp/index' >"${verify_dir}/retrieval.env"
  printf '%s\n' \
    'RETRIEVAL_SSH_TARGET=kevinbellm-retrieval@192.0.2.10' \
    'RETRIEVAL_SSH_PORT=22' >"${verify_dir}/retrieval-client.env"

  render() {
    sed \
      -e 's|@LLAMA_SERVER_BIN@|/usr/bin/true|g' \
      -e 's|@VENV_PYTHON@|/usr/bin/true|g' \
      -e "s|@SERVICE_DIR@|${verify_dir}|g" \
      -e "s|@RETRIEVAL_ENV@|${verify_dir}/retrieval.env|g" \
      -e "s|@RETRIEVAL_CLIENT_ENV@|${verify_dir}/retrieval-client.env|g" \
      -e 's|@RETRIEVAL_TUNNEL_KEY@|/tmp/retrieval_key|g' \
      -e 's|@RETRIEVAL_KNOWN_HOSTS@|/tmp/retrieval_known_hosts|g' \
      "$1" >"$2"
  }
  render "${embedding_unit}" "${verify_dir}/kevinbellm-embedding.service"
  render "${reranker_unit}" "${verify_dir}/kevinbellm-reranker.service"
  render "${api_unit}" "${verify_dir}/kevinbellm-doc-retrieval.service"
  render "${tunnel_unit}" "${verify_dir}/kevinbellm-retrieval-tunnel.service"

  systemd-analyze --user verify \
    "${verify_dir}/kevinbellm-embedding.service" \
    "${verify_dir}/kevinbellm-reranker.service" \
    "${verify_dir}/kevinbellm-doc-retrieval.service" \
    "${verify_dir}/kevinbellm-retrieval-tunnel.service"
fi

printf 'Retrieval contract checks passed.\n'
