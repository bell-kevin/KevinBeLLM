# KevinBeLLM: two-node local AI

KevinBeLLM is an AGPL-licensed, self-hosted AI workspace served from two
Ethernet-connected Ubuntu desktops. This repository continues the original
proof of concept at its existing public URL. Its current architecture is a
two-host Ampere design:

```text
Remote browser
  -> Cloudflare Access
  -> outbound-only Cloudflare Tunnel
  -> Machine A: KevinBeLLM login + app + llama-server + RTX 3060 12 GiB
                                                  |
                                                  | restricted SSH tunnel
                                                  v
                  Machine B: loopback-only llama.cpp RPC + RTX 3070 8 GiB
```

Machine A coordinates inference and serves the authenticated application.
Machine B contributes GPU layers through a loopback-only llama.cpp RPC worker.
The app, model API, and RPC listener are not published directly to the LAN or
Internet; SSH is the only LAN-facing administration service, and remote web
traffic arrives through an outbound Cloudflare Tunnel.

The public [GitHub Pages site](docs/index.html) is only a static project landing
page. It has no login form, runtime probe, model-health indicator, analytics, or
private configuration. Authorized users enter the actual assistant at
<https://assistant.kevin-bell.com/>, first through Cloudflare Access and then
through KevinBeLLM's own login.

## Hardware and model

| Role | GPU | Nominal VRAM | Workload |
| --- | --- | ---: | --- |
| Machine A, coordinator | NVIDIA GeForce RTX 3060 | 12 GiB | KevinBeLLM, llama-server, local GPU layers |
| Machine B, worker | NVIDIA GeForce RTX 3070 | 8 GiB | Restricted llama.cpp RPC worker |

Both GPUs are Ampere devices with CUDA compute capability 8.6, so they use the
same pinned llama.cpp source revision and CUDA build configuration. Their 20
GiB of nominal VRAM remains two
separate memory pools: bandwidth does not add together, and CUDA contexts,
compute buffers, KV cache, and display use consume part of each card.

The checked-in model installer pins
`Qwen_Qwen3.5-27B-Q4_K_M.gguf` (17,984,872,928 bytes) and verifies its SHA-256
before use. The setup guide calls for a conservative first load with one
request slot and a 4K context; larger contexts should be enabled only after
measuring real VRAM headroom.

For smaller models or concurrent users, one independent model server per GPU
may provide better aggregate throughput than splitting every request across the
network. Installing both cards in one compatible host would remove the network
RPC boundary, but only after confirming motherboard lanes, case clearance,
cooling, and PSU capacity.

## Cold boots and remote availability

Both hosts use full-disk encryption. After power loss or shutdown, someone must
physically power on each machine and enter its LUKS passphrase; Wake-on-LAN and
SSH cannot bypass that pre-boot step. Once the disks are unlocked, enabled user
services can restore the worker, restricted tunnel, coordinator, and app
without a graphical login.

This repository intentionally does not publish current uptime, unlock state,
model availability, internal addresses, or service health.

## Deployment and administration

Follow [the two-node setup guide](docs/TWO_NODE_SETUP.md) for the staged
installation. It covers:

- physical inventory and encrypted-boot behavior;
- stable router DHCP reservations without public port forwarding;
- fingerprint-verified, key-only SSH from Windows;
- the matching pinned legacy-CPU-safe llama.cpp source and CUDA configuration;
- the restricted Machine A-to-B tunnel and systemd user services;
- checksum-verified model installation;
- KevinBeLLM startup, validation, benchmarking, and rollback;
- an outbound Cloudflare Tunnel protected by Cloudflare Access.

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
DEFAULT_MODEL=kevinbellm-27b
```

The inference address is required to remain on loopback.

## RPC security boundary

Do not skip the RPC warning in the setup documents. llama.cpp describes its RPC
backend as experimental and insecure, and the backend has had critical
unauthenticated code-execution vulnerabilities. This project therefore:

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
- `systemd/cluster/` — hardened worker, tunnel, and coordinator templates.
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
