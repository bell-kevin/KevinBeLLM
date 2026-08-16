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

The measured persistent configuration is 32,768-token context, batch 2,048,
ubatch 512, eight CPU threads, one request slot, f16 K/V cache, flash attention,
memory mapping, and Qwen3.5 MTP with draft maximum 2. Generation ranges from
roughly 53 to 69 tokens/second with a median near 62, with 3,322 MiB VRAM free
and temperatures in the high 40s to low 50s °C. A forced OpenAI-compatible tool
request returned exactly one parsed weather call. These are measurements of this
Machine A, not general guarantees.

That range is set by MTP draft acceptance, which depends on how predictable the
output text is. Generation speed tracks acceptance almost linearly, so the
workload matters far more than the request size. Measured at a 256-token
generation budget, prompt caching off, at the temperature 0.3 the application
actually sends:

| Output type | Generation | MTP draft acceptance |
| --- | ---: | ---: |
| Code, arithmetic reasoning | 68-69 tokens/s | 87-89% |
| Structured data, lists, short factual answers, translation | 61-63 tokens/s | 71-77% |
| Discursive technical prose, creative writing | 53-57 tokens/s | 56-62% |

Benchmark at the temperature the application sends, not at 0. Sampling
temperature feeds back into draft acceptance: dropping 0.3 to 0 raised a prose
answer from 52 to 59 tokens/second, while a structured JSON answer did not move
at all, because its output was already near-deterministic. Measuring at 0
overstates the prose end of the range by roughly 12 percent.

Answer length does not cost speed. Forcing generation from 128 to 4,096 tokens
with `ignore_eos` raised the rate rather than lowering it, because repetitive
filler drafts better than real prose. There is no KV-growth penalty from long
answers worth planning around. Prompt length is nearly free up to about 7,500
tokens (65.0 tokens/second at a tiny prompt, 64.9 at 7,569) and costs about 11
percent by 23,949, all measured at temperature 0.

When comparing future runs, hold the workload fixed. Two prompts of identical
length and identical generation budget differed by 15 tokens/second purely on
output type, which is wider than most of the tuning changes below. A sweep run
against a single prompt will rank these settings wrongly.

Settings swept on this hardware before being fixed:

| Setting | Swept range | Chosen | Finding |
| --- | --- | ---: | --- |
| Context size | 4,096 - 32,768 | 32,768 | KV cache is unusually cheap, so the old 4,096 gave up 8x context for almost nothing. |
| K/V cache type | q8_0, f16 | f16 | Identical on short prompts, so an early short-prompt comparison called it a wash. At depth the dequantization step is real: +8% at a 23,949-token prompt with draft acceptance matched. Costs about 500 MiB. |
| MTP draft depth | 1 - 8 | 2 | Peak of a clear inverted U. See the table below. |
| ubatch | 256 - 2,048 | 512 | All values land within 2% of ~1,350 tokens/second. Prefill is GPU-compute-bound here, not batch-bound, so the smallest sufficient value keeps VRAM free. |

The draft-depth sweep, on f16 K/V at temperature 0, as the mean over the ten
output types above and as a single deep-context probe:

| `--spec-draft-n-max` | Ten-workload mean | 23,949-token prompt |
| ---: | ---: | ---: |
| 1 | 56.8 tokens/s | 51.0 tokens/s |
| **2** | **64.1 tokens/s** | **57.8 tokens/s** |
| 3 | 65.0 tokens/s | 55.5 tokens/s |
| 4 | 63.6 tokens/s | 53.1 tokens/s |
| 6 | 55.7 tokens/s | 43.8 tokens/s |
| 8 | 57.9 tokens/s | 42.3 tokens/s |

Depth 3 edges depth 2 on short prompts, but 2 wins at every realistic prompt
length and by 4 percent at 24,000 tokens, so 2 is the setting. Depth 2 also
compresses the spread rather than raising the peak: against the previous q8_0
and depth-4 configuration, the slowest workload rose from 40 to 53 tokens/second
while the fastest fell from 78 to 69. For an interactive assistant the floor
matters more than the ceiling, so this is a deliberate trade.

Prefill rate, not generation rate, sets time to first token: about 0.3 s at 130
prompt tokens, 2.6 s at 3,200, and 18 s at 23,000. That ceiling is a property of
the RTX 3060 and cannot be tuned away.

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
