#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

preset='27b-iq4_xs'
output_file=''
verify_only=0

usage() {
  cat <<EOF
Usage: download-model.sh [--preset NAME] [--output PATH] [--verify-only]

Presets:
  27b-iq4_xs    Qwen3.8-27B UD-IQ4_XS (default; layer-split over both GPUs)
  9b-q6_k       Qwen3.5-9B Q6_K (smaller, much faster, RTX 3060 alone)

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
