<a name="readme-top"></a>

# KevinBeLLM: private local AI

https://bell-kevin.github.io/KevinBeLLM/

https://assistant.kevin-bell.com/

KevinBeLLM is an AGPL-licensed, self-hosted AI workspace served from owned
Ubuntu hardware. This repository continues the original proof of concept at
its existing public URL. The everyday architecture is deliberately simple:

```text
Remote browser
  -> Cloudflare Access
  -> outbound-only Cloudflare Tunnel
  -> Machine A: KevinBeLLM login + app + standalone llama-server
                Qwen3.8-27B IQ4_XS layer-split over an RTX 3060 12 GiB
                and an RTX 3070 8 GiB in the same host
```

Both GPUs sit in the one host: the RTX 3070 occupies its PCIEX1_1 slot on a
riser, so the everyday model spans the two cards without a network hop. There is
no second machine, and the service starts no llama.cpp RPC process, tunnel, or
listener. The app and
model API are not published directly to the LAN or Internet; SSH is the only
LAN-facing administration service, and remote web traffic arrives through an
outbound Cloudflare Tunnel.

The public [GitHub Pages site](https://bell-kevin.github.io/KevinBeLLM/) is only a static project landing
page. It has no login form, runtime probe, model-health indicator, analytics, or
private configuration. Authorized users enter the actual assistant at
<https://assistant.kevin-bell.com/>, first through Cloudflare Access and then
through KevinBeLLM's own login.

## Hardware and model

| Role | GPU | Nominal VRAM | Link | Workload |
| --- | --- | ---: | --- | --- |
| Machine A, primary | NVIDIA GeForce RTX 3060 | 12 GiB | PCIe 2.0 x16 | App, and layers 0-n of the everyday model |
| Machine A, secondary | NVIDIA GeForce RTX 3070 | 8 GiB | PCIe 2.0 x1 riser | Remaining layers of the same model |

Both GPUs are Ampere devices with CUDA compute capability 8.6, so they share the
same pinned llama.cpp source revision and CUDA build configuration. Their 20 GiB
of nominal VRAM (19,753 MiB as llama.cpp counts it) remains two separate memory
pools: capacity adds, bandwidth does not, and CUDA contexts, compute buffers, KV
cache, and display use consume part of each card.

The x1 riser costs far less than it looks like it should. Layer splitting moves
only one hidden-state vector per boundary, so against the 3070 alone the split
loses about 11% of prefill and 7% of decode. Prefill is the part that is
link-sensitive: raising `--ubatch-size` from 512 to 1024 sends twice as much
across the x1 link per crossing and makes prefill *worse* (494 to 458
tokens/second), and 2048 exhausts VRAM outright. 512 is the tuned value.

`CUDA_DEVICE_ORDER=PCI_BUS_ID` is set on the service because CUDA otherwise
orders devices fastest-first, which silently makes `CUDA0` the smaller 3070.

Machine A's deployed model is preset `27b-iq4_xs`: `Qwen3.8-27B-UD-IQ4_XS.gguf`
from `unsloth/Qwen3.8-27B-GGUF`, pinned at immutable revision
`4ca720788d1e01f1bff70c033e0d0028fd02e502`, verified against its exact
14,252,845,984-byte size and SHA-256, with mismatched files refused. It occupies
13.26 GiB and is layer-split `--tensor-split 64,36` across the two cards.

At the deployed sampling temperature the tuned MTP configuration generates
roughly 22-27 tokens/second, with a median near 24. As with the 9B, the spread
follows how predictable the output is rather than how long it is: code drafts
well at about 26 tokens/second while discursive prose and factual answers sit
near 23. Speculative draft depth was swept over 1, 2, 3, 4, and 6; depth 2 is the
peak at 24.3 and depth 6 collapses to 17.6. Prefill runs at about 595
tokens/second on a short prompt and 500 at 8k.

Time to first token is about 1.6 s for a short prompt. A cold 8.4k-token prompt
costs about 18 s, but that is a worst case rather than the usual one: llama.cpp
reuses the cached prefix, so the next turn of the same conversation starts in
about 3.8 s and an unchanged prompt in about 1.6 s. The system prompt therefore
stamps only the UTC *date*; a per-request timestamp there would invalidate the
whole prefix on every turn and silently reintroduce the cold cost.

The KV cache is `q8_0` rather than `f16`. This model spends 0.25 MiB per token of
KV, so 32,768 tokens of `f16` would need 8 GiB on top of the weights and does not
fit; `q8_0` halves that. At a full 32k context the peak measured use is 9,234
MiB of the 3060 and 7,029 MiB of the 3070, leaving roughly 3.0 GiB and 0.8 GiB
free. Results are local measurements, not a guarantee for other systems.

Preset `9b-q6_k` (`Qwen_Qwen3.5-9B-Q6_K.gguf`, revision
`182be2fd6c7bc44887d88a91cb03ff009cc9f549`, 7,958,818,848 bytes) remains the
installer default and the documented fallback. It runs on the 3060 alone at
roughly 53-69 tokens/second, median near 62, with a 1,350 tokens/second prefill —
far faster than the 27B, and far less capable.

## Cold boots and remote availability

Both hosts use full-disk encryption. After power loss or shutdown, someone must
physically power on the machine and enter its LUKS passphrase; Wake-on-LAN and
SSH cannot bypass that pre-boot step. Once it is unlocked, enabled user services
can restore inference, the app, and the outbound connector without a graphical
login.

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

## Assistant behavior

Answers stream from llama.cpp as the model produces them, so first visible text
arrives in about 0.25 s instead of after the whole generation. The terminating
message carries the authoritative answer, so the live preview can never diverge
from the stored reply.

Finished answers render as Markdown in the browser, and retrieved sources appear
as a separate citation card rather than as URLs appended to the answer text. The
renderer is a deferred script that degrades safely: if it fails to load, answers
remain readable as the plain streamed text and the citation card is dropped
rather than rendered through an unvetted URL check.

Qwen3.8's extended thinking is off by default and enabled per request with the
`Think` toggle, which is slower to start and better on hard questions. When it
is on, the thinking text streams into a collapsible block above the answer. Both
modes fit the 32,768-token context.

## Project layout

- `services/assistant-web/` — authenticated FastAPI assistant with a llama.cpp
  OpenAI-compatible backend adapter.
- `services/live-tools/` — small read-only FastAPI tool service: Open-Meteo
  weather and forecast, and Hugging Face model discovery. It has no shell,
  filesystem, model-download, email, or account tools.
- `scripts/` — Machine A lifecycle helpers: setup, start, status, doctor, stop,
  autostart installation, and container-engine selection.
- `scripts/cluster/` — Ubuntu preparation, SSH hardening, pinned builds, model
  download, service installation, and status checks.
- `systemd/cluster/` — the hardened inference unit template.
- `scripts/windows/` — administration-laptop SSH setup and private forwarding.
- `infra/cluster/` — non-secret examples; active environment files are ignored.
- `infra/search/` — loopback-only SearXNG built from a pinned image digest, used
  both as a browser UI and as the model's search tool.
- `infra/cloudflare/` — the named-tunnel adapter for authenticated remote
  access; its token file and environment are ignored.
- `docs/` — source for the static
  [GitHub Pages landing site](https://bell-kevin.github.io/KevinBeLLM/).

KevinBeLLM retains Argon2 password hashing, hashed sessions, CSRF and origin
checks, a bounded tool loop, SearXNG integration, live-data tools, and a
rootless-container layout. Cloudflare Access supplements this application
login; it does not replace it.

## Tests and checks

GitHub Actions runs on pull requests and on pushes to `main`, with pinned action
digests and no persisted credentials. It installs the assistant's locked test
dependencies with `--require-hashes` and then runs:

- `pytest` for the assistant service;
- `node --test` for the browser Markdown renderer;
- `bash -n` over every tracked shell script, and a PowerShell parse check over
  the Windows administration scripts;
- `scripts/check-standalone-contract.sh`, which pins the standalone unit's
  inference settings and its no-RPC security invariants, so retuning inference
  fails the build until the expected values are updated deliberately, and which
  also fails if that unit ever gains a second-host argument or dependency;
- `scripts/check-public-tree.sh`, which fails if a private runtime file, model,
  database, key, host-trust file, or tunnel-token-shaped credential is ever
  tracked.

The same checks run locally from the repository root. The Python tests need the
locked development dependencies in the active environment; the other four need
nothing installed:

```bash
python -m pip install --require-hashes -r services/assistant-web/requirements-dev.lock
(cd services/assistant-web && python -m pytest -q)
node --test services/assistant-web/tests/markdown.test.mjs
./scripts/check-standalone-contract.sh
./scripts/check-public-tree.sh
```

## License and public source

The project is licensed `AGPL-3.0-or-later`; see [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Models, llama.cpp, Cloudflare,
and other dependencies retain their own licenses and terms.

When the modified application is offered over a network, publish the
corresponding source and set `SOURCE_URL` to this exact public repository.
Never commit private environment files, passwords, SSH keys, Cloudflare tunnel
credentials, model files, account databases, logs, or chat exports.

https://bell-kevin.github.io/KevinBeLLM/

https://assistant.kevin-bell.com/

<p align="left"><a href="#readme-top">back to top</a></p>
