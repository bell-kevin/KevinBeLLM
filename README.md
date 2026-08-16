# KevinBeLLM: private local AI

KevinBeLLM is an AGPL-licensed, self-hosted AI workspace served from owned
Ubuntu hardware. This repository continues the original proof of concept at
its existing public URL. The everyday architecture is deliberately simple:

```text
Remote browser
  -> Cloudflare Access
  -> outbound-only Cloudflare Tunnel
  -> Machine A: KevinBeLLM login + app + standalone llama-server
                Qwen3.5-9B Q6_K fully on the RTX 3060 12 GiB
```

Machine B's RTX 3070 remains available for maintenance, experiments, or the
explicitly optional 27B two-node profile. Normal service does not depend on
Machine B and starts no llama.cpp RPC process, tunnel, or listener. The app and
model API are not published directly to the LAN or Internet; SSH is the only
LAN-facing administration service, and remote web traffic arrives through an
outbound Cloudflare Tunnel.

The public [GitHub Pages site](docs/index.html) is only a static project landing
page. It has no login form, runtime probe, model-health indicator, analytics, or
private configuration. Authorized users enter the actual assistant at
<https://assistant.kevin-bell.com/>, first through Cloudflare Access and then
through KevinBeLLM's own login.

## Hardware and model

| Role | GPU | Nominal VRAM | Workload |
| --- | --- | ---: | --- |
| Machine A, primary | NVIDIA GeForce RTX 3060 | 12 GiB | App and fully GPU-resident everyday model |
| Machine B, optional | NVIDIA GeForce RTX 3070 | 8 GiB | Maintenance and deliberate two-node experiments |

Both GPUs are Ampere devices with CUDA compute capability 8.6, so they use the
same pinned llama.cpp source revision and CUDA build configuration. Their 20
GiB of nominal VRAM remains two
separate memory pools: bandwidth does not add together, and CUDA contexts,
compute buffers, KV cache, and display use consume part of each card.

The default model installer pins `Qwen_Qwen3.5-9B-Q6_K.gguf` at immutable
revision `182be2fd6c7bc44887d88a91cb03ff009cc9f549`, verifies its exact
7,958,818,848-byte size and SHA-256, and refuses mismatched files. On Machine A,
the measured MTP configuration generated `53.985 ± 0.057` tokens/second over
three 128-token runs, retained about 4.3 GiB of free VRAM, and passed a
coherent OpenAI-compatible tool-call check. Results are local measurements,
not a guarantee for other systems.

The optional pinned 27B Q4_K_M artifact is retained for comparison. It needs
CPU offload on A alone and measured only about 1.42 tokens/second with the
conservative build, so it is not the interactive default. The existing
two-machine profile can distribute it across both GPUs only after the operator
accepts the separate RPC security warning.

## Cold boots and remote availability

Both hosts use full-disk encryption. After power loss or shutdown, someone must
physically power on a required machine and enter its LUKS passphrase; Wake-on-LAN
and SSH cannot bypass that pre-boot step. Everyday service needs only Machine A.
Once A is unlocked, enabled user services can restore inference, the app, and
the outbound connector without a graphical login. Machine B needs physical
unlock only when its optional services are intentionally used.

This repository intentionally does not publish current uptime, unlock state,
model availability, internal addresses, or service health.

## Deployment and administration

Follow [the cluster helper guide](infra/cluster/README.md) for the safe
standalone installation. It covers:

- physical inventory and encrypted-boot behavior;
- stable router DHCP reservations without public port forwarding;
- fingerprint-verified, key-only SSH from Windows;
- the pinned llama.cpp source and CUDA configuration;
- the standalone Machine A systemd user service;
- checksum-verified model installation;
- KevinBeLLM startup, validation, benchmarking, and rollback;
- an outbound Cloudflare Tunnel protected by Cloudflare Access.

The much longer [two-node setup guide](docs/TWO_NODE_SETUP.md) is retained for
the optional 27B RPC experiment; it is not required for normal operation.

The checked-in cluster helpers and ignored private environment files are
documented in [infra/cluster/README.md](infra/cluster/README.md).

For private LAN maintenance from the Windows administration laptop:

```powershell
ssh kevinbellm-a
ssh kevinbellm-b
```

To open a laptop-local UI tunnel without publishing a LAN port:

```powershell
.\scripts\windows\Open-KevinBeLLMForward.ps1
```

Then visit <http://127.0.0.1:3000>. Direct forwarding of the llama API is
available only as an explicit diagnostics option:

```powershell
.\scripts\windows\Open-KevinBeLLMForward.ps1 -ForwardLlamaApi
```

On Machine A, the application lifecycle remains:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/doctor.sh
./scripts/stop.sh
```

For an in-place upgrade of the old proof of concept, stop its old Compose
projects first. To retain its account database, set
`KEVINBELLM_DATA_VOLUME=asus-kevin-bellm-data` in the private root `.env`.
Fresh Machine A deployments use the hardware-neutral `kevinbellm-data` volume;
`setup.sh` refuses the common silent-empty-database migration mistake.

The deployed application uses the local llama.cpp OpenAI-compatible endpoint:

```dotenv
INFERENCE_BACKEND=llamacpp
INFERENCE_BASE_URL=http://127.0.0.1:8080
DEFAULT_MODEL=kevinbellm-9b
PREFERRED_MODELS=kevinbellm-9b,kevinbellm-27b
```

The inference address is required to remain on loopback.

## Optional RPC security boundary

The standalone default has no RPC process, dependency, command-line argument,
or listener and does not require a risk acknowledgment. If the optional
two-node profile is selected, do not skip its warning: llama.cpp describes its
RPC backend as experimental and insecure, and the backend has had critical
unauthenticated code-execution vulnerabilities. That profile therefore:

- binds the worker to `127.0.0.1:50052`;
- binds Machine A's forwarded endpoint to `127.0.0.1:50053`;
- carries RPC traffic through a source-restricted, command-restricted SSH key;
- blocks the RPC port in both host firewalls; and
- refuses to start RPC until the operator explicitly acknowledges the risk.

These controls reduce exposure; they do not make an unsafe RPC implementation
trusted. Never expose either RPC port to the LAN, a Cloudflare route, or a
router port forward. See [SECURITY.md](SECURITY.md) for reporting and deployment
guidance.

## Project layout

- `services/assistant-web/` — authenticated FastAPI assistant with a llama.cpp
  OpenAI-compatible backend adapter.
- `scripts/cluster/` — Ubuntu preparation, SSH hardening, pinned builds, model
  download, tunnel setup, service installation, and status checks.
- `systemd/cluster/` — hardened standalone, optional worker, tunnel, and
  coordinator templates.
- `scripts/windows/` — administration-laptop SSH setup and private forwarding.
- `infra/cluster/` — non-secret examples; active environment files are ignored.
- `docs/` — static GitHub Pages landing site and the authoritative deployment
  guide.

KevinBeLLM retains Argon2 password hashing, hashed sessions, CSRF and origin
checks, a bounded tool loop, SearXNG integration, live-data tools, and a
rootless-container layout. Cloudflare Access supplements this application
login; it does not replace it.

## License and public source

The project is licensed `AGPL-3.0-or-later`; see [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Models, llama.cpp, Cloudflare,
and other dependencies retain their own licenses and terms.

When the modified application is offered over a network, publish the
corresponding source and set `SOURCE_URL` to this exact public repository.
Never commit private environment files, passwords, SSH keys, Cloudflare tunnel
credentials, model files, account databases, logs, or chat exports.
