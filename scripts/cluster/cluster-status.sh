#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

usage() {
  printf 'Usage: cluster-status.sh\n\nReports Machine A inference health and proves no RPC listener exists.\n'
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

systemctl --user --no-pager --full status kevinbellm-llama.service || result=1
check_listener 8080 127.0.0.1
# There is one host and no RPC. Prove it rather than assuming it: a leftover
# unit from an older two-node install would otherwise be invisible here.
check_no_listener 50052
check_no_listener 50053
for rpc_unit in kevinbellm-rpc-tunnel.service kevinbellm-rpc-worker.service; do
  if systemctl --user is-active --quiet "${rpc_unit}"; then
    cluster_warn "${rpc_unit} is active; this deployment must not run RPC."
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

if command -v curl >/dev/null 2>&1; then
  cluster_info "llama-server health response"
  curl --silent --show-error --fail-with-body --max-time 5 http://127.0.0.1:8080/health || result=1
  printf '\n'
  cluster_info "llama-server model response"
  curl --silent --show-error --fail-with-body --max-time 5 http://127.0.0.1:8080/v1/models || result=1
  printf '\n'
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader || result=1
fi
exit "${result}"
