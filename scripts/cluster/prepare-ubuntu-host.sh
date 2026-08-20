#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

host_name=""
install_ubuntu_cuda=0

usage() {
  cat <<'EOF'
Usage: prepare-ubuntu-host.sh [--hostname NAME] [--with-ubuntu-cuda]

Installs the common build/SSH packages on Machine A. The optional
Ubuntu CUDA package is convenient but may lag NVIDIA's supported toolkit; omit
it when nvcc is already installed from NVIDIA's repository.
EOF
}

while (($#)); do
  case "$1" in
    --hostname)
      (($# >= 2)) || cluster_die "--hostname requires a value."
      host_name="$2"
      shift 2
      ;;
    --with-ubuntu-cuda)
      install_ubuntu_cuda=1
      shift
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

cluster_require_ubuntu
cluster_require_non_root
cluster_require_command sudo

if [[ -n "${host_name}" ]]; then
  [[ "${host_name}" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}$ ]] || cluster_die "Invalid hostname: ${host_name}"
  if [[ "$(hostname)" != "${host_name}" ]]; then
    cluster_info "Setting hostname to ${host_name}"
    sudo hostnamectl set-hostname "${host_name}"
  fi

  # Ubuntu desktop installs commonly map the machine hostname on 127.0.1.1.
  # hostnamectl updates /etc/hostname but not this entry, which otherwise leaves
  # sudo and local service logs warning that the newly named host cannot resolve.
  hosts_entry_count="$(awk '$1 == "127.0.1.1" { count++ } END { print count + 0 }' /etc/hosts)"
  if [[ "${hosts_entry_count}" == '1' ]]; then
    hosts_tmp="$(mktemp)"
    awk -v new_hostname="${host_name}" '
      $1 == "127.0.1.1" { $2 = new_hostname }
      { print }
    ' /etc/hosts >"${hosts_tmp}"
    if ! cmp -s -- "${hosts_tmp}" /etc/hosts; then
      cluster_info "Updating the 127.0.1.1 hostname entry in /etc/hosts"
      sudo install -o root -g root -m 644 "${hosts_tmp}" /etc/hosts
    fi
    rm -f -- "${hosts_tmp}"
  elif [[ "${hosts_entry_count}" != '0' ]]; then
    cluster_die "Multiple 127.0.1.1 entries exist in /etc/hosts; inspect them before renaming this host."
  else
    cluster_warn "/etc/hosts has no 127.0.1.1 entry; leaving it unchanged."
  fi
fi

packages=(
  build-essential
  ca-certificates
  cmake
  curl
  git
  iproute2
  ninja-build
  openssh-client
  openssh-server
  pkg-config
  python3-minimal
  ufw
)
if ((install_ubuntu_cuda)); then
  packages+=(nvidia-cuda-toolkit)
fi

cluster_info "Installing Ubuntu prerequisites"
sudo apt-get update
sudo apt-get install -y --no-install-recommends "${packages[@]}"
sudo systemctl enable --now ssh.service

install -d -m 700 \
  "${HOME}/.cache/kevinbellm-cluster" \
  "${HOME}/.cache/llama.cpp" \
  "${HOME}/.config/kevinbellm-cluster" \
  "${HOME}/.local/opt" \
  "${HOME}/models"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  cluster_warn "nvidia-smi is missing. Install a current NVIDIA driver before building llama.cpp."
elif ! nvidia-smi >/dev/null 2>&1; then
  cluster_warn "The NVIDIA driver is installed but no working GPU was detected."
else
  cluster_info "NVIDIA driver sees the GPU"
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
fi

if ! command -v nvcc >/dev/null 2>&1; then
  cluster_warn "nvcc is missing. Install a CUDA toolkit, or rerun with --with-ubuntu-cuda."
else
  cluster_info "CUDA compiler: $(nvcc --version | tail -n 1)"
fi

cat <<'EOF'

Base preparation is complete. Before disabling SSH passwords, install and test
the laptop's public key, then run harden-ssh.sh with the correct home-LAN CIDR.
EOF
