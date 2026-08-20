# Machine A inference

The safe everyday deployment runs entirely on Machine A:

```text
Windows laptop --SSH--> Machine A:22
                            |-- UI 127.0.0.1:3000
                            `-- llama-server 127.0.0.1:8080
                                `-- RTX 3060 + RTX 3070: Qwen3.8-27B IQ4_XS
```

The everyday model is resident across both GPUs in Machine A. Ports 3000 and
8080 bind to loopback, with no LAN listener or router port-forward.

## Encrypted boot boundary

Full-disk encryption means SSH and systemd cannot start until someone physically
powers on the required host and enters its LUKS passphrase. Everyday service
requires only this host. User lingering lets inference, the application, and
the outbound Cloudflare connector return after A is unlocked without a desktop
login. Automatic TPM or initramfs-network unlock has a different threat model
and is outside this deployment.

Reserve the Ethernet addresses in the router for durable SSH aliases, but do
not create any router port forwards. A DHCP client-list entry is not a
reservation: both MAC/IP pairs must appear under **Address Reservation**.

## 1. Prepare Machine A and admin SSH

At A's physical console, from this repository:

```bash
./scripts/cluster/prepare-ubuntu-host.sh --hostname kevinbellm-a
nvidia-smi
nvcc --version
```

On the Windows administration laptop, populate the ignored private inventory
and install a passphrase-protected admin key:

```powershell
Copy-Item infra\cluster\inventory.example.env infra\cluster\inventory.env
notepad infra\cluster\inventory.env
.\scripts\windows\Install-KevinBeLLMSSH.ps1 -GenerateKey -InstallPublicKey
ssh kevinbellm-a
```

Compare the first-login ED25519 fingerprint against the value read at A's
physical console. Only after a second key-only login works, harden SSH from the
trusted LAN CIDR:

```bash
sudo ./scripts/cluster/harden-ssh.sh \
  --admin-user "$(id -un)" --lan-cidr 192.168.0.0/24 --enable-ufw
```

Keep the original session open while testing. Never add an Internet-facing SSH
router rule.

## 2. Build the pinned llama.cpp revision

Run as A's normal login/service user:

```bash
./scripts/cluster/install-llama-cpp.sh
cat ~/.local/opt/llama.cpp-b10451/KEVINBELLM_BUILD_SPEC.txt
```

The installer verifies immutable commit
`10bf611e533d81f739128304991c5e133c6aebd8`, CUDA compute capability 8.6,
disabled embedded/prebuilt UI, and `GGML_RPC=OFF`. It builds only
`llama-server`, `llama-cli`, and `llama-bench`.

## 3. Download and verify the everyday model

```bash
./scripts/cluster/download-model.sh --preset 27b-iq4_xs
```

The default and sole supported preset is `27b-iq4_xs`, so omitting `--preset`
is equivalent. The download resumes through a mode-private `.part` file and
verifies:

```text
Repository: unsloth/Qwen3.8-27B-GGUF
Revision:   4ca720788d1e01f1bff70c033e0d0028fd02e502
File:       Qwen3.8-27B-UD-IQ4_XS.gguf
Bytes:      14252845984
SHA-256:    40fac4050e940397dbf13087afd50f4734a11805bf9d65ef8ddd7483470e6199
```

It refuses output symlinks and never overwrites a mismatched final file. Verify
an existing copy without network access with:

```bash
./scripts/cluster/download-model.sh \
  --preset 27b-iq4_xs \
  --output "$HOME/models/Qwen3.8-27B-UD-IQ4_XS.gguf" \
  --verify-only
```

## 4. Install the standalone service

```bash
./scripts/cluster/install-services.sh --enable-now
```

The first invocation safely creates
`~/.config/kevinbellm-cluster/standalone.env` with the absolute home path and
mode `0600`. It verifies the GGUF before starting, enables user lingering, and
installs `kevinbellm-llama.service`.

