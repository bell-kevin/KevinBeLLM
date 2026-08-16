# Two-machine Ampere inference cluster

Machine A is the coordinator (RTX 3060 12 GB). Machine B is the worker (RTX
3070 8 GB). Both run the exact llama.cpp `b10451` commit and CUDA architecture
8.6 build flags. Machine B's RPC process listens only on `127.0.0.1:50052`.
Machine A reaches it through a persistent, host-key-pinned SSH local forward at
`127.0.0.1:50053`; `llama-server` listens only on `127.0.0.1:8080`.

```text
Windows laptop --SSH--> Machine A:22
                            |-- UI 127.0.0.1:3000
                            |-- llama 127.0.0.1:8080
                            `-- 127.0.0.1:50053 ==SSH==> Machine B:22
                                                       `-> 127.0.0.1:50052 RPC
```

There is deliberately no LAN listener or router port-forward for 3000, 8080,
50052, or 50053.

## Mandatory RPC security acknowledgment

Do not skip this section. Upstream labels the RPC backend a fragile, insecure
proof of concept and says never to run it on an open network or in a sensitive
environment. It has no protocol authentication. A critical unauthenticated RCE
was published as [CVE-2026-34159 / GHSA-j8rj-fmpv-wcxw](https://github.com/ggml-org/llama.cpp/security/advisories/GHSA-j8rj-fmpv-wcxw),
and a separate controlled-indirect-call path was [reported against current code](https://github.com/ggml-org/llama.cpp/issues/25289).
Although `b10451` postdates the affected range of the first report, this setup
must treat access to an RPC socket as equivalent to code execution as the
service user.

The loopback binding, SSH encryption, restricted tunnel key, firewall rule, and
systemd hardening reduce exposure; they do not make the RPC parser safe. Any
compromised local process on A can reach A's forward, and any compromised local
process on B can reach B's RPC listener. Do not use this design with untrusted
users or workloads.

Both role env files must contain this exact, deliberate acknowledgment, and
service installation also requires a command-line acknowledgment flag:

```text
ACKNOWLEDGE_LLAMA_RPC_RCE=YES_I_ACCEPT_UNAUTHENTICATED_RCE_RISK
```

## Before configuration

1. Install a supported Ubuntu release on both machines with full-disk
   encryption, current NVIDIA drivers, and a CUDA toolkit.
2. In the router, reserve a stable IPv4 address for each Ethernet MAC. Do not
   create any port forwards. Record the addresses in a private copy of
   `inventory.example.env` named `inventory.env`.
3. Set each firmware to power on normally when its physical power button is
   pressed. Wake-on-LAN is optional and is not required or configured here.

Full-disk encryption changes the boot boundary: after power loss, normal SSH
cannot start until someone physically enters the LUKS passphrase. These scripts
enable systemd user lingering, so SSH, the tunnel, and inference return
automatically after that unlock without an interactive desktop login. Automatic
TPM unlock or initramfs SSH unlock has a different threat model and is outside
this setup.

## 1. Prepare both Ubuntu hosts

At each physical console, from a checkout of this repository:

```bash
./scripts/cluster/prepare-ubuntu-host.sh --hostname kevinbellm-a
# Use kevinbellm-b on Machine B.
```

Add `--with-ubuntu-cuda` only if using Ubuntu's CUDA package rather than an
already installed NVIDIA toolkit. Confirm `nvidia-smi` and `nvcc --version` on
both machines.

## 2. Establish laptop admin SSH

On the Windows laptop, copy the inventory example, edit every placeholder, and
run PowerShell:

```powershell
Copy-Item infra\cluster\inventory.example.env infra\cluster\inventory.env
notepad infra\cluster\inventory.env
.\scripts\windows\Install-KevinBeLLMSSH.ps1 -GenerateKey -InstallPublicKey
ssh kevinbellm-a
ssh kevinbellm-b
```

`ssh-keygen` asks for a passphrase for the laptop admin key. When connecting for
the first time, compare the displayed host-key fingerprint with `sudo
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256` run at that
machine's physical console. Never accept an unverified fingerprint.

Only after key logins work in new terminals, harden each host. Replace the user
and CIDR with the private inventory values:

```bash
sudo ./scripts/cluster/harden-ssh.sh \
  --admin-user YOUR_USER --lan-cidr 192.168.0.0/24 --enable-ufw
```

Keep the original login open while testing another key-only login. The UFW
option allows SSH from the LAN and inserts a deny for TCP/50052.

## 3. Build the same llama.cpp revision on both hosts

Run on A and B as the non-root service/login user:

```bash
./scripts/cluster/install-llama-cpp.sh
cat ~/.local/opt/llama.cpp-b10451/KEVINBELLM_BUILD_SPEC.txt
```

The installer verifies immutable commit
`10bf611e533d81f739128304991c5e133c6aebd8` and builds `llama-server`,
`ggml-rpc-server`, `llama-cli`, and `llama-bench` with:

```text
GGML_CUDA=ON
GGML_RPC=ON
CMAKE_CUDA_ARCHITECTURES=86
GGML_NATIVE=OFF
GGML_AVX=OFF
GGML_AVX2=OFF
GGML_BMI2=OFF
GGML_FMA=OFF
GGML_F16C=OFF
```

The explicit CPU baseline keeps the same binaries runnable on the older
pre-AVX/AVX2 host CPUs; inference remains CUDA-backed. The remaining CMake
flags are recorded in the build-spec file. Run the same script after a failed
build; CMake and Ninja resume idempotently.

## 4. Establish the restricted A-to-B tunnel identity

On A, generate the service key (unlocked so it can start unattended):

```bash
./scripts/cluster/generate-tunnel-key.sh
```

This key is safe only in the restricted account created below. Never put it in
a normal account's `authorized_keys`. Transfer only its `.pub` file to B using
the already trusted admin SSH connection.

At B's console or through an already verified admin session, display B's host
key and install the tunnel public key. `--from` must be A's router-reserved LAN
address:

```bash
./scripts/cluster/show-host-key-fingerprints.sh
sudo ./scripts/cluster/install-worker-tunnel-key.sh \
  --public-key-file /path/to/kevinbellm_rpc_tunnel_ed25519.pub \
  --from MACHINE_A_RESERVED_IP
```

The created `llama-rpc-tunnel` account has no interactive shell. Its key is
restricted twice (sshd plus `authorized_keys`) to client-local forwarding whose
only destination is B's `127.0.0.1:50052`.

Back on A, pin the ED25519 fingerprint read from B—not one learned from the
network:

```bash
./scripts/cluster/pin-worker-host-key.sh \
  --host MACHINE_B_RESERVED_IP \
  --fingerprint SHA256:PASTE_PHYSICALLY_VERIFIED_VALUE
```

The pin helper refuses to overwrite an existing different trust record.

## 5. Download the exact model on A

The downloader resumes an interrupted transfer and checks the immutable
revision, exact 17,984,872,928-byte size, and LFS SHA-256 before installing:

```bash
./scripts/cluster/download-model.sh
```

The preset is `model-presets/27b-q4_k_m.env.example`. The exact file is
`bartowski/Qwen_Qwen3.5-27B-GGUF` revision
`d7b113c40283f4d99f4eb0ec20d126ad653cc736`, file
`Qwen_Qwen3.5-27B-Q4_K_M.gguf`, SHA-256
`81657841d62f1821c748d0fea6c260b7d3508844fe4e9250253ef81c4e4d9edf`.

## 6. Install boot-persistent user services

Install the worker first. The first run creates a private example env and stops;
edit it, read the warning above, set the exact acknowledgment, and rerun:

```bash
# Machine B
./scripts/cluster/install-services.sh \
  --role worker --acknowledge-rpc-risk --enable-now
```

Do the same on A. Its env also needs the reserved worker address, absolute model
path, and stable model alias `kevinbellm-27b`:

```bash
# Machine A
./scripts/cluster/install-services.sh \
  --role coordinator --acknowledge-rpc-risk --enable-now
```

Installed unit names are:

- A: `kevinbellm-rpc-tunnel.service` and `kevinbellm-llama.service`
- B: `kevinbellm-rpc-worker.service`

A does not treat a merely listening SSH forward as worker readiness. The tunnel
unit runs pinned `llama-bench` device discovery through the forward and becomes
active only after an `RPC0` device answers. If B is still booting, the tunnel
keeps retrying. The worker and `llama-server` also retry transient failures
without exhausting a short start-limit window.

The llama defaults are 8192-token context, one parallel slot, zero prompt-cache
RAM, layer split, automatic GPU layer count/fit, Q8 K/V caches, and Jinja chat
templates. The Q8 caches conserve VRAM for the tight 27B fit. These settings are
configurable only in A's private `coordinator.env`. Network bindings are
hardcoded and cannot be overridden there. B enables upstream's persistent RPC
tensor cache under `~/.cache/llama.cpp/rpc`; the first model load crosses SSH,
while later identical loads can reuse that cache.

## 7. Verify the boundary and use it from Windows

```bash
# Machine B
./scripts/cluster/cluster-status.sh --role worker

# Machine A
./scripts/cluster/cluster-status.sh --role coordinator
```

The checks fail if 50052, 50053, or 8080 appears on anything except the expected
IPv4 loopback address. From another LAN device, TCP/50052 must be unreachable.
Do not "fix" RPC connectivity by binding it to `0.0.0.0` or forwarding it in
the router.

On Windows, keep this foreground PowerShell open while using the UI:

```powershell
.\scripts\windows\Open-KevinBeLLMForward.ps1
```

It creates only a laptop-loopback forward at `http://127.0.0.1:3000` to A's
app. Add `-ForwardLlamaApi` only for direct API diagnostics; that adds
`http://127.0.0.1:18080` to A's llama API. It never forwards RPC port 50052.

## Operational notes and alternatives

- A power button plus physical disk unlock is sufficient; user services restart
  after unlock because lingering is enabled. Check with `loginctl show-user
  "$USER" -p Linger`.
- Inspect logs with `journalctl --user -u UNIT_NAME -e`.
- Stop the coordinator with `systemctl --user stop kevinbellm-llama.service
  kevinbellm-rpc-tunnel.service`; stop B with `systemctl --user stop
  kevinbellm-rpc-worker.service`.
- Layer-split generation sends relatively small activations per token, but model
  loading and prompt processing still pay for gigabit Ethernet. Combined VRAM
  does not mean combined 400 GB/s bandwidth.
- Putting both GPUs in one adequately powered/cooled PCIe system is the safer,
  more mature no-purchase pooling method when the chassis, motherboard, and PSU
  already support it. It removes the unauthenticated RPC network parser and SSH
  hop. Keep this two-host RPC setup for the no-disassembly experiment and compare
  it with `llama-bench` before deciding.
