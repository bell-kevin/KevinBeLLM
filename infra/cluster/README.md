# Machine A inference

The safe everyday deployment runs entirely on Machine A:

```text
Windows laptop --SSH--> Machine A:22
                            |-- UI 127.0.0.1:3000
                            `-- llama-server 127.0.0.1:8080
                                `-- RTX 3060 + RTX 3070: Qwen3.8-27B Q5_K_S
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
cat ~/.local/opt/llama.cpp-b10451-bdver2/KEVINBELLM_BUILD_SPEC.txt
```

The installer verifies immutable commit
`10bf611e533d81f739128304991c5e133c6aebd8`, CUDA compute capability 8.6,
flash attention, CUDA graphs, disabled embedded/prebuilt UI, and `GGML_RPC=OFF`.
It also verifies that the FX-8370's `bdver2 -mno-lwp` target reaches llama.cpp's
sampler, grammar, vocabulary, Unicode, and common sampling translation units,
and that the linked CUDA runtime major matches the selected compiler. It builds
only `llama-server`, `llama-cli`, and `llama-bench`.

## 3. Download and verify a model preset

```bash
./scripts/cluster/download-model.sh --preset 27b-q5_k_s
```

The measured everyday deployment uses `27b-q5_k_s`. The helper also retains
`27b-iq4_xs` as the smaller rollback preset. Both are pinned to the same
immutable repository revision and downloads resume through a mode-private
`.part` file.

The deployed Q5_K_S artifact verifies as:

```text
Preset:     27b-q5_k_s
Repository: unsloth/Qwen3.8-27B-GGUF
Revision:   4ca720788d1e01f1bff70c033e0d0028fd02e502
File:       Qwen3.8-27B-UD-Q5_K_S.gguf
Bytes:      18665753504
SHA-256:    d8d62ffcf84d42658dd6ccf9782b4d0404700af78b26d750507510c7597b5bfe
```

The IQ4_XS fallback verifies as:

```text
Preset:     27b-iq4_xs
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
  --preset 27b-q5_k_s \
  --output "$HOME/models/Qwen3.8-27B-UD-Q5_K_S.gguf" \
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
- devices: `CUDA0,CUDA1`, layer split `67,33`, every model layer on the GPUs;
- Qwen reasoning defaults made explicit with `--reasoning-effort xhigh` and
  `--reasoning-preserve`; individual non-thinking requests can still select
  `reasoning_effort=none`;
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

The measured persistent Q5_K_S configuration is a 32,768-token context, batch
512, ubatch 128, eight generation and batch CPU threads, one request slot,
`q4_0` K/V cache, flash attention, memory mapping, every model layer on the
GPUs, and a `67,33` tensor split. Qwen3.8 MTP remains at draft maximum 2 and
probability threshold zero. Observed Q5_K_S decode is about 30-33 tokens/second,
and a 15,395-token prompt evaluated at 377.28 tokens/second. After the full
quality run, peak memory was 11,872/12,288 MiB on the RTX 3060 and
7,353/8,192 MiB on the RTX 3070. These are measurements of this Machine A, not
general guarantees.

Qwen publishes separate sampler recipes for its two modes. Production thinking
uses `temperature=1`, `top_p=0.95`, `top_k=20`, `min_p=0`,
`presence_penalty=0`, and `repeat_penalty=1`; production non-thinking uses
`temperature=0.7`, `top_p=0.8`, `top_k=20`, `min_p=0`,
`presence_penalty=1.5`, and `repeat_penalty=1`. Do not mix the two profiles when
comparing configurations. See the [Qwen3.8-27B model
card](https://huggingface.co/Qwen/Qwen3.8-27B).

Treat batch 512, ubatch 128, `q4_0` K/V, and the `67,33` split as one tested Q5
configuration; its primary GPU has only 416 MiB of measured peak headroom. The
former IQ4_XS deployment used batch 2,048, ubatch 512, `q8_0` K/V, and a `64,36`
split, but those settings are not a safe template for the larger quant.
Benchmark changes across several fixed workloads at the application's deployed
sampling settings; MTP acceptance makes output type a material part of the
result. The repository root README records the historical IQ4_XS measurements.

For a repeatable comparison, forward the raw API to laptop loopback and run:

```bash
python3 scripts/cluster/benchmark-inference.py \
  --base-url http://127.0.0.1:18080
```

The default is one warm-up and three measured repetitions of both the Fast and
production-shaped tool-schema paths. `--help` documents the fixed-workload,
long-context, prompt-cache, alternate-branch, and JSON-output controls.

Measure answer quality separately. Capture a same-hardware baseline before
changing the quant or inference settings, then compare the new configuration
after its server is ready on the same loopback forward:

```bash
python3 scripts/cluster/evaluate-quality.py \
  --base-url http://127.0.0.1:18080 \
  --profile reasoning --json > /tmp/kevinbellm-quality-baseline.json

python3 scripts/cluster/evaluate-quality.py \
  --base-url http://127.0.0.1:18080 \
  --profile reasoning \
  --compare /tmp/kevinbellm-quality-baseline.json \
  --fail-on-regression
```

The evaluator defaults to `xhigh`, preserved thinking and Qwen's official
thinking sampler, plus the production output ceiling and forced-answer
reasoning budget. It scores a deterministic 16-case corpus spanning reasoning,
coding, instruction following, calibration, grounded tool use, and long-context
retrieval. `--fail-on-regression` rejects a lower total or a regression in the
protected calibration or long-context categories. Run `--list-cases` to inspect
the case IDs and `--help` for seed, token-budget, non-thinking, and JSON options.

The deployment decision used the same single seed, `424242`, for both quants.
IQ4_XS passed 13/14 (92.86%); the stable all-GPU Q5_K_S configuration passed
14/14 (100%), gaining the instruction-following case with no losses and no
protected-category regression. That is strong evidence for this exact local
regression gate, but one small single-seed corpus is not a statistically broad
intelligence measurement.

The resulting **Local Quality Score** is only a controlled regression signal
for this deployment. Artificial Analysis reports [35 for its hosted
non-reasoning configuration](https://artificialanalysis.ai/models/qwen3-8-27b-non-reasoning)
and [52 for its hosted `xhigh`
configuration](https://artificialanalysis.ai/models/qwen3-8-27b), but neither
number is a score for the local Q5_K_S deployment or its IQ4_XS fallback.

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

For an automatically reconnecting, per-user port 3000 forward at Windows
logon, run `scripts\windows\Install-KevinBeLLMAutoForward.ps1` once. The task
runs without elevation or stored credentials. Use
`Get-KevinBeLLMAutoForwardStatus.ps1` to inspect it and
`Uninstall-KevinBeLLMAutoForward.ps1` to remove it.

## 6. Turn off the RTX 3070 lighting (optional)

The Gigabyte RTX 3070 Gaming OC lights up from its own flash profile on every
power cycle. Its LED controller sits on the GPU's I2C bus, which only root can
reach through the NVIDIA driver, so this runs on Machine A as the login user
and uses sudo where needed:

```bash
./scripts/cluster/gpu-rgb-off.sh
```

The helper fetches a pinned, SHA-256-verified OpenRGB AppImage once into
`/opt`, sets the card to black at zero brightness, and installs the root
oneshot `gpu-rgb-off.service` so the setting returns after every boot. Pass
`--once` to skip the unit or `--uninstall` to remove everything it installed.

It refuses to run while `nvidia-smi` cannot enumerate every GPU. After an
Xid 79 ("GPU has fallen off the bus") event the driver cannot reach the card
until Machine A is rebooted at its console:

```bash
journalctl -k -b | grep -i xid
```

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
