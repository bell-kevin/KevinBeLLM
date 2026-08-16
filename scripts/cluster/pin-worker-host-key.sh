#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

worker_host=""
expected_fingerprint=""
worker_port=22
known_hosts_file="${HOME}/.ssh/known_hosts.kevinbellm-worker"

usage() {
  cat <<'EOF'
Usage: pin-worker-host-key.sh --host HOST --fingerprint SHA256:... [options]

Options:
  --port PORT              Machine B SSH port (default: 22)
  --known-hosts-file PATH  Dedicated output file

Obtain the ED25519 fingerprint at Machine B's physical console using
show-host-key-fingerprints.sh. ssh-keyscan alone does not establish trust.
EOF
}

while (($#)); do
  case "$1" in
    --host)
      (($# >= 2)) || cluster_die "--host requires a value."
      worker_host="$2"
      shift 2
      ;;
    --fingerprint)
      (($# >= 2)) || cluster_die "--fingerprint requires a value."
      expected_fingerprint="$2"
      shift 2
      ;;
    --port)
      (($# >= 2)) || cluster_die "--port requires a value."
      worker_port="$2"
      shift 2
      ;;
    --known-hosts-file)
      (($# >= 2)) || cluster_die "--known-hosts-file requires a value."
      known_hosts_file="$2"
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
cluster_require_command ssh-keygen
cluster_require_command ssh-keyscan
[[ -n "${worker_host}" ]] || cluster_die "--host is required."
[[ "${expected_fingerprint}" == SHA256:* ]] || cluster_die "Pass the complete SHA256:... fingerprint."
[[ "${worker_port}" =~ ^[0-9]+$ ]] && ((worker_port >= 1 && worker_port <= 65535)) || cluster_die "Invalid SSH port."

tmp_scan="$(mktemp)"
trap 'rm -f -- "${tmp_scan}"' EXIT
ssh-keyscan -T 5 -p "${worker_port}" -t ed25519 -- "${worker_host}" >"${tmp_scan}" 2>/dev/null || true
[[ -s "${tmp_scan}" ]] || cluster_die "No ED25519 host key received from ${worker_host}:${worker_port}."

scanned_fingerprints="$(ssh-keygen -lf "${tmp_scan}" -E sha256 | awk '{ print $2 }' | sort -u)"
[[ "${scanned_fingerprints}" == "${expected_fingerprint}" ]] || cluster_die \
  "HOST KEY MISMATCH: expected ${expected_fingerprint}, received ${scanned_fingerprints}. Do not connect."

install -d -m 700 "$(dirname -- "${known_hosts_file}")"
if [[ -e "${known_hosts_file}" ]]; then
  existing_fingerprints="$(ssh-keygen -lf "${known_hosts_file}" -E sha256 2>/dev/null | awk '{ print $2 }' | sort -u || true)"
  [[ "${existing_fingerprints}" == "${expected_fingerprint}" ]] || cluster_die \
    "${known_hosts_file} already pins a different key. Inspect it manually; this script will not overwrite trust."
  cluster_info "Existing Machine B host-key pin already matches"
else
  install -m 600 "${tmp_scan}" "${known_hosts_file}"
  cluster_info "Pinned Machine B's verified ED25519 host key in ${known_hosts_file}"
fi
