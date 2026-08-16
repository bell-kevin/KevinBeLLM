#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
engine="$("${project_dir}/scripts/container-engine.sh")"
args=()

while (( "$#" )); do
  if [[ "$1" == "-f" || "$1" == "--file" ]]; then
    if (( "$#" < 2 )); then
      echo "$1 requires a Compose file path." >&2
      exit 2
    fi
    compose_file="$2"
    args+=("$1" "${compose_file}")
    shift 2
    if [[ "${engine}" == "podman" ]]; then
      podman_override="$(dirname -- "${compose_file}")/compose.podman.yaml"
      if [[ -f "${podman_override}" ]]; then
        args+=(-f "${podman_override}")
      fi
    fi
  else
    args+=("$1")
    shift
  fi
done

if [[ "${engine}" == "podman" ]]; then
  podman_compose_bin="$(command -v podman-compose 2>/dev/null || true)"
  if [[ -z "${podman_compose_bin}" && -x "${HOME}/.local/bin/podman-compose" ]]; then
    # pipx installs here by default, but non-interactive SSH and systemd user
    # managers do not necessarily include ~/.local/bin in PATH.
    podman_compose_bin="${HOME}/.local/bin/podman-compose"
  fi
  if [[ -z "${podman_compose_bin}" ]]; then
    echo "Podman is installed, but podman-compose is unavailable." >&2
    echo "Install podman-compose >= 1.4.1; implicit providers are not used." >&2
    exit 1
  fi

  compose_version="$("${podman_compose_bin}" --version 2>/dev/null | sed -nE 's/.*[^0-9]([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -n 1)"
  minimum_version="1.4.1"
  if [[ -z "${compose_version}" ]] || \
     [[ "$(printf '%s\n' "${minimum_version}" "${compose_version}" | sort -V | head -n 1)" != "${minimum_version}" ]]; then
    echo "podman-compose >= ${minimum_version} is required; found ${compose_version:-an unknown version}." >&2
    echo "Ubuntu 24.04's 1.0.6 package does not enforce service health dependencies." >&2
    exit 1
  fi

  # Podman 4.9 rejects keep-id user namespaces inside a Compose-created pod.
  # These stacks use explicit loopback/host networking, so a shared pod is
  # unnecessary and must remain disabled.
  exec "${podman_compose_bin}" --in-pod=false "${args[@]}"
fi

exec docker compose "${args[@]}"
