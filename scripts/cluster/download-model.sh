#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

readonly model_repo='bartowski/Qwen_Qwen3.5-27B-GGUF'
readonly model_revision='d7b113c40283f4d99f4eb0ec20d126ad653cc736'
readonly model_filename='Qwen_Qwen3.5-27B-Q4_K_M.gguf'
readonly model_bytes='17984872928'
readonly model_sha256='81657841d62f1821c748d0fea6c260b7d3508844fe4e9250253ef81c4e4d9edf'
output_file="${HOME}/models/${model_filename}"

usage() {
  cat <<EOF
Usage: download-model.sh [--output PATH]

Downloads ${model_repo}/${model_filename} at immutable revision
${model_revision}, then verifies both byte count and SHA-256. A .part file is
kept for safe resume after an interruption.
EOF
}

while (($#)); do
  case "$1" in
    --output)
      (($# >= 2)) || cluster_die "--output requires a value."
      output_file="$2"
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

cluster_require_non_root
cluster_require_command curl
cluster_require_command sha256sum
cluster_require_command stat

verify_model() {
  local candidate="$1"
  local actual_bytes actual_hash
  actual_bytes="$(stat -c '%s' "${candidate}")"
  [[ "${actual_bytes}" == "${model_bytes}" ]] || return 1
  actual_hash="$(sha256sum "${candidate}" | awk '{ print $1 }')"
  [[ "${actual_hash}" == "${model_sha256}" ]]
}

if [[ -f "${output_file}" ]]; then
  if verify_model "${output_file}"; then
    cluster_info "Verified existing model; no download needed: ${output_file}"
    exit 0
  fi
  cluster_die "Existing output has the wrong size or checksum; refusing to overwrite ${output_file}."
elif [[ -e "${output_file}" ]]; then
  cluster_die "Output exists and is not a regular file: ${output_file}"
fi

install -d -m 700 "$(dirname -- "${output_file}")"
partial_file="${output_file}.part"
download_url="https://huggingface.co/${model_repo}/resolve/${model_revision}/${model_filename}?download=true"

if [[ -f "${partial_file}" ]] && (( $(stat -c '%s' "${partial_file}") > model_bytes )); then
  cluster_die "Partial file is larger than expected; inspect or remove it manually: ${partial_file}"
fi

cluster_info "Downloading the pinned 17.98 GB model (an interrupted .part download can resume)"
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
