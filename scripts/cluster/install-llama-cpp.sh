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
# Keep the fixed-host build separate from pre-optimization and ad-hoc candidate
# trees. Besides making rollback obvious, this prevents CMake from accepting a
# cache whose absolute source/build paths belong to a relocated experiment.
install_dir="${HOME}/.local/opt/llama.cpp-${llama_ref}-bdver2"
jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"

usage() {
  cat <<EOF
Usage: install-llama-cpp.sh [--install-dir PATH] [--jobs N]

Builds the immutable llama.cpp ${llama_ref} commit for Ampere compute capability
8.6 on Machine A. The build includes CUDA inference and disables network RPC.
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
cuda_major="${BASH_REMATCH[1]}"
((cuda_major >= 11)) || cluster_die "CUDA 11 or newer is required for compute capability 8.6."
cuda_compiler="$(command -v nvcc)"
cuda_compiler_real="$(readlink -f "${cuda_compiler}")"

source_dir="${install_dir}/source"
build_dir="${install_dir}/build"
spec_file="${install_dir}/KEVINBELLM_BUILD_SPEC.txt"
new_clone=0

[[ ! -L "${build_dir}" ]] || cluster_die "Refusing build-directory symlink: ${build_dir}"
if [[ -f "${build_dir}/CMakeCache.txt" ]] && \
   grep -qxF 'GGML_RPC:BOOL=ON' "${build_dir}/CMakeCache.txt"; then
  cluster_die "Refusing to reuse an RPC-enabled build directory: ${build_dir}. Build into a fresh install directory."
fi
[[ ! -e "${build_dir}/bin/ggml-rpc-server" ]] || \
  cluster_die "Refusing to reuse a build containing ggml-rpc-server: ${build_dir}. Build into a fresh install directory."

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
  -DCMAKE_CUDA_ARCHITECTURES:STRING=86
  -DCMAKE_CUDA_COMPILER="${cuda_compiler}"
  # FX-8370 is GCC's bdver2 target, except this chip does not expose LWP.
  # Global language flags are intentional: llama's grammar, sampler, vocab,
  # tokenization, and Unicode translation units sit outside ggml-cpu.
  '-DCMAKE_C_FLAGS:STRING=-march=bdver2 -mno-lwp'
  '-DCMAKE_CXX_FLAGS:STRING=-march=bdver2 -mno-lwp'
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON
  -DGGML_CUDA=ON
  -DGGML_CUDA_FA=ON
  -DGGML_CUDA_GRAPHS=ON
  -DGGML_NATIVE=OFF
  -DGGML_RPC=OFF
  # Machine A's fixed Piledriver CPU supports SSE4.2, AVX, FMA, and F16C, but
  # not AVX2 or BMI2. Spell out that exact ISA instead of either compiling a
  # scalar CPU path or allowing CMake's generic x86 defaults to emit unsupported
  # instructions. These options also keep ggml-cpu's own dispatch contract
  # explicit instead of relying only on the global fixed-host compiler target.
  -DGGML_SSE42=ON
  -DGGML_AVX=ON
  -DGGML_AVX2=OFF
  -DGGML_BMI2=OFF
  -DGGML_FMA=ON
  -DGGML_F16C=ON
  -DBUILD_SHARED_LIBS=OFF
  -DLLAMA_BUILD_EXAMPLES=OFF
  -DLLAMA_BUILD_TESTS=OFF
  -DLLAMA_BUILD_UI=OFF
  -DLLAMA_USE_PREBUILT_UI=OFF
  -DLLAMA_OPENSSL=OFF
)

cluster_info "Configuring the pinned Ampere CUDA build"
cmake "${cmake_args[@]}"
cmake_cache="${build_dir}/CMakeCache.txt"
for realized_setting in \
  'CMAKE_BUILD_TYPE:STRING=Release' \
  'CMAKE_CUDA_ARCHITECTURES:STRING=86' \
  'CMAKE_C_FLAGS:STRING=-march=bdver2 -mno-lwp' \
  'CMAKE_CXX_FLAGS:STRING=-march=bdver2 -mno-lwp' \
  'CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON' \
  'GGML_CUDA:BOOL=ON' \
  'GGML_CUDA_FA:BOOL=ON' \
  'GGML_CUDA_GRAPHS:BOOL=ON' \
  'GGML_NATIVE:BOOL=OFF' \
  'GGML_RPC:BOOL=OFF' \
  'GGML_SSE42:BOOL=ON' \
  'GGML_AVX:BOOL=ON' \
  'GGML_AVX2:BOOL=OFF' \
  'GGML_BMI2:BOOL=OFF' \
  'GGML_FMA:BOOL=ON' \
  'GGML_F16C:BOOL=ON' \
  'BUILD_SHARED_LIBS:BOOL=OFF' \
  'LLAMA_BUILD_EXAMPLES:BOOL=OFF' \
  'LLAMA_BUILD_TESTS:BOOL=OFF' \
  'LLAMA_BUILD_UI:BOOL=OFF' \
  'LLAMA_USE_PREBUILT_UI:BOOL=OFF' \
  'LLAMA_OPENSSL:BOOL=OFF'; do
  grep -qxF "${realized_setting}" "${cmake_cache}" || \
    cluster_die "CMake did not realize required setting: ${realized_setting}"
