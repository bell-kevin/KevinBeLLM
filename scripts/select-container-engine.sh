#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
engine="${1:-}"

case "${engine}" in
  podman|docker) ;;
  *) echo "Usage: $0 podman|docker" >&2; exit 2 ;;
esac

if ! command -v "${engine}" >/dev/null 2>&1; then
  echo "${engine} is not installed." >&2
  exit 1
fi

if [[ "${engine}" == "podman" ]]; then
  if ! command -v podman-compose >/dev/null 2>&1; then
    echo "podman-compose >= 1.4.1 must be installed before selecting Podman." >&2
    exit 1
  fi
  compose_version="$(podman-compose --version 2>/dev/null | sed -nE 's/.*[^0-9]([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -n 1)"
  minimum_version="1.4.1"
  if [[ -z "${compose_version}" ]] || \
     [[ "$(printf '%s\n' "${minimum_version}" "${compose_version}" | sort -V | head -n 1)" != "${minimum_version}" ]]; then
    echo "podman-compose >= ${minimum_version} is required; found ${compose_version:-an unknown version}." >&2
    exit 1
  fi
fi

umask 077
printf '%s\n' "${engine}" > "${project_dir}/.runtime-engine"
chmod 600 "${project_dir}/.runtime-engine"
echo "KevinBeLLM will use ${engine}."
