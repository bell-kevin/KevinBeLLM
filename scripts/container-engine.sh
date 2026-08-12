#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
engine_file="${project_dir}/.runtime-engine"

if [[ -n "${CONTAINER_ENGINE:-}" ]]; then
  case "${CONTAINER_ENGINE}" in
    podman|docker) ;;
    *) echo "CONTAINER_ENGINE must be 'podman' or 'docker'." >&2; exit 2 ;;
  esac
  if ! command -v "${CONTAINER_ENGINE}" >/dev/null 2>&1; then
    echo "Requested container engine '${CONTAINER_ENGINE}' is not installed." >&2
    exit 1
  fi
  printf '%s\n' "${CONTAINER_ENGINE}"
elif [[ -f "${engine_file}" ]]; then
  engine="$(tr -d '[:space:]' < "${engine_file}")"
  case "${engine}" in
    podman|docker) ;;
    *) echo "Invalid engine recorded in ${engine_file}." >&2; exit 2 ;;
  esac
  if ! command -v "${engine}" >/dev/null 2>&1; then
    echo "Recorded container engine '${engine}' is not installed." >&2
    exit 1
  fi
  printf '%s\n' "${engine}"
elif command -v podman >/dev/null 2>&1; then
  printf '%s\n' podman
elif command -v docker >/dev/null 2>&1; then
  printf '%s\n' docker
else
  echo "Install rootless Podman (preferred) or Docker with Compose support." >&2
  exit 1
fi
