#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

admin_user=""
lan_cidr=""
ssh_port=22
enable_ufw=0
allow_lan_passwords=0

usage() {
  cat <<'EOF'
Usage: sudo harden-ssh.sh --admin-user USER --lan-cidr CIDR [options]

Options:
  --ssh-port PORT        SSH port to protect (default: 22)
  --enable-ufw           Enable UFW and allow SSH only from CIDR
  --allow-lan-passwords  Also accept USER's password from CIDR, which must be
                         a private IPv4 range; other addresses still need a key

The script refuses to disable password login until USER has at least one valid
public key in ~/.ssh/authorized_keys. Keep the current SSH session open while
testing a second login from the laptop. Rerun without --allow-lan-passwords to
make the LAN key-only again.
EOF
}

while (($#)); do
  case "$1" in
    --admin-user)
      (($# >= 2)) || cluster_die "--admin-user requires a value."
      admin_user="$2"
      shift 2
      ;;
    --lan-cidr)
      (($# >= 2)) || cluster_die "--lan-cidr requires a value."
      lan_cidr="$2"
      shift 2
      ;;
    --ssh-port)
      (($# >= 2)) || cluster_die "--ssh-port requires a value."
      ssh_port="$2"
      shift 2
      ;;
    --enable-ufw)
      enable_ufw=1
      shift
      ;;
    --allow-lan-passwords)
      allow_lan_passwords=1
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
cluster_require_root
cluster_require_command sshd
[[ -n "${admin_user}" ]] || cluster_die "--admin-user is required."
[[ -n "${lan_cidr}" ]] || cluster_die "--lan-cidr is required."
[[ "${ssh_port}" =~ ^[0-9]+$ ]] && ((ssh_port >= 1 && ssh_port <= 65535)) || cluster_die "Invalid SSH port."
# The account password also unlocks sudo, so never accept it from outside a
# private home network.
private_lan_cidr='^(10\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])|192\.168)\.[0-9]{1,3}\.[0-9]{1,3}/(1[6-9]|2[0-9]|3[0-2])$'
if ((allow_lan_passwords)) && ! [[ "${lan_cidr}" =~ ${private_lan_cidr} ]]; then
  cluster_die "--allow-lan-passwords needs a private IPv4 --lan-cidr of /16 or narrower, such as 192.168.0.0/24."
fi

admin_home="$(getent passwd "${admin_user}" | cut -d: -f6)"
[[ -n "${admin_home}" && -d "${admin_home}" ]] || cluster_die "User does not exist: ${admin_user}"
authorized_keys="${admin_home}/.ssh/authorized_keys"
[[ -s "${authorized_keys}" ]] || cluster_die "${authorized_keys} is missing or empty. Install the laptop public key first."
grep -Eq '^[[:space:]]*(restrict,|from=|command=|no-|permitopen=|principals=|environment=|cert-authority|[[:alnum:]-]+=)*[[:space:]]*(ssh-ed25519|ecdsa-sha2-|sk-ssh-ed25519|sk-ecdsa-sha2-|ssh-rsa)' "${authorized_keys}" || \
  cluster_die "No recognizable SSH public key found in ${authorized_keys}."

config_dir=/etc/ssh/sshd_config.d
config_file="${config_dir}/00-kevinbellm-hardening.conf"
tmp_config="$(mktemp)"
backup_config="$(mktemp)"
had_previous=0
if [[ -f "${config_file}" ]]; then
  cp -- "${config_file}" "${backup_config}"
  had_previous=1
fi
trap 'rm -f -- "${tmp_config}" "${backup_config}"' EXIT

restore_previous_config() {
  if ((had_previous)); then
    install -o root -g root -m 644 "${backup_config}" "${config_file}"
  else
    rm -f -- "${config_file}"
  fi
}

cat >"${tmp_config}" <<EOF
# Managed by KevinBeLLM scripts/cluster/harden-ssh.sh
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
X11Forwarding no
AllowAgentForwarding no
MaxAuthTries 3
LoginGraceTime 30
EOF
if ((allow_lan_passwords)); then
  # sshd ends a Match block at the end of its included file, so this one
  # cannot capture the rest of /etc/ssh/sshd_config.
  cat >>"${tmp_config}" <<EOF

Match User ${admin_user} Address ${lan_cidr}
    PasswordAuthentication yes
EOF
fi

install -d -m 755 "${config_dir}"
install -o root -g root -m 644 "${tmp_config}" "${config_file}"
if ! sshd -t; then
  restore_previous_config
  cluster_die "sshd rejected the hardening fragment; it was removed."
fi

# OpenSSH uses the first value it obtains. The 00- prefix intentionally wins
# over Ubuntu cloud-init fragments; verify effective settings, not just syntax.
require_effective_settings() {
  local addr="$1"
  local password="$2"
  local effective_config required_setting

  effective_config="$(sshd -T -C "user=${admin_user},host=$(hostname),addr=${addr}")"
  for required_setting in \
    'permitrootlogin no' \
    "passwordauthentication ${password}" \
    'kbdinteractiveauthentication no' \
    'pubkeyauthentication yes'; do
    if ! grep -qxF "${required_setting}" <<<"${effective_config}"; then
      restore_previous_config
      cluster_die "Effective sshd setting for ${addr} was not applied: ${required_setting}"
    fi
  done
}

lan_password=no
if ((allow_lan_passwords)); then
  lan_password=yes
fi
require_effective_settings 127.0.0.1 no
# Match Address counts the network address itself as inside the range.
require_effective_settings "${lan_cidr%/*}" "${lan_password}"

# Clean up only older files carrying this script's exact marker. Never remove
# an administrator-owned fragment merely because its filename looks similar.
for old_managed_config in \
  "${config_dir}/10-kevinbellm-hardening.conf" \
  "${config_dir}/60-kevinbellm-hardening.conf"; do
  if [[ -f "${old_managed_config}" ]] && \
     grep -qxF '# Managed by KevinBeLLM scripts/cluster/harden-ssh.sh' "${old_managed_config}"; then
    rm -f -- "${old_managed_config}"
  fi
done
systemctl reload ssh.service

if ((enable_ufw)); then
  cluster_require_command ufw
  cluster_info "Adding the LAN-scoped SSH firewall policy"
  ufw allow from "${lan_cidr}" to any port "${ssh_port}" proto tcp comment 'KevinBeLLM SSH from home LAN'
  ufw --force enable

  # Never delete administrator-owned firewall rules automatically. Surface the
  # common unsafe case so an older `allow OpenSSH`/`allow 22` rule is not hidden
  # behind the new narrow rule or a default-deny policy.
  ufw_status="$(ufw status verbose)"
  if grep -Ei "^(OpenSSH|${ssh_port}(/tcp)?)([[:space:]]+\(v6\))?[[:space:]]+ALLOW([[:space:]]+IN)?[[:space:]]+Anywhere" <<<"${ufw_status}" >/dev/null; then
    cluster_warn "A pre-existing broad SSH ALLOW rule remains. Review 'sudo ufw status numbered' and remove only that broad rule after proving the LAN-scoped key login."
  fi
  if ! grep -Ei '^Default:[[:space:]]+(deny|reject)[[:space:]]+\(incoming\)' <<<"${ufw_status}" >/dev/null; then
    cluster_warn "UFW's incoming default is not confirmed as deny/reject. Review 'sudo ufw status verbose' before treating SSH as LAN-scoped."
  fi
  printf '%s\n' "${ufw_status}"
fi

cluster_info "SSH hardening installed and sshd configuration validated"
if ((allow_lan_passwords)); then
  cluster_info "${admin_user} can also sign in with a password from ${lan_cidr}; every other address still needs a key"
fi
cluster_warn "Do not close this session until a new key-only SSH login succeeds from the laptop."