done
realized_cuda_compiler="$(sed -n 's/^CMAKE_CUDA_COMPILER:[^=]*=//p' "${cmake_cache}")"
[[ -n "${realized_cuda_compiler}" ]] || cluster_die "CMake did not record its CUDA compiler."
[[ "$(readlink -f "${realized_cuda_compiler}")" == "${cuda_compiler_real}" ]] || \
  cluster_die "CMake selected an unexpected CUDA compiler: ${realized_cuda_compiler}"
compile_commands="${build_dir}/compile_commands.json"
[[ -r "${compile_commands}" ]] || cluster_die "CMake did not export compile commands."
for fixed_host_source in \
  '/src/llama-sampler.cpp' \
  '/src/llama-grammar.cpp' \
  '/src/llama-vocab.cpp' \
  '/src/unicode.cpp' \
  '/common/sampling.cpp'; do
  compile_command="$(grep -m 1 -F "${fixed_host_source}" "${compile_commands}" || true)"
  [[ "${compile_command}" == *'-march=bdver2 -mno-lwp'* ]] || \
    cluster_die "Fixed-host ISA flags did not reach ${fixed_host_source}."
done
cuda_compile_command="$(grep -m 1 -F '/fattn-vec-instance-f16-f16.cu' "${compile_commands}" || true)"
[[ "${cuda_compile_command}" == *'--generate-code=arch=compute_86,code=[compute_86,sm_86]'* ]] || \
  cluster_die "CUDA compile commands do not target compute_86/sm_86."
cluster_info "Building llama-server, llama-cli, and llama-bench"
cmake --build "${build_dir}" --parallel "${jobs}" --target llama-server llama-cli llama-bench

for binary in llama-server llama-cli llama-bench; do
  [[ -x "${build_dir}/bin/${binary}" ]] || cluster_die "Build did not produce ${binary}."
done
[[ ! -e "${build_dir}/bin/ggml-rpc-server" ]] || \
  cluster_die "The RPC-disabled build unexpectedly produced ggml-rpc-server."
linked_cudart_major="$(ldd "${build_dir}/bin/llama-server" | sed -n 's/.*libcudart\.so\.\([0-9][0-9]*\).*/\1/p' | head -n 1)"
if [[ -n "${linked_cudart_major}" && "${linked_cudart_major}" != "${cuda_major}" ]]; then
  cluster_die "CUDA toolkit/runtime mismatch: nvcc ${cuda_major}, libcudart ${linked_cudart_major}."
fi

{
  printf 'repository=%s\n' "${llama_repo}"
  printf 'ref=%s\n' "${llama_ref}"
  printf 'commit=%s\n' "${llama_commit}"
  printf '%s\n' 'CMAKE_BUILD_TYPE=Release'
  printf '%s\n' 'CMAKE_CUDA_ARCHITECTURES=86'
  printf 'CMAKE_CUDA_COMPILER=%s\n' "${cuda_compiler_real}"
  printf 'CUDA_RUNTIME_MAJOR=%s\n' "${linked_cudart_major:-static}"
  printf '%s\n' 'CMAKE_C_FLAGS=-march=bdver2 -mno-lwp'
  printf '%s\n' 'CMAKE_CXX_FLAGS=-march=bdver2 -mno-lwp'
  printf '%s\n' 'CMAKE_EXPORT_COMPILE_COMMANDS=ON'
  printf '%s\n' 'GGML_CUDA=ON'
  printf '%s\n' 'GGML_CUDA_FA=ON'
  printf '%s\n' 'GGML_CUDA_GRAPHS=ON'
  printf '%s\n' 'GGML_NATIVE=OFF'
  printf '%s\n' 'GGML_RPC=OFF'
  printf '%s\n' 'GGML_SSE42=ON'
  printf '%s\n' 'GGML_AVX=ON'
  printf '%s\n' 'GGML_AVX2=OFF'
  printf '%s\n' 'GGML_BMI2=OFF'
  printf '%s\n' 'GGML_FMA=ON'
  printf '%s\n' 'GGML_F16C=ON'
  printf '%s\n' 'BUILD_SHARED_LIBS=OFF'
  printf '%s\n' 'LLAMA_BUILD_EXAMPLES=OFF'
  printf '%s\n' 'LLAMA_BUILD_TESTS=OFF'
  printf '%s\n' 'LLAMA_BUILD_UI=OFF'
  printf '%s\n' 'LLAMA_USE_PREBUILT_UI=OFF'
  printf '%s\n' 'LLAMA_OPENSSL=OFF'
  sha256sum \
    "${build_dir}/bin/llama-server" \
    "${build_dir}/bin/llama-cli" \
    "${build_dir}/bin/llama-bench"
} >"${spec_file}"
chmod 644 "${spec_file}"

cluster_info "Installed pinned llama.cpp build at ${install_dir}"
"${build_dir}/bin/llama-server" --version
