#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Checks the optional retrieval path from either end. The client role also
# re-verifies that Machine A's everyday inference boundary is untouched.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

role=""
usage() {
  printf 'Usage: retrieval-status.sh --role server|client\n\n'
  printf '  server  Machine B: embedding, reranking, and retrieval API\n'
  printf '  client  Machine A: the forward, plus the untouched inference boundary\n'
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
[[ "${role}" == 'server' || "${role}" == 'client' ]] || cluster_die "--role must be server or client."
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

probe() {
  local label="$1"
  local url="$2"
  cluster_info "${label}"
  curl --silent --show-error --fail-with-body --max-time 5 "${url}" || result=1
  printf '\n'
}

if [[ "${role}" == 'server' ]]; then
  systemctl --user --no-pager --full status \
    kevinbellm-embedding.service \
    kevinbellm-reranker.service \
    kevinbellm-doc-retrieval.service || result=1
  check_listener 8081 127.0.0.1
  check_listener 8082 127.0.0.1
  check_listener 8091 127.0.0.1
  # Retrieval must never introduce the RPC parser onto Machine B.
  check_no_listener 50052
  if command -v curl >/dev/null 2>&1; then
    probe "embedding server health" http://127.0.0.1:8081/health
    probe "reranking server health" http://127.0.0.1:8082/health
    probe "retrieval API health" http://127.0.0.1:8091/health
  fi
else
  systemctl --user --no-pager --full status kevinbellm-retrieval-tunnel.service || result=1
  check_listener 8091 127.0.0.1

  # The point of this section: confirm the everyday profile is exactly as it
  # was before retrieval was added.
  cluster_info "Re-checking Machine A's standalone inference boundary"
  check_listener 8080 127.0.0.1
  check_no_listener 50052
  check_no_listener 50053
  for rpc_unit in kevinbellm-rpc-tunnel.service kevinbellm-rpc-worker.service; do
    if systemctl --user is-active --quiet "${rpc_unit}"; then
      cluster_warn "${rpc_unit} is active; retrieval must not enable the RPC path."
      result=1
    else
      cluster_info "Verified ${rpc_unit} is inactive"
    fi
  done
  llama_arguments="$(systemctl --user show kevinbellm-llama.service -p ExecStart --value 2>/dev/null || true)"
  if [[ "${llama_arguments}" == *--rpc* ]]; then
    cluster_warn "kevinbellm-llama.service now carries an --rpc argument."
    result=1
  else
    cluster_info "Verified the everyday inference unit still has no --rpc argument"
  fi

  if command -v curl >/dev/null 2>&1; then
    probe "retrieval API health through the forward" http://127.0.0.1:8091/health
    probe "everyday llama-server health" http://127.0.0.1:8080/health
  fi
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader || result=1
fi
exit "${result}"
