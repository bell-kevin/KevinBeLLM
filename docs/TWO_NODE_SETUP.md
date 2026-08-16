# Two-node KevinBeLLM setup

This guide turns the two Ethernet-connected desktops into one private inference
service while keeping the Windows laptop as the control console. It is written
for Ubuntu hosts with NVIDIA drivers and passphrase-backed encrypted disks. Do
not run the mutating steps until the inventory section confirms that assumption
on both machines.

The recommended first build is:

```text
Windows laptop (browser + SSH client; may sleep or disconnect)
    |
    | SSH, home LAN/Wi-Fi
    v
Machine A / kevinbellm-a / coordinator / RTX 3060 12 GiB
    - KevinBeLLM UI                 127.0.0.1:3000
    - llama-server                  127.0.0.1:8080
    - local end of worker tunnel    127.0.0.1:50053
    |
    | authenticated SSH tunnel over wired Ethernet
    v
Machine B / kevinbellm-b / worker / RTX 3070 8 GiB
    - ggml-rpc-server               127.0.0.1:50052
```

Machine A, not the laptop, is the coordinator. It owns the model file, the
inference HTTP process, the application, and one of the GPUs. The A-to-B data
path stays on Ethernet, and closing the laptop or its UI tunnel does not stop
inference. Making the laptop the coordinator would add Wi-Fi and laptop uptime
to every request and would make a sleeping laptop take the whole service down.

Machine A is the sensible desktop coordinator because its 12 GiB card gives
the local server more headroom; Machine B is a narrowly scoped 8 GiB worker.
Both Ampere cards support the same pinned source revision and CUDA build
configuration: NVIDIA lists the RTX 3060 and RTX 3070 as compute capability
8.6. The cards are still separate devices, not a
single 20 GiB CUDA allocation. llama.cpp places tensors and KV cache across
local and RPC devices; it does not turn their bandwidth into one 400 GB/s memory
bus. See the official [NVIDIA compute-capability table][nvidia-cc], [EVGA 3060
specification][evga-3060], and [Gigabyte 3070 specification][gigabyte-3070].

The reported starting inventory is below. Confirm the exact product/revision
from each card label and `nvidia-smi` before relying on it.

| | Machine A | Machine B |
|---|---|---|
| Card | EVGA RTX 3060 XC Gaming, `12G-P5-3657-KR` | Gigabyte RTX 3070 Gaming OC 8G, `GV-N3070GAMING OC-8GD` |
| GPU | GA106, 3,584 CUDA cores, Ampere/sm_86 | GA104, 5,888 CUDA cores, Ampere/sm_86 |
| VRAM | 12 GiB GDDR6, 192-bit | 8 GiB GDDR6, 256-bit |
| Local memory bandwidth | 360 GB/s | 448 GB/s |
| Board power/connectors | about 170 W, one PCIe 8-pin | about 220 W, one 8-pin plus one 6-pin |
| Physical card | 201.8 mm, dual-slot | 286 x 115 x 51 mm, three fans |
| System RAM | 32 GiB DDR3 | 32 GiB DDR3 |

Equal system-RAM capacity means RAM does not choose the coordinator. The 3060's
larger VRAM does; the 3070 contributes greater compute and local bandwidth. The
360 and 448 GB/s figures describe separate local memory buses and must not be
added when estimating two-node bandwidth.

## What can and cannot happen remotely

There is one unavoidable physical bootstrap. The desktops' identities and
reserved addresses are not yet confirmed, and the laptop has no verified SSH
trust relationship with either one. First attach a monitor and keyboard to each
desktop, power it on, unlock it, and log in.

Passphrase-backed full-disk encryption is intentionally earlier than the normal
network and SSH services. Until the LUKS volume is unlocked, the regular root
filesystem, `sshd`, containers, model server, and these systemd units cannot
start. Ubuntu's FDE documentation describes that passphrase-at-boot flow
directly ([Ubuntu FDE][ubuntu-fde]). Therefore:

- physically pressing the power button is enough to start a machine, but not
  enough to make it reachable;
- type the disk passphrase locally after every cold boot, then normal boot and
  service autostart can finish without a desktop login;
- Wake-on-LAN and BIOS "restore after AC loss" can power a computer but cannot
  type a LUKS passphrase;
- do not add an unencrypted key file, TPM auto-unlock, or an initramfs SSH
  server as a shortcut during this project. Those are separate security-design
  decisions, not SSH configuration tweaks.

If later the requirement changes to unattended recovery after a power outage,
evaluate TPM-bound unlock or a deliberately engineered initramfs remote-unlock
path separately, with backups and a recovery-key drill. Do not weaken the
current encryption merely to save one physical unlock.

Once both disks are unlocked, user services can start at boot without a GUI
login if lingering is enabled. `scripts/cluster/install-services.sh` installs
the cluster units under `~/.config/systemd/user/` and runs
`sudo loginctl enable-linger <linux-user>`; lingering creates that user's service
manager at boot and keeps it after logout ([systemd `loginctl`][loginctl]). The
existing KevinBeLLM UI autostart uses the same mechanism.

## Stage 0: record facts before changing anything

Use the physical console on **each** desktop. Label the output A and B. Commands
that only inspect the system are safe to run now:

```bash
hostnamectl
cat /etc/os-release
uname -a
lscpu
free -h
lsblk -o NAME,MODEL,SIZE,TYPE,FSTYPE,MOUNTPOINTS
findmnt -no SOURCE,FSTYPE,OPTIONS /
sudo dmsetup ls --target crypt
sudo cat /etc/crypttab
nvidia-smi --query-gpu=index,name,uuid,memory.total,driver_version --format=csv
nvidia-smi topo -m
lspci -nnk | grep -A3 -E 'VGA|3D|Ethernet'
ip -brief link
ip -brief -4 address
ip route
```

`/etc/crypttab` normally contains device UUIDs, not a passphrase, but do not
publish the inventory output blindly. Confirm that the source backing `/` is a
device-mapper/LUKS mapping. If the machines use BitLocker, VeraCrypt, ZFS native
encryption, Windows, or another OS, stop: the boot and service instructions in
this guide do not apply unchanged.

Identify the wired interface from `ip -brief link`, then record its permanent
MAC address and negotiated link:

```bash
cat /sys/class/net/<wired-interface>/address
sudo ethtool <wired-interface>
```

The desired result is `Speed: 1000Mb/s`, `Duplex: Full`, and `Link detected:
yes` on both. Also inspect and write down information software cannot reliably
discover:

