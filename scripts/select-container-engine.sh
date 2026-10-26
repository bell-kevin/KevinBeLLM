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
  podman_compose_bin="$(command -v podman-compose 2>/dev/null || true)"
  if [[ -z "${podman_compose_bin}" && -x "${HOME}/.local/bin/podman-compose" ]]; then
    podman_compose_bin="${HOME}/.local/bin/podman-compose"
  fi
  if [[ -z "${podman_compose_bin}" ]]; then
    echo "podman-compose >= 1.4.1 must be installed before selecting Podman." >&2
    exit 1
  fi
  # Match only the podman-compose line (current releases print Podman's version
  # first), and let a broken install reach the error below instead of errexit.
  compose_version="$("${podman_compose_bin}" --version 2>/dev/null | sed -nE 's/^podman-compose( version)?:? +v?([0-9]+\.[0-9]+\.[0-9]+).*/\2/p' | head -n 1 || true)"
  minimum_version="1.4.1"
  if [[ -z "${compose_version}" ]]; then
    echo "${podman_compose_bin} --version failed or printed no version." >&2
    echo "A pipx install breaks when a release upgrade removes its Python; rebuild it with: pipx reinstall-all" >&2
    exit 1
  fi
  if [[ "$(printf '%s\n' "${minimum_version}" "${compose_version}" | sort -V | head -n 1)" != "${minimum_version}" ]]; then
    echo "podman-compose >= ${minimum_version} is required; found ${compose_version}." >&2
    exit 1
  fi
fi

umask 077
printf '%s\n' "${engine}" > "${project_dir}/.runtime-engine"
chmod 600 "${project_dir}/.runtime-engine"
echo "KevinBeLLM will use ${engine}."
