#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

# Shared, deliberately small helpers for the Ubuntu cluster scripts.

cluster_info() {
  printf '==> %s\n' "$*"
}

cluster_warn() {
  printf 'WARNING: %s\n' "$*" >&2
}

cluster_die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cluster_require_command() {
  command -v "$1" >/dev/null 2>&1 || cluster_die "Required command not found: $1"
}

cluster_require_ubuntu() {
  [[ "$(uname -s)" == "Linux" ]] || cluster_die "This helper must run on Linux."
  [[ -r /etc/os-release ]] || cluster_die "Cannot identify this Linux distribution."

  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || cluster_die "This helper supports Ubuntu only (found ${ID:-unknown})."
}

cluster_require_non_root() {
  [[ "${EUID}" -ne 0 ]] || cluster_die "Run this helper as the target login user, not as root. It will use sudo where needed."
}

cluster_require_root() {
  [[ "${EUID}" -eq 0 ]] || cluster_die "Run this helper through sudo."
}

cluster_env_value() {
  local env_file="$1"
  local key="$2"
  local line value

  [[ -f "${env_file}" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}=" "${env_file}" | tail -n 1 || true)"
  [[ -n "${line}" ]] || return 1
  value="${line#*=}"
  value="${value%$'\r'}"

  # Support the simple quoting accepted by systemd EnvironmentFile. Deliberately
  # do not source private env files; a typo must not become shell execution.
  if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "${value}"
}

cluster_sed_escape_replacement() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}
