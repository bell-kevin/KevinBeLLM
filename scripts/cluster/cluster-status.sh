#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

role=""
usage() {
  printf 'Usage: cluster-status.sh --role standalone|coordinator|worker\n'
}

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || cluster_die "--role requires a value."
      role="$2"
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
[[ "${role}" == 'standalone' || "${role}" == 'coordinator' || "${role}" == 'worker' ]] || \
  cluster_die "--role must be standalone, coordinator, or worker."
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

check_no_listener() {
  local port="$1"
  local listeners
  listeners="$(ss -H -ltn "sport = :${port}" | awk '{ print $4 }')"
  if [[ -n "${listeners}" ]]; then
    cluster_warn "Unexpected listener on TCP/${port}: ${listeners//$'\n'/, }"
    result=1
  else
    cluster_info "Verified no listener on TCP/${port}"
  fi
}

if [[ "${role}" == 'worker' ]]; then
  systemctl --user --no-pager --full status kevinbellm-rpc-worker.service || result=1
  check_listener 50052 127.0.0.1
elif [[ "${role}" == 'standalone' ]]; then
  systemctl --user --no-pager --full status kevinbellm-llama.service || result=1
  check_listener 8080 127.0.0.1
  check_no_listener 50052
  check_no_listener 50053
  for rpc_unit in kevinbellm-rpc-tunnel.service kevinbellm-rpc-worker.service; do
    if systemctl --user is-active --quiet "${rpc_unit}"; then
      cluster_warn "${rpc_unit} is active during standalone operation."
      result=1
    else
      cluster_info "Verified ${rpc_unit} is inactive"
    fi
    if systemctl --user is-enabled --quiet "${rpc_unit}" 2>/dev/null; then
      cluster_warn "${rpc_unit} remains enabled for a future boot."
      result=1
    else
      cluster_info "Verified ${rpc_unit} is not enabled"
    fi
  done
else
  systemctl --user --no-pager --full status kevinbellm-rpc-tunnel.service kevinbellm-llama.service || result=1
  check_listener 50053 127.0.0.1
  check_listener 8080 127.0.0.1
fi

if [[ "${role}" != 'worker' ]] && command -v curl >/dev/null 2>&1; then
  cluster_info "llama-server health response"
  curl --silent --show-error --max-time 5 http://127.0.0.1:8080/health || result=1
  printf '\n'
  cluster_info "llama-server model response"
  curl --silent --show-error --max-time 5 http://127.0.0.1:8080/v1/models || result=1
  printf '\n'
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader || result=1
fi
exit "${result}"