- motherboard model and available full-length PCIe slots;
- case clearance and slot spacing;
- the PSU label, total wattage, model, PCIe leads, and connector layout;
- which card is physically installed in which machine;
- a working local login, sudo access, and the disk-recovery information.

Take a current backup before package, driver, boot, or hardware work. This
project does not require repartitioning or changing encryption.

Set clear hostnames while still at the consoles:

```bash
# Run on Machine A only
sudo hostnamectl set-hostname kevinbellm-a

# Run on Machine B only
sudo hostnamectl set-hostname kevinbellm-b
```

Re-open a shell so the prompt reflects the new name. Hostnames are labels, not
address guarantees; use router reservations for stable addresses.

Copy `infra/cluster/inventory.example.env` to a private, ignored
`infra/cluster/inventory.env` on the administration checkout and fill it from
the recorded facts. Never guess the username, interface, LAN subnet, IP, or
model path. The examples under `infra/cluster/` are templates, not active
configuration.

### Reserve addresses in the router

Open the home router's administration page and find its DHCP/client list. Match
the two *wired* MAC addresses recorded above, then make DHCP reservations. A
typical plan might be `192.168.1.20` for A and `192.168.1.21` for B, but use
addresses valid for this router and not assigned to another client. Reserving
the laptop's Wi-Fi address also permits a narrower firewall rule later.

Do not configure both a manual static address on Ubuntu and a DHCP reservation.
Prefer reservations: the router remains the source of truth for gateway, DNS,
and collision avoidance. Do not add any router port-forwarding rule.

After renewing the leases from the physical console or rebooting and unlocking,
confirm the chosen addresses with `ip -brief -4 address`. From PowerShell on the
laptop, confirm reachability:

```powershell
ping.exe -4 <machine-a-reserved-ip>
ping.exe -4 <machine-b-reserved-ip>
Get-NetNeighbor -AddressFamily IPv4 | Format-Table IPAddress,LinkLayerAddress,State
```

Some hosts or firewalls ignore ping. The router lease table plus a successful
SSH connection is the decisive test.

## Stage 1: establish SSH without trusting the network blindly

Ubuntu documents both the OpenSSH server installation and Ed25519 key setup in
its [OpenSSH guide][ubuntu-ssh]. Do this at each physical console first:

```bash
sudo apt update
sudo apt install openssh-server
sudo systemctl enable --now ssh.service
sudo systemctl status ssh.service --no-pager
```

Print the server host-key fingerprints on each physical screen:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

Photograph or transcribe the `SHA256:...` value for A and B. A first-connect
prompt on the laptop is trustworthy only after it matches the value observed at
that machine's console. `ssh-keyscan` alone does **not** authenticate a host;
the project uses it only after an independently obtained fingerprint is pinned.

### Create one laptop key and install it on both hosts

From PowerShell in this repository, use the Windows bootstrap helper after
copying and editing its private inventory:

```powershell
Copy-Item .\infra\cluster\inventory.example.env .\infra\cluster\inventory.env
notepad.exe .\infra\cluster\inventory.env
Get-Help .\scripts\windows\Install-KevinBeLLMSSH.ps1 -Detailed
.\scripts\windows\Install-KevinBeLLMSSH.ps1 -GenerateKey -InstallPublicKey
```

The helper creates a passphrase-protected admin key, adds managed
`kevinbellm-a`/`kevinbellm-b` aliases to the laptop's SSH config, and offers
only the public key to each host. Compare each first-connect fingerprint to the
one recorded at that host's console before accepting it.

If doing the same operation manually, create a dedicated, passphrase-protected
key. Never copy the private key to either desktop:

```powershell
ssh-keygen.exe -t ed25519 -a 100 -f "$env:USERPROFILE\.ssh\id_ed25519_kevinbellm_admin" -C "kevinbellm-laptop-admin"
```

Install only the `.pub` line on each server. The first connection may use the
existing Linux password; compare the displayed host fingerprint before
answering `yes`:

```powershell
Get-Content "$env:USERPROFILE\.ssh\id_ed25519_kevinbellm_admin.pub" |
  ssh.exe <linux-user>@<machine-a-reserved-ip> 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys'

Get-Content "$env:USERPROFILE\.ssh\id_ed25519_kevinbellm_admin.pub" |
  ssh.exe <linux-user>@<machine-b-reserved-ip> 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys'
```

On each host, make permissions unambiguous:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

Create `%USERPROFILE%\.ssh\config` entries on the laptop. Replace values, and
keep `HostName` as the reserved IP even if `.local` name resolution works:

