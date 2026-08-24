#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

usage() {
  printf 'Usage: cluster-status.sh\n\nReports Machine A inference health and GPU state.\n'
}

while (($#)); do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      cluster_die "Unknown option: $1"
      ;;
  esac
done
cluster_require_command ss

result=0
check_listener() {
  local port="$1"
  local expected="$2"
  local listeners
  listeners="$(ss -H -ltn "sport = :${port}" | awk '{ print $4 }')"
  if [[ -z "${listeners}" ]]; then
    cluster_warn "Nothing is listening on TCP/${port}."
    result=1
    return
  fi
  while IFS= read -r address; do
    if [[ "${address}" != "${expected}:${port}" ]]; then
      cluster_warn "Unsafe/unexpected TCP/${port} listener: ${address} (expected ${expected}:${port} only)"
      result=1
    else
      cluster_info "Verified loopback listener ${address}"
    fi
  done <<<"${listeners}"
}

systemctl --user --no-pager --full status kevinbellm-llama.service || result=1
check_listener 8080 127.0.0.1

if command -v curl >/dev/null 2>&1; then
  cluster_info "llama-server health response"
  curl --silent --show-error --fail-with-body --max-time 5 http://127.0.0.1:8080/health || result=1
  printf '\n'
  cluster_info "llama-server model response"
  curl --silent --show-error --fail-with-body --max-time 5 http://127.0.0.1:8080/v1/models || result=1
  printf '\n'
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  cluster_info "GPU clocks, power, negotiated PCIe links, utilization, and memory"
  nvidia-smi \
    --query-gpu=index,name,pstate,persistence_mode,temperature.gpu,power.draw,power.limit,clocks.current.sm,clocks.max.sm,pcie.link.gen.gpucurrent,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max,utilization.gpu,memory.used,memory.total \
    --format=csv,noheader || result=1
fi

governors="$(cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sort -u || true)"
if [[ -n "${governors}" ]]; then
  cluster_info "CPU frequency governor(s): ${governors//$'\n'/,}"
fi

main_pid="$(systemctl --user show kevinbellm-llama.service -p MainPID --value 2>/dev/null || true)"
if [[ "${main_pid}" =~ ^[1-9][0-9]*$ && -e "/proc/${main_pid}/exe" ]]; then
  server_bin="$(readlink -f "/proc/${main_pid}/exe")"
  install_dir="$(dirname -- "$(dirname -- "$(dirname -- "${server_bin}")")")"
  build_spec="${install_dir}/KEVINBELLM_BUILD_SPEC.txt"
  cmake_cache="$(dirname -- "$(dirname -- "${server_bin}")")/CMakeCache.txt"
  cluster_info "Running llama-server: ${server_bin}"
  if [[ -r "${build_spec}" ]]; then
    cluster_info "Pinned build identity and performance contract"
    sed -n '1,20p' "${build_spec}"
  fi
  if [[ -r "${cmake_cache}" ]]; then
    cluster_info "Realized CMake performance flags"
    grep -E '^(CMAKE_CUDA_ARCHITECTURES|GGML_(AVX|AVX2|BMI2|CUDA|CUDA_FA|CUDA_GRAPHS|F16C|FMA|NATIVE|RPC|SSE42)):' \
      "${cmake_cache}" | sort
  fi
fi
exit "${result}"
