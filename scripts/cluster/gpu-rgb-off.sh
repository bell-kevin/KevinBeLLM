#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

# Pinned OpenRGB release. Ubuntu 24.04 ships no openrgb package and Machine A
# has no FUSE, so the AppImage is extracted rather than mounted.
openrgb_version='1.0rc3.1'
openrgb_url='https://codeberg.org/OpenRGB/OpenRGB/releases/download/release_candidate_1.0rc3.1/OpenRGB_1.0rc3.1_x86_64_5e81e26.AppImage'
openrgb_sha256='42910311b364ae525ca593f53f5fadcf746b4de41e9e49302a5aa5dd614a608a'
readonly openrgb_version openrgb_url openrgb_sha256

# Root-owned locations: the boot unit runs as root, so nothing it executes may
# live in a user-writable directory.
openrgb_root="/opt/openrgb-${openrgb_version}"
openrgb_bin="${openrgb_root}/AppRun"
install_dir='/usr/local/lib/kevinbellm'
unit_name='gpu-rgb-off.service'
unit_path="/etc/systemd/system/${unit_name}"
readonly openrgb_root openrgb_bin install_dir unit_name unit_path

device_match='3070'
persist=1
uninstall=0
attempts=6

usage() {
  cat <<'USAGE'
Usage: gpu-rgb-off.sh [options]

Options:
  --match REGEX   Case-insensitive OpenRGB device-name match (default 3070)
  --once          Turn the lighting off now without installing the boot unit
  --uninstall     Remove the boot unit, its script copy, and the staged OpenRGB

Turns off the RGB lighting on Machine A's Gigabyte GeForce RTX 3070 Gaming OC
(PCI subsystem 1458:404c). The LED controller sits on the GPU's I2C bus, which
only root can reach through the NVIDIA driver, and it reloads its own flash
profile on every power cycle. The default run therefore also installs a root
oneshot unit that re-applies "off" at boot. OpenRGB is fetched once as a
pinned, SHA-256-verified AppImage and staged under /opt.

Run as the login user; sudo is used where needed.
USAGE
}

while (($#)); do
  case "$1" in
    --match)
      (($# >= 2)) || cluster_die "--match requires a value."
      device_match="$2"
      shift 2
      ;;
    --once)
      persist=0
      shift
      ;;
    --uninstall)
      uninstall=1
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
cluster_require_command systemctl

as_root() {
  if ((EUID == 0)); then
    "$@"
  else
    sudo "$@"
  fi
}

openrgb() {
  as_root env QT_QPA_PLATFORM=offscreen "${openrgb_bin}" --noautoconnect "$@"
}

if ((uninstall)); then
  as_root systemctl disable --now "${unit_name}" >/dev/null 2>&1 || true
  as_root rm -f "${unit_path}"
  as_root rm -rf "${install_dir}" "${openrgb_root}"
  as_root systemctl daemon-reload
  cluster_info "Removed ${unit_name}, ${install_dir}, and ${openrgb_root}."
  exit 0
fi

stage_openrgb() {
  local work_dir
  ((EUID != 0)) || cluster_die "OpenRGB is not staged at ${openrgb_bin}. Run this helper as the login user first."
  cluster_require_command curl
  cluster_require_command sha256sum

  work_dir="$(mktemp -d)"
  trap 'rm -rf "${work_dir}"' EXIT

  cluster_info "Downloading OpenRGB ${openrgb_version}"
  curl --fail --location --silent --show-error --max-time 600 \
    --output "${work_dir}/OpenRGB.AppImage" "${openrgb_url}"
  [[ "$(sha256sum "${work_dir}/OpenRGB.AppImage" | awk '{ print $1 }')" == "${openrgb_sha256}" ]] || \
    cluster_die "OpenRGB download does not match the pinned SHA-256; refusing to install it."

  chmod 0755 "${work_dir}/OpenRGB.AppImage"
  (cd "${work_dir}" && ./OpenRGB.AppImage --appimage-extract >/dev/null)
  [[ -x "${work_dir}/squashfs-root/AppRun" ]] || cluster_die "AppImage extraction did not produce AppRun."

  as_root rm -rf "${openrgb_root}"
  as_root install -d -m 0755 "${openrgb_root}"
  as_root cp -a "${work_dir}/squashfs-root/." "${openrgb_root}/"
  as_root chown -R root:root "${openrgb_root}"
  cluster_info "Staged OpenRGB at ${openrgb_root}"
}

# The LED controller is reached through the NVIDIA driver. After Xid 79 ("GPU
# has fallen off the bus") the driver cannot pass I2C traffic to the card, and
# nvidia-smi fails for the whole node until it is rebooted. Check this before
# downloading or asking for sudo.
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -L >/dev/null 2>&1 || cluster_die \
    "nvidia-smi cannot enumerate every GPU. Check 'journalctl -k -b | grep -i xid'; a GPU that fell off the bus needs a reboot before its lighting can be changed."
else
  cluster_warn "nvidia-smi not found; skipping the GPU liveness check."
fi

[[ -x "${openrgb_bin}" ]] || stage_openrgb

as_root modprobe i2c-dev >/dev/null 2>&1 || true

# OpenRGB numbers devices at detection time, so resolve the index on every run.
# At boot the NVIDIA I2C adapters can appear a few seconds after the driver
# loads, hence the short retry loop.
device_index=''
device_name=''
listing=''
for ((attempt = 1; attempt <= attempts; attempt++)); do
  listing="$(openrgb --list-devices 2>/dev/null || true)"
  line="$(grep -iE "^[0-9]+: .*${device_match}" <<<"${listing}" | head -n 1 || true)"
  if [[ -n "${line}" ]]; then
    device_index="${line%%:*}"
    device_name="${line#*: }"
    break
  fi
  ((attempt < attempts)) && sleep 5
done

if [[ -z "${device_index}" ]]; then
  cluster_warn "No OpenRGB device matched /${device_match}/. Devices detected:"
  grep -E '^[0-9]+: ' <<<"${listing}" >&2 || printf '  (none)\n' >&2
  exit 1
fi

# The Gigabyte RGB Fusion 2 GPU driver has no "Off" mode; Direct mode with
# every LED black at zero brightness is the equivalent.
openrgb --device "${device_index}" --mode Direct --color 000000 --brightness 0 >/dev/null
cluster_info "Lighting off: device ${device_index} (${device_name})"

((persist)) || exit 0

unit_tmp="$(mktemp)"
cat >"${unit_tmp}" <<UNIT
[Unit]
Description=Turn off Gigabyte RTX 3070 RGB lighting via OpenRGB
After=systemd-modules-load.service

[Service]
Type=oneshot
ExecStart=${install_dir}/gpu-rgb-off.sh --once --match '${device_match}'

[Install]
WantedBy=multi-user.target
UNIT

as_root install -d -m 0755 "${install_dir}"
as_root install -m 0755 "${script_dir}/gpu-rgb-off.sh" "${script_dir}/common.sh" "${install_dir}/"
as_root install -m 0644 "${unit_tmp}" "${unit_path}"
rm -f "${unit_tmp}"
as_root systemctl daemon-reload
as_root systemctl enable "${unit_name}" >/dev/null
cluster_info "Installed ${unit_name}; it re-applies \"off\" at every boot. Remove with: $0 --uninstall"