```sshconfig
Host kevinbellm-a
    HostName <machine-a-reserved-ip>
    User <linux-user>
    IdentityFile ~/.ssh/id_ed25519_kevinbellm_admin
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3

Host kevinbellm-b
    HostName <machine-b-reserved-ip>
    User <linux-user>
    IdentityFile ~/.ssh/id_ed25519_kevinbellm_admin
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

Open a **new** PowerShell window and prove key-only access to each:

```powershell
ssh.exe -o PasswordAuthentication=no kevinbellm-a hostnamectl --static
ssh.exe -o PasswordAuthentication=no kevinbellm-b hostnamectl --static
```

Now place this completed, reviewed project checkout on A and B. Prefer to
commit the intended files to a controlled branch, clone that branch on both
hosts, and check out the same commit; a trusted file copy is acceptable if the
repository is not being published yet. Do not transfer `.env` files,
`infra/cluster/inventory.env`, private keys, tokens, downloaded model blobs, or
other ignored secrets. From each checkout, record `git rev-parse HEAD` (when
using Git) and require the values to match. All repository-script commands
below assume the checkout is already present and is the version you reviewed.
On each host, `./scripts/cluster/show-host-key-fingerprints.sh` can now reprint
the available host fingerprints; its Ed25519 value must match the one recorded
manually at that host's console.

If the reviewed checkout was copied from Windows rather than cloned with Git,
restore Linux executable bits once on each host and verify a representative
entry point before continuing:

```bash
chmod +x scripts/*.sh scripts/cluster/*.sh
test -x scripts/cluster/prepare-ubuntu-host.sh
```

Keep the original console/session open while hardening. On each host, review and
then run:

```bash
./scripts/cluster/harden-ssh.sh --help
sudo ./scripts/cluster/harden-ssh.sh \
  --admin-user <linux-user> \
  --lan-cidr <home-lan-cidr> \
  --enable-ufw
sudo sshd -t
```

Do not disable password authentication until the two key-only tests above pass.
After any SSH configuration change, validate with `sshd -t`, reload, and test a
second session before closing the first. Ubuntu explicitly warns that an SSH
configuration error can lock out a remote administrator ([Ubuntu OpenSSH
guide][ubuntu-ssh]). Never allow direct root login.

The helper's optional `--enable-ufw` permits the supplied LAN CIDR to TCP 22 and
adds an explicit deny for TCP 50052. Use the narrowest CIDR that includes the
laptop and, on B, Machine A; if those addresses cannot all be reserved,
allowing the home LAN subnet is a reasonable recovery tradeoff. Canonical
documents host/subnet-scoped rules in its [UFW guide][ubuntu-ufw]. No allow
rule is needed for ports 3000, 8080, 11434, 50052, or 50053 because those remain
loopback-only.

The helper never deletes administrator-owned firewall rules. It prints the
resulting verbose status and warns when it recognizes either a pre-existing
broad `OpenSSH`/port-22 `ALLOW Anywhere` rule or an incoming default it cannot
confirm as deny/reject. After the LAN-scoped key login works in a second
terminal, review `sudo ufw status verbose` and `sudo ufw status numbered`, then
remove only a broad rule you have positively identified; do not close the
original session first.

## Stage 2: prepare matching llama.cpp CUDA builds

Use the same revision on both hosts. This project pins llama.cpp build tag
`b10451`, currently commit `10bf611e533d81f739128304991c5e133c6aebd8`.
Pinning avoids an RPC protocol or model-support mismatch after one host updates.
Do not build one host from `master` and the other from the tag.

From the matching reviewed checkouts placed in Stage 1, run on each host:

```bash
cd <path-to-kevinBeLLMbetterHardware>
./scripts/cluster/prepare-ubuntu-host.sh --help
./scripts/cluster/prepare-ubuntu-host.sh
nvidia-smi
nvcc --version
```

Do not replace a working NVIDIA driver casually. `nvidia-smi` must work before
building; `nvcc --version` must show a CUDA toolkit supported by that driver.
The helper installs build and SSH prerequisites but does not replace a driver.
If `nvcc` is absent and Ubuntu's packaged toolkit is appropriate for the
installed driver, rerun it with `--with-ubuntu-cuda`; otherwise install a
driver-compatible toolkit from NVIDIA, then re-run the checks. The build helper
will refuse to continue without a working GPU and CUDA 11 or newer.

Then build on **both** hosts:

```bash
./scripts/cluster/install-llama-cpp.sh --help
./scripts/cluster/install-llama-cpp.sh
```

The essential upstream build settings are CUDA plus RPC:

```bash
cmake -S <llama.cpp-source> -B <build-dir> \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_CUDA=ON \
  -DGGML_RPC=ON \
  -DGGML_NATIVE=OFF \
  -DGGML_AVX=OFF \
  -DGGML_AVX2=OFF \
  -DGGML_BMI2=OFF \
  -DGGML_FMA=OFF \
  -DGGML_F16C=OFF \
  -DLLAMA_BUILD_UI=OFF \
  -DLLAMA_USE_PREBUILT_UI=OFF \
  -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build <build-dir> --config Release -j
```

Those flags follow the pinned [llama.cpp CUDA/RPC build instructions][llama-rpc].
The explicit CPU feature disables keep one SSE4.2-compatible baseline runnable
on these pre-AVX/AVX2 CPUs; the performance-critical model work remains on
CUDA. At `b10451` the worker binary is named `ggml-rpc-server`, not
`rpc-server`. Build separately on each host rather than copying a host-native
binary between possibly different CPUs.
Because the builds are compiled separately, their executable hashes and sizes
need not be byte-for-byte identical. The compatibility requirements are the
same pinned source commit and the same recorded build options on both hosts.
Both UI flags are deliberate. At this revision, disabling the UI build alone
still allows a prebuilt UI download; KevinBeLLM uses the server API and does
not need those unpinned browser assets.

Verify both installations before continuing:

```bash
~/.local/opt/llama.cpp-b10451/build/bin/llama-server --version
~/.local/opt/llama.cpp-b10451/build/bin/llama-bench --list-devices
~/.local/opt/llama.cpp-b10451/build/bin/ggml-rpc-server --help
grep '^commit=' ~/.local/opt/llama.cpp-b10451/KEVINBELLM_BUILD_SPEC.txt
```

The device list on A must include the RTX 3060 and B must include the RTX 3070.
Record the reported llama.cpp commit on both and require an exact match.

## Stage 3: bring up the worker through SSH

This is the non-negotiable security rule:

> Never expose the llama.cpp RPC port to the LAN, the router, or the internet.

The upstream project calls the RPC backend proof-of-concept, fragile, and
insecure and says never to run it on an open network or sensitive environment
([pinned RPC warning][llama-rpc-warning]). It has no authentication boundary
appropriate for a LAN. In this design B binds RPC only to `127.0.0.1`; an
authenticated, host-key-pinned SSH tunnel carries it to A. SSH reduces network
exposure, but it does not make bugs in the RPC implementation impossible.

This is not a theoretical caveat. A critical unauthenticated RCE was disclosed
as [CVE-2026-34159 / GHSA-j8rj-fmpv-wcxw][rpc-rce]. Its stated affected range
ends at b7991, earlier than this pinned b10451, but a separate [controlled
indirect-call report against current code][rpc-current-report] remains open as
of this guide. A version pin gives reproducibility; it is not a claim that the
RPC parser is safe.

Operationally, treat access to either RPC loopback socket as equivalent to code
execution on B. A process on B can reach `127.0.0.1:50052`; while the tunnel is
up, a process under A's local security boundary can reach
`127.0.0.1:50053`. Use this only on trusted, single-owner machines, keep both
hosts patched, and do not run untrusted local workloads beside it. The service
installer deliberately requires both the `--acknowledge-rpc-risk` flag and this
exact private-environment value before it will run anything:

```dotenv
ACKNOWLEDGE_LLAMA_RPC_RCE=YES_I_ACCEPT_UNAUTHENTICATED_RCE_RISK
```

### Manual proof before autostart

On B, run the worker in a terminal for the first test:

```bash
~/.local/opt/llama.cpp-b10451/build/bin/ggml-rpc-server \
  --host 127.0.0.1 \
  --port 50052 \
  --device CUDA0 \
  --cache
```

In a second B shell, verify the binding. The local address must be
`127.0.0.1:50052`, never `0.0.0.0:50052` or `[::]:50052`:

```bash
ss -ltnp | grep 50052
```

The pinned worker defaults to loopback and supports `--cache`; the cache avoids
re-sending large tensors on later loads ([RPC cache documentation][llama-rpc]).
Its first population can still be slow. Cached tensors live on B's encrypted
disk and become accessible after that disk is unlocked.

On A, create a separate key used only for the service tunnel and pin B's
verified host key:

```bash
./scripts/cluster/generate-tunnel-key.sh --help
./scripts/cluster/generate-tunnel-key.sh
./scripts/cluster/pin-worker-host-key.sh --help
./scripts/cluster/pin-worker-host-key.sh \
  --host <machine-b-reserved-ip> \
  --fingerprint 'SHA256:<fingerprint-read-at-b-console>'
```

Transfer only A's tunnel **public** key to B using the already trusted admin
connection:

```bash
# Run on A; replace the B admin user and reserved address.
scp ~/.ssh/kevinbellm_rpc_tunnel_ed25519.pub \
  <machine-b-admin-user>@<machine-b-reserved-ip>:/tmp/kevinbellm_rpc_tunnel_ed25519.pub
```

Then run on B:

```bash
sudo ./scripts/cluster/install-worker-tunnel-key.sh \
  --public-key-file /tmp/kevinbellm_rpc_tunnel_ed25519.pub \
  --from <machine-a-reserved-ip>
rm /tmp/kevinbellm_rpc_tunnel_ed25519.pub
```

The B helper creates the dedicated no-shell user `llama-rpc-tunnel` and
restricts the key by source address and `permitopen` to only
`127.0.0.1:50052`; OpenSSH documents these authorized-key restrictions in
[`sshd(8)`][openssh-sshd]. The normal laptop administration key remains
separate. The private tunnel key never leaves A.

Test from A in the foreground:

```bash
ssh -NT \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$HOME/.ssh/known_hosts.kevinbellm-worker" \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -i "$HOME/.ssh/kevinbellm_rpc_tunnel_ed25519" \
  -L 127.0.0.1:50053:127.0.0.1:50052 \
  llama-rpc-tunnel@<machine-b-reserved-ip>
```

While it is running, `ss -ltnp | grep 50053` on A should show a loopback SSH
listener. Stopping this SSH command must remove A's port 50053 listener. B's
50052 listener remains inaccessible from the LAN. Using different local and
remote port numbers also makes accidental direct-RPC configuration easier to
spot. Press Ctrl+C in both foreground tests before installing services.

### Configure and install the persistent units

The active role files live outside the repository under
`~/.config/kevinbellm-cluster/`. Install the worker file on B:

```bash
install -d -m 700 ~/.config/kevinbellm-cluster
install -m 600 infra/cluster/worker.example.env \
  ~/.config/kevinbellm-cluster/worker.env
nano ~/.config/kevinbellm-cluster/worker.env
```

Set the exact RPC acknowledgment shown above. Leave
`CUDA_VISIBLE_DEVICES=0`. Then install, enable, start, and verify B's user unit:

```bash
./scripts/cluster/install-services.sh \
  --role worker \
  --acknowledge-rpc-risk \
  --enable-now
./scripts/cluster/cluster-status.sh --role worker
systemctl --user is-enabled kevinbellm-rpc-worker.service
```

On A, download the reviewed model with the resumable, checksum-verifying helper
before enabling the coordinator:

```bash
df -h "$HOME/models"
./scripts/cluster/download-model.sh --help
./scripts/cluster/download-model.sh
```

The default output is
`$HOME/models/Qwen_Qwen3.5-27B-Q4_K_M.gguf`. Keep at least several GiB beyond
the 17.98 GB file free on A; B also needs free space if the RPC tensor cache is
enabled.

Now install and edit A's private file:

```bash
install -d -m 700 ~/.config/kevinbellm-cluster
install -m 600 infra/cluster/coordinator.example.env \
  ~/.config/kevinbellm-cluster/coordinator.env
nano ~/.config/kevinbellm-cluster/coordinator.env
```

Set these values and leave the remaining conservative defaults in place:

```dotenv
ACKNOWLEDGE_LLAMA_RPC_RCE=YES_I_ACCEPT_UNAUTHENTICATED_RCE_RISK
WORKER_SSH_TARGET=llama-rpc-tunnel@<machine-b-reserved-ip>
WORKER_SSH_PORT=22
MODEL_PATH=/home/<linux-user>/models/Qwen_Qwen3.5-27B-Q4_K_M.gguf
LLAMA_MODEL_ALIAS=kevinbellm-27b
LLAMA_ARG_CTX_SIZE=4096
LLAMA_ARG_N_PARALLEL=1
LLAMA_ARG_SPLIT_MODE=layer
LLAMA_ARG_N_GPU_LAYERS=auto
LLAMA_ARG_FIT=on
LLAMA_ARG_JINJA=true
LLAMA_ARG_CACHE_TYPE_K=q8_0
LLAMA_ARG_CACHE_TYPE_V=q8_0
```

The checked-in example defaults to 8192 context; 4096 is the safer first-load
value for proving headroom. Raise it to 8192 only after the 4K configuration is
stable and measured. Do not paste a disk passphrase, SSH private key, or
model-site token into either environment file.

Review the templates before installation:

- `systemd/cluster/llama-rpc-worker.service.in` runs on B;
- `systemd/cluster/llama-rpc-tunnel.service.in` runs on A;
- `systemd/cluster/llama-server.service.in` runs on A after the tunnel;
- `scripts/cluster/install-services.sh` renders and installs them;
- `scripts/cluster/cluster-status.sh` checks the resulting chain.

Install A's coordinator role:

```bash
./scripts/cluster/install-services.sh --help
./scripts/cluster/install-services.sh \
  --role coordinator \
  --acknowledge-rpc-risk \
  --enable-now
./scripts/cluster/cluster-status.sh --role coordinator
```

The installer places systemd **user** units, enables lingering, and uses these
exact names. Inspect them with `--user`:

```bash
systemctl --user status \
  kevinbellm-rpc-tunnel.service \
  kevinbellm-llama.service
systemctl --user is-enabled \
  kevinbellm-rpc-tunnel.service \
  kevinbellm-llama.service
```

The tunnel unit does not declare itself ready merely because SSH opened local
port 50053. It repeatedly runs the pinned `llama-bench` RPC device-discovery
handshake and waits for an `RPC0` device from B before systemd starts
`llama-server`. The tunnel retries while B is still booting; if a later worker
outage makes the worker or `llama-server` exit, their units also retry without a
finite burst limit. Powering A a little early therefore does not permanently
strand the coordinator.

On B, re-check that RPC is loopback-only. On A, re-check that the forwarded RPC
port and llama HTTP port are loopback-only:

```bash
# On B
ss -ltnp | grep -E ':50052\b'

# On A
ss -ltnp | grep -E ':(50053|8080)\b'
curl --fail http://127.0.0.1:8080/health
```

The pinned llama server's default bind is `127.0.0.1`; keep it explicit. It
registers the remote device with `--rpc 127.0.0.1:50053`, starts with
`--n-gpu-layers auto` and `--fit on`, and can be tuned later with
`--tensor-split`. The upstream server options are documented in the [pinned
server README][llama-server].

## Stage 4: connect KevinBeLLM to the coordinator

Run the existing application only on A. Its SearXNG, live-tools, database, and
browser UI stay as designed; only its inference backend changes. In A's private
`.env`, set the llama.cpp backend and the same model alias exposed by
`llama-server`:

Machine A also needs a supported Compose runtime. Ubuntu 24.04 currently ships
Podman 4.9 but an older `podman-compose` than this project accepts. Install
rootless Podman and the pinned PyPI release as A's normal login user, then
select it explicitly:

```bash
sudo apt-get install -y --no-install-recommends \
  podman pipx uidmap slirp4netns fuse-overlayfs
pipx install 'podman-compose==1.6.0'
export PATH="${HOME}/.local/bin:${PATH}"
podman info
podman-compose --version
./scripts/select-container-engine.sh podman
```

Do not install a container runtime on B solely for the RPC worker; its service
runs the native pinned llama.cpp binary. If a newer `podman-compose` is adopted,
update and retest the pin deliberately rather than installing an unbounded
latest version during recovery.

```dotenv
INFERENCE_BACKEND=llamacpp
INFERENCE_BASE_URL=http://127.0.0.1:8080
DEFAULT_MODEL=kevinbellm-27b
PREFERRED_MODELS=kevinbellm-27b
```

For the llama.cpp backend, context is controlled by
`LLAMA_ARG_CTX_SIZE` in A's private `coordinator.env`; the legacy
`OLLAMA_CONTEXT_LENGTH` setting applies only when the backend is Ollama. Keep
the base URL on loopback; the container uses host networking specifically to
reach loopback services.

Use the repository lifecycle scripts so the backend health check follows the
selected mode:

```bash
./scripts/setup.sh
./scripts/start.sh
./scripts/status.sh
./scripts/doctor.sh
```

`scripts/inference.sh` is the shared backend control/status helper used by the
updated lifecycle. Do not separately expose llama-server to make the container
reach it.

If the existing `kevinbellm.service` user unit should start the UI before any
interactive login, enable lingering once on A:

```bash
sudo loginctl enable-linger <linux-user>
./scripts/install-autostart.sh
```

The UI unit retries a failed boot-time start after transient inference or
container-runtime failures. A deliberate `systemctl --user stop
kevinbellm.service` remains stopped.

Do not enable the Cloudflare remote mode merely for home-LAN access. The local
Windows SSH tunnel below is smaller and keeps the UI off the LAN.

## Stage 5: open the UI safely from Windows

The UI remains bound to A's loopback interface. From PowerShell on the laptop:

```powershell
Get-Help .\scripts\windows\Open-KevinBeLLMForward.ps1 -Detailed
.\scripts\windows\Open-KevinBeLLMForward.ps1
```

The equivalent manual tunnel is:

```powershell
ssh.exe -NT `
  -o BatchMode=yes `
  -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=30 `
  -o ServerAliveCountMax=3 `
  -L 127.0.0.1:3000:127.0.0.1:3000 `
  kevinbellm-a
```

Leave that PowerShell window open and browse to <http://127.0.0.1:3000>. Closing
the window only closes laptop access; A and B keep running. Re-run the helper to
reconnect. Never use `-L 0.0.0.0:3000:...`, never browse to A's LAN IP on port
3000, and never forward 3000 on the router.

For a deliberate direct llama API diagnostic, the helper's
`-ForwardLlamaApi` switch also maps laptop loopback port 18080 to A's 8080. It
never forwards RPC ports 50052 or 50053. Leave that switch off for normal UI
use.

The normal cold-boot routine is now exact:

1. Power on B, enter B's disk passphrase, and wait for Ubuntu to finish booting.
2. Power on A, enter A's disk passphrase, and wait. Its tunnel unit may retry
   until B is ready.
3. From the laptop, SSH to B and run
   `./scripts/cluster/cluster-status.sh --role worker` from the project checkout;
   SSH to A and run the same command with `--role coordinator`.
4. Start the Windows UI tunnel and open `http://127.0.0.1:3000`.

## Model fit: treat 20 GiB as a ceiling, not free capacity

The nominal device total is 12 + 8 = 20 GiB, but CUDA contexts, compute buffers,
the KV cache, the display server if present, and allocation margins all consume
VRAM. Model file size is not total runtime memory. System RAM on the two hosts
also does not become shared VRAM.

| Model class | Typical Q4 weight artifact | Recommended use here |
|---|---:|---|
| 9B dense | about 5.6-5.9 GB | Run on either one GPU; usually faster and simpler without RPC. |
| 27B dense | about 17-18 GB | Plausible across both GPUs at 4K, possibly 8K context; this is the first cluster target. |
| 30B MoE Q4_K_M | about 18.6 GB | Borderline but plausible; benchmark context and scratch headroom. |
| 35B MoE Q4_K_M | about 20.4-21.2 GB for weights alone; roughly 22-24+ GB working need | Does not fully fit in the nominal 20 GiB pool. CPU offload can run it, but it is no longer an all-VRAM workload. |

For concrete artifacts, a Qwen3.5 9B Q4_K_M conversion is listed at 5.63 GB
([9B artifact][qwen-9b]), a Qwen3.5 27B Q4_K_M conversion at 17.98 GB
([27B artifact][qwen-27b]), and the ggml-org Qwen3.6 35B Q4_K_M file at 20.4 GB
before runtime allocations ([35B artifact][qwen-35b]). Artifact repositories and
tags can change: record the model repository revision, filename, byte size,
SHA-256, source-model license, and chat template when downloading.

The reviewed first 27B candidate is pinned rather than taken from a mutable
`main` branch:

```text
Repository: bartowski/Qwen_Qwen3.5-27B-GGUF
Revision:   d7b113c40283f4d99f4eb0ec20d126ad653cc736
File:       Qwen_Qwen3.5-27B-Q4_K_M.gguf
Bytes:      17984872928
SHA-256:    81657841d62f1821c748d0fea6c260b7d3508844fe4e9250253ef81c4e4d9edf
```

Download only on A (B receives and caches tensors through RPC), then require
the hash to match before starting a service. The reviewed helper pins the URL,
resumes through a `.part` file, validates byte count and SHA-256, and refuses to
overwrite a mismatching file:

```bash
./scripts/cluster/download-model.sh
```

Do not continue on a mismatch. Review the source model and quantizer licenses
at that pinned revision before use.

Start the 27B service with these principles:

- `--host 127.0.0.1 --port 8080`;
- `--rpc 127.0.0.1:50053`;
- automatic GPU layers, `--fit on`, and default layer split first;
- a 4K context initially;
- the default roughly 1 GiB fit margin per device;
- Q8 K and V caches to save VRAM, with prompt-cache RAM disabled;
- `--jinja` for the tool-call-capable chat endpoint;
- one request slot while characterizing memory.

Then compare automatic placement with an explicit 12:8 capacity ratio. Do not
assume 12:8 is fastest: free memory, the display workload, the 3070's greater
compute/bandwidth, and RPC transfer cost can change the optimum. If it OOMs,
reduce context and batch sizes first, try quantized KV cache if the model
supports it, then choose a smaller quant or partial CPU offload. Do not remove
all safety margin merely because one short prompt loaded once.

The statement that RPC sends only a small activation at a layer boundary is a
useful intuition for token generation, not a performance guarantee. Startup may
send large tensors, prompt processing uses larger batches, SSH adds encryption,
and implementation details can change. Gigabit Ethernet has a 125 MB/s
theoretical line rate, far below either GPU's local VRAM bandwidth. Measure this
specific pair.

## Benchmark before choosing the permanent mode

Keep the llama.cpp commit, GGUF SHA-256, context, batch settings, prompt/generation
lengths, thermals, and number of repetitions fixed. `llama-bench` reports prompt
processing (`pp`) and token generation (`tg`) separately and excludes
tokenization/sampling; the upstream [benchmark documentation][llama-bench]
explains those limits.

Use three or five repetitions after a warm-up. Stop A's production model server
to release its VRAM, but keep B's worker and A's tunnel running:

```bash
systemctl --user stop kevinbellm-llama.service
LLAMA_BENCH="$HOME/.local/opt/llama.cpp-b10451/build/bin/llama-bench"
MODEL="$HOME/models/Qwen_Qwen3.5-27B-Q4_K_M.gguf"

# Two-node automatic-placement test from A
"$LLAMA_BENCH" \
  --model "$MODEL" \
  --rpc 127.0.0.1:50053 \
  --n-gpu-layers 99 \
  --fit-target 1024 \
  --n-prompt 512 \
  --n-gen 128 \
  --repetitions 3 \
  --output md

# Explicit capacity split comparison; b10451 llama-bench uses slash separators.
"$LLAMA_BENCH" \
  --model "$MODEL" \
  --rpc 127.0.0.1:50053 \
  --n-gpu-layers 99 \
  --fit-target 1024 \
  --tensor-split 12/8 \
  --n-prompt 512 \
  --n-gen 128 \
  --repetitions 3 \
  --output md
```

For the 9B single-GPU baselines, point `MODEL` at one checksum-identical 9B
GGUF on each host and run the same command without `--rpc`, `--fit-target`, or
`--tensor-split`. `--n-gpu-layers 99` means all layers for these models; unlike
`llama-server`, this pinned `llama-bench` expects an integer here. Restart the
production server afterward with
`systemctl --user start kevinbellm-llama.service`.

Copy the model temporarily to B only if benchmarking B alone; verify its hash
matches A. Stop the production server first so it does not reserve VRAM.

Fill this matrix rather than relying on somebody else's token rate:

| Test | Model/context | Placement | Cold load s | pp512 tok/s | tg128 tok/s | Peak A VRAM | Peak B VRAM | Notes |
|---|---|---|---:|---:|---:|---:|---:|---|
| A1 | 9B Q4 / 4K | A / 3060 only | | | | | n/a | simple baseline |
| B1 | 9B Q4 / 4K | B / 3070 only | | | | n/a | | likely fastest 9B single-card path |
| C1 | 9B Q4 / 4K | A+B / automatic | | | | | | proves whether RPC helps a small model |
| A2 | 27B Q4 / 4K | A with CPU spill | | | | | n/a | no-pool baseline |
| C2 | 27B Q4 / 4K | A+B / automatic | | | | | | primary target |
| C3 | 27B Q4 / 4K | A+B / 12:8 | | | | | | compare split |
| C4 | 27B Q4 / 8K | best C2/C3 split | | | | | | context headroom test |
| C5 | chosen config | end-to-end KevinBeLLM | | n/a | visible tok/s | | | include tool-call and UI latency |

During each run, watch both hosts:

```bash
nvidia-smi --loop=1

# On A
journalctl --user -f \
  -u kevinbellm-rpc-tunnel.service \
  -u kevinbellm-llama.service

# On B
journalctl --user -f -u kevinbellm-rpc-worker.service
```

Also verify the wired path with `ethtool` and, if desired, a short controlled
`iperf3` test bound to B's LAN address. If temporarily opening an iperf port in
UFW, scope it to A and remove the rule immediately after the test. Never use the
RPC port as the network-throughput test.

Choose based on both `pp` and `tg`: interactive chat often cares about time to
first token and steady generation, while long document ingestion is dominated
by prompt processing. The 9B model may be better on one GPU even when the 27B
model benefits decisively from pooling.

## Better no-purchase alternatives

### Best technical option: both GPUs in one desktop

If existing parts support it, putting both cards in one desktop is better than
RPC: fewer services, no unauthenticated RPC protocol crossing even an SSH
tunnel, no Ethernet bottleneck, and local llama.cpp multi-GPU placement. It
still presents two CUDA devices rather than one VRAM allocation, but llama.cpp
can split tensors locally.

Do **not** move a card until all of these are physically verified:

- two usable PCIe slots, including the electrical lane width of the second;
- space for the 286 x 115 x 51 mm three-fan 3070 and the 201.8 mm dual-slot
  3060, including airflow and cable bends;
- a quality PSU with adequate total/12 V capacity for both GPUs plus CPU and
  transients;
- the required independent PCIe power connectors: one 8-pin for the 3060 and
  one 8-pin plus one 6-pin for the 3070;
- no SATA/Molex-to-PCIe adapters and no connector overloading;
- acceptable thermals and BIOS support, potentially including Above 4G
  Decoding.

The two GPU board-power figures total about 390 W before the CPU and rest of the
system. Manufacturer single-card system recommendations (550 W for the EVGA,
650 W for the Gigabyte) are not dual-card PSU sizing advice. Use the actual PSU
model and CPU to decide; if power connectors, clearance, or PSU headroom are
missing, the no-purchase condition is not met.

For a local two-GPU build, remove `--rpc`, the worker, and the A-to-B tunnel,
confirm both devices with `llama-server --list-devices`, and benchmark automatic
placement versus `--tensor-split 12,8` (the server CLI uses comma-separated
fractions). Put the faster 3070 in the best-connected slot if motherboard lanes
and thermals favor it, then measure rather than assuming.

### Best throughput option: two independent servers

Run a complete 9B Q4 model on each desktop and route separate conversations or
requests to them. This does **not** let a single 27B request use 20 GiB, but it
can deliver more aggregate requests per minute and isolates failures. It may
also give a single user lower latency because the 3070 is not waiting at an RPC
boundary. Keep both inference HTTP ports loopback-only and reach them with
separate SSH forwards or an authenticated loopback router on A.

This is the right comparison if most work fits in 8 GiB. Pool only when the
larger model's quality is worth the extra moving parts and latency.

### CPU offload

llama.cpp supports CPU+GPU hybrid inference for models larger than VRAM
([llama.cpp project description][llama-project]). A 35B Q4 can use A's 32 GiB
system RAM for the portion that will not fit, but DDR3 and CPU execution will be
materially slower. B's 32 GiB RAM is not transparently pooled with A's RAM.

## Failure diagnosis

Work from the bottom upward; do not repeatedly reinstall everything.

### Laptop cannot SSH after power-on

1. Look at the physical display. If it shows the encryption prompt, unlock it.
2. Check the router lease and wired link lights.
3. Try the reserved IP, not just the hostname.
4. At the console run `systemctl status ssh` and `ip -brief -4 address`.
5. Check `sudo ufw status numbered`.

If SSH reports that a host key changed, stop. Do not reflexively run
`ssh-keygen -R`. Compare the current fingerprint at the physical console and
determine whether the OS was reinstalled or an address was reassigned; an
unexplained change can be an interception attempt.

### A cannot see B's RPC worker

On B:

```bash
nvidia-smi
systemctl --user status kevinbellm-rpc-worker.service --no-pager
journalctl --user -u kevinbellm-rpc-worker.service -b --no-pager -n 100
ss -ltnp | grep 50052
```

On A:

```bash
systemctl --user status kevinbellm-rpc-tunnel.service --no-pager
journalctl --user -u kevinbellm-rpc-tunnel.service -b --no-pager -n 100
ss -ltnp | grep 50053
```

For packet-level SSH diagnostics, stop A's tunnel unit temporarily and rerun
the complete foreground `ssh -vvv -NT ... -L
127.0.0.1:50053:127.0.0.1:50052` command from Stage 3. Restart the unit after
the diagnosis.

Common causes are B still locked, a changed DHCP address, a host-key mismatch,
wrong `permitopen`, incorrect key permissions, or B's worker unit not running.
The expected state is a B loopback RPC listener and an A loopback SSH listener.

### RPC connects but model loading fails

- compare `llama-server --version` and the recorded b10451 commit on both;
- on A, run `~/.local/opt/llama.cpp-b10451/build/bin/llama-bench --rpc 127.0.0.1:50053 --list-devices`;
- set `GGML_RPC_DEBUG=1` temporarily on the worker and inspect its journal;
- verify the GGUF SHA-256 and architecture support;
- stop other GPU consumers and inspect free VRAM;
- lower context/batch sizes or use a smaller quant;
- clear the RPC cache only after stopping services and confirming that the
  exact cache path is disposable. A stale cache is a diagnosis, not the first
  assumption.

### It runs but is slow

- confirm both Ethernet links are 1 Gb/s full duplex and neither node fell back
  to Wi-Fi or 100 Mb/s;
- compare the same model locally on each GPU, then automatic and explicit
  splits;
- separate cold-load time, `pp`, `tg`, and end-to-end UI latency;
- watch GPU utilization, clocks, temperature, and power limits on both hosts;
- remember that the slowest pipeline stage and SSH CPU overhead can dominate;
- confirm the worker service uses `--cache`; compare first and second model
  loads because the first still crosses SSH;
- use one GPU for 9B if RPC is slower there. Pooling is a capacity tool, not an
  automatic speed multiplier.

### llama-server is healthy but the UI is not

On A, check in this order:

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:3000/health
./scripts/status.sh
./scripts/doctor.sh
```

Confirm `.env` has `INFERENCE_BACKEND=llamacpp`, a loopback
`INFERENCE_BASE_URL`, and an exact model alias match. Then reconnect the laptop
tunnel with `-o ExitOnForwardFailure=yes`; a local port collision on the laptop
will otherwise look like an application failure.

### Services do not return after reboot

First confirm the disk was unlocked; no normal service can fix a pre-unlock
state. Then use `systemctl --user is-enabled` and `journalctl --user -b` with
the exact three unit names above. Confirm lingering for the service user on
**both** hosts with `loginctl show-user "$USER" -p Linger`. Systemd ordering
makes A's server require its tunnel, and the tunnel restart policy tolerates B
booting later.

## Roll back without losing the original project

The cluster path is additive. Do not delete Ollama models, the application data
volume, `.env` backups, SSH access, or encrypted volumes while testing it.

1. On A, stop the application and disable its cluster inference path:

   ```bash
   ./scripts/stop.sh
   systemctl --user disable --now \
     kevinbellm-llama.service \
     kevinbellm-rpc-tunnel.service
   ```

2. On B, disable the worker:

   ```bash
   systemctl --user disable --now kevinbellm-rpc-worker.service
   ```
3. On A, restore the backed-up `.env` values:

   ```dotenv
   INFERENCE_BACKEND=ollama
   INFERENCE_BASE_URL=http://127.0.0.1:11434
   DEFAULT_MODEL=<an-installed-ollama-model>
   PREFERRED_MODELS=<installed-ollama-models>
   ```

4. Start the user-local Ollama service and the normal app:

   ```bash
   systemctl --user enable --now ollama.service
   ./scripts/start.sh
   ./scripts/status.sh
   ./scripts/doctor.sh
   ```

5. Re-open the same laptop UI tunnel. Port 3000 and the account database have
   not changed.

Only after the rollback has passed should you optionally remove cluster unit
files, dedicated tunnel public-key authorization, build trees, worker cache, or
GGUF files. Keep the laptop administration keys. When deleting a cache or model,
resolve and inspect its absolute path first.

## Exact acceptance checklist

Complete these in order. A failed checkbox is a stop condition for the next
stage.

### A. Physical and inventory

- [ ] Backups and disk-recovery information are current for both hosts.
- [ ] Both hosts are Ubuntu, sudo works, and encryption type/root mapping is
      recorded.
- [ ] `nvidia-smi` identifies the 3060 12 GiB on A and 3070 8 GiB on B.
- [ ] Both wired links negotiate 1 Gb/s full duplex.
- [ ] PSU label, slots, clearance, MAC addresses, usernames, and current IPs are
      recorded in the private inventory.
- [ ] Hostnames are `kevinbellm-a` and `kevinbellm-b`.

### B. Addressing and SSH

- [ ] Router DHCP reservations exist for both wired MAC addresses.
- [ ] There are no router forwards for SSH, UI, llama, Ollama, or RPC ports.
- [ ] A and B Ed25519 host-key fingerprints were captured at their consoles.
- [ ] The laptop's dedicated public key is installed on both; private key stays
      on the laptop.
- [ ] New key-only SSH sessions to A and B succeed and match pinned host keys.
- [ ] SSH hardening passes `sshd -t` and the helper's effective-setting checks;
      a recovery session was kept open while testing.
- [ ] Firewall rules allow only required SSH sources and expose no RPC/UI port.

### C. Runtime

- [ ] The repository exists at a known absolute path on each host with no copied
      secrets.
- [ ] `nvidia-smi` and `nvcc --version` succeed on both.
- [ ] Both llama.cpp builds report b10451 / commit
      `10bf611e533d81f739128304991c5e133c6aebd8`.
- [ ] A lists CUDA0 3060 and B lists CUDA0 3070.
- [ ] B's RPC worker listens only on `127.0.0.1:50052`.
- [ ] A's dedicated tunnel key is restricted, and B's host key is independently
      pinned.
- [ ] A's forwarded endpoint listens only on `127.0.0.1:50053`.
- [ ] A's llama-server listens only on `127.0.0.1:8080` and `/health` succeeds.
- [ ] The three exact user units are enabled and `Linger=yes` for the service
      user on both hosts.

### D. Model and application

- [ ] Model source, revision, license, filename, byte size, SHA-256, alias, and
      chat template are recorded.
- [ ] `download-model.sh` verifies the pinned 27B file; the worker cache has
      adequate disk space.
- [ ] The 27B Q4 starts at 4K context with Q8 K/V cache and VRAM margin on both
      GPUs.
- [ ] KevinBeLLM uses the llama.cpp loopback backend and exact model alias.
- [ ] `setup.sh`, `start.sh`, `status.sh`, and `doctor.sh` pass on A.
- [ ] A's UI listens only on `127.0.0.1:3000`.
- [ ] The Windows loopback tunnel opens the authenticated UI at
      `http://127.0.0.1:3000`.

### E. Measurement and reboot drill

- [ ] The 9B single-card and 27B two-node benchmark rows are recorded.
- [ ] Automatic versus 12:8 placement is compared with the same inputs.
- [ ] Cold load, prompt processing, token generation, end-to-end latency,
      thermals, and peak VRAM are recorded separately.
- [ ] B and then A were cleanly shut down, physically powered on, and locally
      unlocked.
- [ ] After unlock, worker, tunnel, llama-server, app, and laptop forward all
      recovered without a GUI login.
- [ ] Ollama rollback was tested or at least its backed-up configuration and
      exact recovery commands were verified.

When all boxes pass, the two-node service is maintainable from the laptop after
each physical power-on/unlock, with no model or UI port exposed to the LAN.

[nvidia-cc]: https://developer.nvidia.com/cuda/gpus
[evga-3060]: https://www.evga.com/products/specs/gpu.aspx?pn=716f8f06-ce42-42da-96f5-28429a21ec06
[gigabyte-3070]: https://www.gigabyte.com/Graphics-Card/GV-N3070GAMING-OC-8GD-rev-10/sp
[ubuntu-fde]: https://documentation.ubuntu.com/security/docs/security-features/storage/encryption-full-disk/
[loginctl]: https://www.freedesktop.org/software/systemd/man/latest/loginctl.html
[ubuntu-ssh]: https://documentation.ubuntu.com/server/how-to/security/openssh-server/
[ubuntu-ufw]: https://documentation.ubuntu.com/server/how-to/security/firewalls/
[openssh-sshd]: https://man.openbsd.org/sshd.8
[llama-project]: https://github.com/ggml-org/llama.cpp/tree/b10451
[llama-rpc]: https://github.com/ggml-org/llama.cpp/blob/b10451/tools/rpc/README.md
[llama-rpc-warning]: https://github.com/ggml-org/llama.cpp/blob/b10451/tools/rpc/README.md#overview
[rpc-rce]: https://github.com/ggml-org/llama.cpp/security/advisories/GHSA-j8rj-fmpv-wcxw
[rpc-current-report]: https://github.com/ggml-org/llama.cpp/issues/25289
[llama-server]: https://github.com/ggml-org/llama.cpp/blob/b10451/tools/server/README.md
[llama-bench]: https://github.com/ggml-org/llama.cpp/blob/b10451/tools/llama-bench/README.md
[qwen-9b]: https://huggingface.co/openresearchtools/Qwen3.5-9B-GGUF
[qwen-27b]: https://huggingface.co/bartowski/Qwen_Qwen3.5-27B-GGUF/tree/d7b113c40283f4d99f4eb0ec20d126ad653cc736
[qwen-35b]: https://huggingface.co/ggml-org/Qwen3.6-35B-A3B-GGUF/tree/main
