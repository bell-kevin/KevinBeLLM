# KevinBeLLM on two Ampere GPUs

This repository adapts [ASUS-KevinBeLLM](https://github.com/bell-kevin/ASUS-KevinBeLLM)
for two Ethernet-connected Ubuntu desktops and a Windows administration laptop:

```text
Windows laptop
  browser -> SSH local forward -> Machine A (RTX 3060 12 GiB)
                                  KevinBeLLM + llama-server
                                             |
                                             | restricted SSH tunnel
                                             v
                                  Machine B (RTX 3070 8 GiB)
                                  loopback-only llama.cpp RPC worker
```

Machine A is the coordinator. Machine B is the worker. The laptop may sleep or
disconnect without stopping inference. All application, llama.cpp, and RPC
ports stay on loopback; SSH is the only LAN-facing service.

The implementation is prepared, but it is not yet deployed to the desktops.
Their operating systems, usernames, wired addresses, host keys, and encryption
boot flow must first be confirmed at their physical consoles.

## Start here

Follow the staged [two-node setup guide](docs/TWO_NODE_SETUP.md). It covers:

- physical inventory and full-disk-encryption behavior;
- stable router DHCP reservations;
- fingerprint-verified, key-only SSH from Windows;
- the pinned CUDA/llama.cpp build on both machines;
- the restricted A-to-B tunnel and systemd user services;
- the checksum-pinned 27B model;
- KevinBeLLM startup, validation, benchmarking, and rollback.

The short reference for the checked-in helpers and private environment files is
in [infra/cluster/README.md](infra/cluster/README.md).

Do not skip the RPC warning in those documents. llama.cpp describes its RPC
backend as experimental and insecure, and it has had critical unauthenticated
code-execution bugs. This project therefore binds the worker to
`127.0.0.1:50052`, binds A's tunnel endpoint to `127.0.0.1:50053`, requires a
restricted SSH key, and refuses to start until the risk is explicitly
acknowledged. Never expose either port to the LAN or router.

## What the hardware can realistically run

The two cards provide 20 GiB of *nominal, separate* VRAM. They do not become one
20 GiB CUDA allocation, and their 360/448 GB/s memory buses do not add together.
CUDA contexts, compute buffers, KV cache, and display use also consume VRAM.

The first target is the pinned Qwen3.5-27B Q4_K_M GGUF (17,984,872,928 bytes),
with one request slot and a 4K context for the first load. After measuring
headroom, try 8K. The original roughly 24 GB 35B Ollama artifact will not fit
entirely in the nominal 20 GiB pool before runtime overhead.

For smaller models, running one independent server per GPU will usually give
better aggregate throughput than crossing the network. If the existing
motherboard, case, cooling, PCIe layout, and PSU safely support both cards,
putting both GPUs in one desktop is the faster and safer no-purchase pooling
option; benchmark that only after checking the physical constraints.

## Normal operation after deployment

After a cold boot, physically power on and unlock Machine B, then Machine A.
Full-disk encryption prevents ordinary SSH and systemd services from starting
until the disk passphrase has been entered. Once unlocked, systemd lingering
starts the worker, tunnel, coordinator, and application without a graphical
login.

From this Windows checkout, open the private UI tunnel:

```powershell
.\scripts\windows\Open-KevinBeLLMForward.ps1
```

Then browse to <http://127.0.0.1:3000>. Direct llama API forwarding is opt-in
for diagnostics:

```powershell
.\scripts\windows\Open-KevinBeLLMForward.ps1 -ForwardLlamaApi
```

On Machine A, the familiar lifecycle remains:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/doctor.sh
./scripts/stop.sh
```

The application now supports either backend:

```dotenv
# Two-node default
INFERENCE_BACKEND=llamacpp
INFERENCE_BASE_URL=http://127.0.0.1:8080
DEFAULT_MODEL=kevinbellm-27b

# Original single-host fallback
INFERENCE_BACKEND=ollama
INFERENCE_BASE_URL=http://127.0.0.1:11434
```

Both inference URLs are required to be loopback addresses.

## Project layout

- `services/assistant-web/` — authenticated FastAPI browser assistant, now with
  Ollama and llama.cpp OpenAI-compatible adapters.
- `scripts/cluster/` — Ubuntu preparation, SSH hardening, pinned builds, model
  download, tunnel setup, service installation, and status checks.
- `systemd/cluster/` — hardened worker, tunnel, and coordinator templates.
- `scripts/windows/` — laptop SSH aliases/key bootstrap and UI forwarding.
- `infra/cluster/` — non-secret examples; active `.env` files here are ignored.
- `docs/TWO_NODE_SETUP.md` — authoritative end-to-end procedure.

The existing login, Argon2 password hashing, hashed sessions, CSRF/origin
checks, bounded tool loop, SearXNG integration, live-data tools, and rootless
container layout are retained from the original project.

## License and source publication

The project is licensed `AGPL-3.0-or-later`; see [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Models, llama.cpp, hosted
services, and other dependencies retain their own licenses.

If this modified application is offered over a network outside private testing,
publish the corresponding modified source and set `SOURCE_URL` to that exact
public repository before advertising the service. Never commit private `.env`
files, SSH keys, tunnel credentials, model files, account data, or chat exports.
