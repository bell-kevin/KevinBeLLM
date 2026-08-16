# Machine A inference and optional two-node mode

The safe everyday deployment runs entirely on Machine A:

```text
Windows laptop --SSH--> Machine A:22
                            |-- UI 127.0.0.1:3000
                            `-- llama-server 127.0.0.1:8080
                                `-- RTX 3060: Qwen3.5-9B Q6_K
```

Machine B is not a dependency. No llama.cpp RPC process, client address,
tunnel, or listener is used in this profile. The optional 27B experiment is
retained below and in [the detailed two-node guide](../../docs/TWO_NODE_SETUP.md),
but it remains disabled unless the operator separately accepts its risk.

There is deliberately no LAN listener or router port-forward for 3000, 8080,
50052, or 50053.

## Encrypted boot boundary

Full-disk encryption means SSH and systemd cannot start until someone physically
powers on the required host and enters its LUKS passphrase. Everyday service
requires only Machine A. User lingering lets inference, the application, and
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
and disabled embedded/prebuilt UI. The existing build includes the optional RPC
tools so the later experiment remains possible, but installed binaries do not
create a network surface by themselves. Standalone systemd never launches the
worker binary or gives `llama-server` an RPC address.

## 3. Download and verify the everyday model

```bash
./scripts/cluster/download-model.sh --preset 9b-q6_k
```

The default preset is also `9b-q6_k`, so omitting `--preset` is equivalent. The
download resumes through a mode-private `.part` file and verifies:

```text
Repository: bartowski/Qwen_Qwen3.5-9B-GGUF
Revision:   182be2fd6c7bc44887d88a91cb03ff009cc9f549
File:       Qwen_Qwen3.5-9B-Q6_K.gguf
Bytes:      7958818848
SHA-256:    073a9275e65d9c8cd2819cf5f77b99fbaa6e87ba591da6bbaa86ec073a64bfef
```

It refuses output symlinks and never overwrites a mismatched final file. Verify
an existing copy without network access with:

```bash
./scripts/cluster/download-model.sh \
  --preset 9b-q6_k \
  --output "$HOME/models/Qwen_Qwen3.5-9B-Q6_K.gguf" \
  --verify-only
```

## 4. Install the standalone service

```bash
./scripts/cluster/install-services.sh --role standalone --enable-now
```

The first invocation safely creates
`~/.config/kevinbellm-cluster/standalone.env` with the absolute home path and
mode `0600`. It verifies the GGUF before starting, enables user lingering, and
installs `kevinbellm-llama.service`.

No risk acknowledgment is needed. Standalone mode rejects
`--acknowledge-rpc-risk`, first disables any installed A-side server, RPC
tunnel, or worker, and refuses to continue if TCP/50052 or TCP/50053 is still
owned by another local process. This happens before model/build validation, so
a failed migration leaves inference down and the RPC path disabled. A
successful migration replaces stale server arguments through an explicit
restart. Its model
path, alias, and measured tuning live in the private env file; its endpoint and
RPC/security arguments cannot be changed there:

- API: `127.0.0.1:8080` only;
- device: `CUDA0`, split mode `none`, every model layer on the GPU;
- no RPC address;
- no readable system/user llama.cpp configuration that could inject RPC before
  command-line parsing;
- offline local-model mode, with model-router/download environment overrides
  removed;
- llama.cpp built-in agent mode disabled (ordinary OpenAI-compatible tool-call
  responses remain available to KevinBeLLM);
- a cleared runtime environment containing only `CUDA_VISIBLE_DEVICES=0`; the
  private model/tuning values are expanded into fixed command-line positions by
  systemd before launch;
- no multimodal projector, embedded web UI, or slots endpoint;
- hardened systemd filesystem, capability, namespace, and privilege controls.

The measured persistent configuration is 4,096-token context, batch 2,048,
ubatch 512, eight CPU threads, one request slot, Q8 K/V cache, flash attention,
memory mapping, and Qwen3.5 MTP with draft maximum 3. Three repeated 128-token
server runs measured `53.985 ± 0.057` generation tokens/second, with MTP draft
acceptance about 52%, 4,388 MiB VRAM free, and a peak temperature of
61°C. A forced OpenAI-compatible tool request returned exactly one parsed
weather call. These are measurements of this Machine A, not general guarantees.

Check the boundary and advertised alias:

```bash
./scripts/cluster/cluster-status.sh --role standalone
curl --fail http://127.0.0.1:8080/v1/models
```

The status command fails if 8080 is not IPv4-loopback-only, either RPC port is
listening, or the optional tunnel remains active or enabled. The model response
must advertise `kevinbellm-9b`.

## 5. Connect and autostart KevinBeLLM

For a fresh `.env`, `setup.sh` now selects:

```dotenv
INFERENCE_BACKEND=llamacpp
INFERENCE_BASE_URL=http://127.0.0.1:8080
DEFAULT_MODEL=kevinbellm-9b
PREFERRED_MODELS=kevinbellm-9b,kevinbellm-27b
CHAT_CONCURRENCY=1
```

`setup.sh` deliberately preserves an existing private `.env`. For an existing
deployment, edit only those model/concurrency lines after making a protected
copy. Even if an old `DEFAULT_MODEL=kevinbellm-27b` remains temporarily, the app
falls back to the only model actually advertised by llama.cpp.

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
diagnostic option for laptop-loopback port 18080 and never forwards RPC.

## Optional 27B two-node profile

Do not enable this merely because the second GPU exists. The standalone 9B
profile is dramatically faster for interactive use and avoids the RPC parser.
The two-node path is a capacity experiment for a larger dense model.

### Mandatory RPC warning

Upstream labels its RPC backend a fragile, insecure proof of concept without
protocol authentication. It has had critical unauthenticated code-execution
findings. An SSH tunnel limits who can reach the parser; it does not make that
parser trusted. Treat access to either RPC loopback socket as code execution as
the service user.

Both private RPC role files must contain this exact deliberate acknowledgment,
and installation also requires the command-line flag:

```text
ACKNOWLEDGE_LLAMA_RPC_RCE=YES_I_ACCEPT_UNAUTHENTICATED_RCE_RISK
```

If that risk is not accepted, stop here. Do not create the restricted account,
start a worker, or expose any RPC address.

### Retained workflow

The complete host-key pinning, no-shell forwarding account, firewall, cache,
benchmark, rollback, and troubleshooting instructions remain in
[`docs/TWO_NODE_SETUP.md`](../../docs/TWO_NODE_SETUP.md). In summary:

```bash
# A: download the retained model without replacing the 9B file
./scripts/cluster/download-model.sh --preset 27b-q4_k_m

# B, only after the full tunnel setup and acknowledgment
./scripts/cluster/install-services.sh \
  --role worker --acknowledge-rpc-risk --enable-now

# A, only after B is verified ready
./scripts/cluster/install-services.sh \
  --role coordinator --acknowledge-rpc-risk --enable-now
```

The coordinator installer renders the separately retained RPC template into the
same `kevinbellm-llama.service` name, so the app endpoint stays stable. It never
overwrites `standalone.env`, the 9B GGUF, `coordinator.env`, or tunnel keys.

Return to the safe default on A with:

```bash
./scripts/cluster/install-services.sh --role standalone --enable-now
./scripts/cluster/cluster-status.sh --role standalone
```

Then disable the worker on B if it was previously enabled:

```bash
systemctl --user disable --now kevinbellm-rpc-worker.service
```

The A installer disables its prior server, tunnel, and any locally installed
worker before validating the standalone replacement. It reports—but never
kills—an unknown process holding an RPC port. Model files and private profiles
remain available for a later deliberate comparison.

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
