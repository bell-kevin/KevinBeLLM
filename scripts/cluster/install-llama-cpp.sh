#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

readonly llama_ref='b10451'
readonly llama_commit='10bf611e533d81f739128304991c5e133c6aebd8'
readonly llama_repo='https://github.com/ggml-org/llama.cpp.git'
install_dir="${HOME}/.local/opt/llama.cpp-${llama_ref}"
jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"

usage() {
  cat <<EOF
Usage: install-llama-cpp.sh [--install-dir PATH] [--jobs N]

Builds the immutable llama.cpp ${llama_ref} commit for Ampere compute capability
8.6. Everyday standalone service needs it only on Machine A. Run the same
EOF
}

while (($#)); do
  case "$1" in
    --install-dir)
      (($# >= 2)) || cluster_die "--install-dir requires a value."
      install_dir="$2"
      shift 2
      ;;
    --jobs)
      (($# >= 2)) || cluster_die "--jobs requires a value."
      jobs="$2"
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
cluster_require_command cmake
cluster_require_command git
cluster_require_command nvidia-smi
cluster_require_command nvcc
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || cluster_die "--jobs must be a positive integer."

nvidia-smi >/dev/null 2>&1 || cluster_die "NVIDIA driver/GPU check failed."
[[ "$(nvcc --version)" =~ release[[:space:]]+([0-9]+)\. ]] || cluster_die "Could not parse the CUDA toolkit version."
((BASH_REMATCH[1] >= 11)) || cluster_die "CUDA 11 or newer is required for compute capability 8.6."

source_dir="${install_dir}/source"
build_dir="${install_dir}/build"
spec_file="${install_dir}/KEVINBELLM_BUILD_SPEC.txt"
new_clone=0

if [[ -e "${install_dir}" && ! -d "${install_dir}" ]]; then
  cluster_die "Install path exists and is not a directory: ${install_dir}"
fi
install -d -m 755 "${install_dir}"

if [[ ! -d "${source_dir}/.git" ]]; then
  if [[ -n "$(find "${source_dir}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    cluster_die "Refusing to replace non-git content in ${source_dir}."
  fi
  cluster_info "Cloning llama.cpp ${llama_ref}"
  git clone --filter=blob:none --no-checkout "${llama_repo}" "${source_dir}"
  new_clone=1
fi

actual_origin="$(git -C "${source_dir}" remote get-url origin)"
[[ "${actual_origin}" == "${llama_repo}" || "${actual_origin}" == 'git@github.com:ggml-org/llama.cpp.git' ]] || \
  cluster_die "Unexpected llama.cpp origin: ${actual_origin}"
if ((new_clone == 0)) && [[ -n "$(git -C "${source_dir}" status --porcelain --untracked-files=normal)" ]]; then
  cluster_die "Existing llama.cpp source has local changes; refusing the pinned checkout in ${source_dir}."
fi

git -C "${source_dir}" fetch --depth 1 origin "${llama_commit}"
git -C "${source_dir}" checkout --detach --force "${llama_commit}"
actual_commit="$(git -C "${source_dir}" rev-parse HEAD)"
[[ "${actual_commit}" == "${llama_commit}" ]] || cluster_die "Pinned commit verification failed."

cmake_args=(
  -S "${source_dir}"
  -B "${build_dir}"
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_CUDA_ARCHITECTURES=86
  -DGGML_CUDA=ON
  -DGGML_NATIVE=OFF
  -DGGML_RPC=ON
  -DGGML_AVX=OFF
  -DGGML_AVX2=OFF
  -DGGML_BMI2=OFF
  -DGGML_FMA=OFF
  -DGGML_F16C=OFF
  -DBUILD_SHARED_LIBS=OFF
  -DLLAMA_BUILD_EXAMPLES=OFF
  -DLLAMA_BUILD_TESTS=OFF
  -DLLAMA_BUILD_UI=OFF
  -DLLAMA_USE_PREBUILT_UI=OFF
  -DLLAMA_OPENSSL=OFF
)

cluster_info "Configuring the pinned Ampere CUDA build (optional RPC capability retained)"
cmake "${cmake_args[@]}"
cluster_info "Building llama-server, ggml-rpc-server, llama-cli, and llama-bench"
cmake --build "${build_dir}" --parallel "${jobs}" --target llama-server ggml-rpc-server llama-cli llama-bench

for binary in llama-server ggml-rpc-server llama-cli llama-bench; do
  [[ -x "${build_dir}/bin/${binary}" ]] || cluster_die "Build did not produce ${binary}."
done

{
  printf 'repository=%s\n' "${llama_repo}"
  printf 'ref=%s\n' "${llama_ref}"
  printf 'commit=%s\n' "${llama_commit}"
  printf '%s\n' 'CMAKE_BUILD_TYPE=Release'
  printf '%s\n' 'CMAKE_CUDA_ARCHITECTURES=86'
  printf '%s\n' 'GGML_CUDA=ON'
  printf '%s\n' 'GGML_NATIVE=OFF'
  printf '%s\n' 'GGML_RPC=ON'
  printf '%s\n' 'GGML_AVX=OFF'
  printf '%s\n' 'GGML_AVX2=OFF'
  printf '%s\n' 'GGML_BMI2=OFF'
  printf '%s\n' 'GGML_FMA=OFF'
  printf '%s\n' 'GGML_F16C=OFF'
  printf '%s\n' 'BUILD_SHARED_LIBS=OFF'
  printf '%s\n' 'LLAMA_BUILD_EXAMPLES=OFF'
  printf '%s\n' 'LLAMA_BUILD_TESTS=OFF'
  printf '%s\n' 'LLAMA_BUILD_UI=OFF'
  printf '%s\n' 'LLAMA_USE_PREBUILT_UI=OFF'
  printf '%s\n' 'LLAMA_OPENSSL=OFF'
  sha256sum \
    "${build_dir}/bin/llama-server" \
    "${build_dir}/bin/ggml-rpc-server" \
    "${build_dir}/bin/llama-cli" \
    "${build_dir}/bin/llama-bench"
} >"${spec_file}"
chmod 644 "${spec_file}"

cluster_info "Installed pinned llama.cpp build at ${install_dir}"
"${build_dir}/bin/llama-server" --version
