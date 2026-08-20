#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

preset='9b-q6_k'
output_file=''
verify_only=0

usage() {
  cat <<EOF
Usage: download-model.sh [--preset NAME] [--output PATH] [--verify-only]

Presets:
  9b-q6_k       Qwen3.5-9B Q6_K (default; standalone Machine A service)
  27b-iq4_xs    Qwen3.8-27B UD-IQ4_XS (Machine A standalone, split over both GPUs)
  27b-q4_k_m    Qwen3.5-27B Q4_K_M (optional two-node RPC service)
  embed-m3      BAAI bge-m3 Q8_0 embeddings (optional Machine B retrieval)
  rerank-m3     BAAI bge-reranker-v2-m3 Q8_0 (optional Machine B retrieval)

Downloads the selected artifact at an immutable revision, then verifies both
byte count and SHA-256. A .part file is kept for safe resume after an
interruption. --verify-only performs no network access.
EOF
}

while (($#)); do
  case "$1" in
    --preset)
      (($# >= 2)) || cluster_die "--preset requires a value."
      preset="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || cluster_die "--output requires a value."
      output_file="$2"
      shift 2
      ;;
    --verify-only)
      verify_only=1
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

case "${preset}" in
  9b-q6_k)
    model_repo='bartowski/Qwen_Qwen3.5-9B-GGUF'
    model_revision='182be2fd6c7bc44887d88a91cb03ff009cc9f549'
    model_filename='Qwen_Qwen3.5-9B-Q6_K.gguf'
    model_bytes='7958818848'
    model_sha256='073a9275e65d9c8cd2819cf5f77b99fbaa6e87ba591da6bbaa86ec073a64bfef'
    ;;
  27b-iq4_xs)
    model_repo='unsloth/Qwen3.8-27B-GGUF'
    model_revision='4ca720788d1e01f1bff70c033e0d0028fd02e502'
    model_filename='Qwen3.8-27B-UD-IQ4_XS.gguf'
    model_bytes='14252845984'
    model_sha256='40fac4050e940397dbf13087afd50f4734a11805bf9d65ef8ddd7483470e6199'
    ;;
  27b-q4_k_m)
    model_repo='bartowski/Qwen_Qwen3.5-27B-GGUF'
    model_revision='d7b113c40283f4d99f4eb0ec20d126ad653cc736'
    model_filename='Qwen_Qwen3.5-27B-Q4_K_M.gguf'
    model_bytes='17984872928'
    model_sha256='81657841d62f1821c748d0fea6c260b7d3508844fe4e9250253ef81c4e4d9edf'
    ;;
  embed-m3)
    # MIT-licensed BAAI bge-m3, 568M parameters, 1024-dimension CLS embeddings.
    # About 610 MiB of the RTX 3070's 8 GiB; Machine A never loads it.
    model_repo='gpustack/bge-m3-GGUF'
    model_revision='2d48f1737679ad900d5c26c5aad5410e9c70fdca'
    model_filename='bge-m3-Q8_0.gguf'
    model_bytes='634553760'
    model_sha256='950f4a8e5e19477a6d3c26d2f162233c20002c601f75e4b002e3239997821167'
    ;;
  rerank-m3)
    # Apache-2.0 BAAI bge-reranker-v2-m3, the cross-encoder counterpart to
    # embed-m3. llama.cpp serves it through --reranking.
    model_repo='gpustack/bge-reranker-v2-m3-GGUF'
    model_revision='3093af03b1a635e67b084b1d8c03c5f5e020fd05'
    model_filename='bge-reranker-v2-m3-Q8_0.gguf'
    model_bytes='635676416'
    model_sha256='a43c7c9b11a4c1517e5bf95151960e1621d1b72f7a493364b01e386cf1aaa1d3'
    ;;
  *)
    cluster_die "Unknown model preset: ${preset}"
    ;;
esac
readonly model_repo model_revision model_filename model_bytes model_sha256
output_file="${output_file:-${HOME}/models/${model_filename}}"

cluster_require_non_root
cluster_require_command sha256sum
cluster_require_command stat
if ((verify_only == 0)); then
  cluster_require_command curl
fi

verify_model() {
  local candidate="$1"
  local actual_bytes actual_hash
  actual_bytes="$(stat -c '%s' "${candidate}")"
  [[ "${actual_bytes}" == "${model_bytes}" ]] || return 1
  actual_hash="$(sha256sum "${candidate}" | awk '{ print $1 }')"
  [[ "${actual_hash}" == "${model_sha256}" ]]
}

if [[ -L "${output_file}" ]]; then
  cluster_die "Refusing model output symlink: ${output_file}"
elif [[ -f "${output_file}" ]]; then
  if verify_model "${output_file}"; then
    chmod 600 "${output_file}"
    cluster_info "Verified ${preset} model: ${output_file}"
    exit 0
  fi
  cluster_die "Existing output has the wrong size or checksum; refusing to overwrite ${output_file}."
elif [[ -e "${output_file}" ]]; then
  cluster_die "Output exists and is not a regular file: ${output_file}"
fi

if ((verify_only)); then
  cluster_die "Model file does not exist: ${output_file}"
fi

output_dir="$(dirname -- "${output_file}")"
if [[ -L "${output_dir}" ]]; then
  cluster_die "Refusing model output-directory symlink: ${output_dir}"
elif [[ -e "${output_dir}" ]]; then
  [[ -d "${output_dir}" ]] || cluster_die "Model output parent is not a directory: ${output_dir}"
else
  install -d -m 700 "${output_dir}"
fi
partial_file="${output_file}.part"
download_url="https://huggingface.co/${model_repo}/resolve/${model_revision}/${model_filename}?download=true"

if [[ -L "${partial_file}" ]]; then
  cluster_die "Refusing partial-download symlink: ${partial_file}"
elif [[ -e "${partial_file}" && ! -f "${partial_file}" ]]; then
  cluster_die "Partial-download path is not a regular file: ${partial_file}"
fi
if [[ -f "${partial_file}" ]] && (( $(stat -c '%s' "${partial_file}") > model_bytes )); then
  cluster_die "Partial file is larger than expected; inspect or remove it manually: ${partial_file}"
fi

cluster_info "Downloading pinned ${preset} model (${model_bytes} bytes; .part downloads resume)"
curl \
  --fail \
  --location \
  --retry 5 \
  --retry-delay 2 \
  --continue-at - \
  --output "${partial_file}" \
  "${download_url}"

cluster_info "Verifying byte count and SHA-256 (this can take a while)"
verify_model "${partial_file}" || cluster_die \
  "Downloaded model failed verification. Leave ${partial_file} quarantined and investigate before retrying."
mv -- "${partial_file}" "${output_file}"
chmod 600 "${output_file}"
cluster_info "Verified model installed at ${output_file}"
