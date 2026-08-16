#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

role=""
usage() {
  printf 'Usage: cluster-status.sh --role coordinator|worker\n'
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
[[ "${role}" == 'coordinator' || "${role}" == 'worker' ]] || cluster_die "--role must be coordinator or worker."
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

if [[ "${role}" == 'worker' ]]; then
  systemctl --user --no-pager --full status kevinbellm-rpc-worker.service || result=1
  check_listener 50052 127.0.0.1
else
  systemctl --user --no-pager --full status kevinbellm-rpc-tunnel.service kevinbellm-llama.service || result=1
  check_listener 50053 127.0.0.1
  check_listener 8080 127.0.0.1
  if command -v curl >/dev/null 2>&1; then
    cluster_info "llama-server health response"
    curl --silent --show-error --max-time 5 http://127.0.0.1:8080/health || result=1
    printf '\n'
  fi
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader || result=1
fi
exit "${result}"