The installer verifies the pinned build specification and model before writing
the unit. If the service is already active, it restarts it after installing the
updated template. Its model path, alias, and measured tuning live in the private
env file; its endpoint and security arguments cannot be changed there:

- API: `127.0.0.1:8080` only;
- devices: `CUDA0,CUDA1`, layer split `64,36`, every model layer on the GPUs;
- no readable system/user llama.cpp configuration that could inject server
  arguments before command-line parsing;
- offline local-model mode, with model-router/download environment overrides
  removed;
- llama.cpp built-in agent mode disabled (ordinary OpenAI-compatible tool-call
  responses remain available to KevinBeLLM);
- a cleared runtime environment with PCI-bus device ordering and
  `CUDA_VISIBLE_DEVICES=0,1`; the private model/tuning values are expanded into
  fixed command-line positions by systemd before launch;
- no multimodal projector, embedded web UI, or slots endpoint;
- hardened systemd filesystem, capability, namespace, and privilege controls.

The measured persistent configuration is a 32,768-token context, batch 2,048,
ubatch 512, eight CPU threads, one request slot, q8_0 K/V cache, flash attention,
memory mapping, and Qwen3.8 MTP with draft maximum 2. At the deployed sampling
temperature it generates roughly 22-27 tokens/second, with a median near 24.
Prefill is about 595 tokens/second on a short prompt and 470 at 8k. These are
measurements of this Machine A, not general guarantees.

Keep ubatch at 512: 1,024 sends twice as much activation data over the PCIe 2.0
x1 boundary and reduced measured prefill, while 2,048 exhausted VRAM. The
`64,36` tensor split leaves more headroom on the 8 GiB card than `60,40` at a
small decode cost. Benchmark changes across several fixed workloads at the
application's deployed sampling settings; MTP acceptance makes output type a
material part of the result. The repository root README records the detailed
measurements.

Check the boundary and advertised alias:

```bash
./scripts/cluster/cluster-status.sh
curl --fail http://127.0.0.1:8080/v1/models
```

The status command fails if 8080 is not IPv4-loopback-only. The model response
must advertise `kevinbellm-27b`.

## 5. Connect and autostart KevinBeLLM

For a fresh `.env`, `setup.sh` now selects:

```dotenv
INFERENCE_BASE_URL=http://127.0.0.1:8080
DEFAULT_MODEL=kevinbellm-27b
PREFERRED_MODELS=kevinbellm-27b
CHAT_CONCURRENCY=1
```

`setup.sh` deliberately preserves an existing private `.env`. For an existing
deployment, edit only those model/concurrency lines after making a protected
copy. The application and llama.cpp should both use the stable
`kevinbellm-27b` alias.

Start and verify the local application:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/doctor.sh
```

Install local or authenticated-remote autostart only after its prerequisites
are configured:

```bash
./scripts/install-autostart.sh local
# Or, after Cloudflare Access and the protected named Tunnel are ready:
./scripts/install-autostart.sh remote
```

Both application modes call `scripts/inference.sh`, which uses the stable
`kevinbellm-llama.service` name. On an encrypted cold boot, unlock Machine A,
then confirm `loginctl show-user "$USER" -p Linger` and the service status.

For private LAN access from Windows:

```powershell
.\scripts\windows\Open-KevinBeLLMForward.ps1
```

This opens only laptop-loopback port 3000. `-ForwardLlamaApi` is an explicit
diagnostic option for laptop-loopback port 18080.

## Operations

```bash
systemctl --user status kevinbellm-llama.service --no-pager
journalctl --user -u kevinbellm-llama.service -e
systemctl --user restart kevinbellm-llama.service
systemctl --user stop kevinbellm-llama.service
```

Keep model files, `.env` files, account databases, SSH material, tunnel tokens,
logs, and chat data out of Git. The public repository contains only examples,
immutable artifact identities, and deployment code.
