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
                Qwen3.5-9B Q6_K fully on the RTX 3060 12 GiB
```

Machine B's RTX 3070 remains available for maintenance, experiments, the
optional document-retrieval profile, or the explicitly optional 27B two-node
profile. Normal service does not depend on
Machine B and starts no llama.cpp RPC process, tunnel, or listener. The app and
model API are not published directly to the LAN or Internet; SSH is the only
LAN-facing administration service, and remote web traffic arrives through an
outbound Cloudflare Tunnel.

The public [GitHub Pages site](https://bell-kevin.github.io/KevinBeLLM/) is only a static project landing
page. It has no login form, runtime probe, model-health indicator, analytics, or
private configuration. Authorized users enter the actual assistant at
<https://assistant.kevin-bell.com/>, first through Cloudflare Access and then
through KevinBeLLM's own login.

## Hardware and model

| Role | GPU | Nominal VRAM | Workload |
| --- | --- | ---: | --- |
| Machine A, primary | NVIDIA GeForce RTX 3060 | 12 GiB | App and fully GPU-resident everyday model |
| Machine B, optional | NVIDIA GeForce RTX 3070 | 8 GiB | Maintenance, optional document retrieval, and deliberate two-node experiments |

Both GPUs are Ampere devices with CUDA compute capability 8.6, so they use the
same pinned llama.cpp source revision and CUDA build configuration. Their 20
GiB of nominal VRAM remains two
separate memory pools: bandwidth does not add together, and CUDA contexts,
compute buffers, KV cache, and display use consume part of each card.

The default model installer pins `Qwen_Qwen3.5-9B-Q6_K.gguf` at immutable
revision `182be2fd6c7bc44887d88a91cb03ff009cc9f549`, verifies its exact
7,958,818,848-byte size and SHA-256, and refuses mismatched files. On Machine A,
the tuned MTP configuration generates roughly 53-69 tokens/second at the
deployed sampling temperature, with a median near 62. What sets that spread is
how predictable the output is, not how long it is: code and arithmetic draft
well and reach 68-69 tokens/second, while discursive technical prose and
creative writing fall to 53-57. Longer answers are not slower, and longer
prompts are nearly free out to about 7,500 tokens. The configuration retains
about 3.2 GiB of free
VRAM at a 32,768-token context, and passes a coherent OpenAI-compatible
tool-call check. Time to first token is about 0.3 s for a short prompt and
scales with prompt length at a prefill rate of roughly 1,350 tokens/second.
Results are local measurements, not a guarantee for other systems.

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

The same guide covers the optional [Machine B document-retrieval
profile](infra/cluster/README.md#optional-machine-b-document-retrieval), which
gives the RTX 3070 a job of its own rather than a share of Machine A's job.

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

Qwen3.5's extended thinking is off by default and enabled per request with the
`Think` toggle, which is slower to start and better on hard questions. When it
is on, the thinking text streams into a collapsible block above the answer. Both
modes fit the 32,768-token context.

## Optional private document search

Machine B's RTX 3070 can host a `search_documents` tool over an index of your
own files: bge-m3 embeddings, dense search, and bge-reranker-v2-m3 reranking all
run there, and Machine A issues one HTTP request per tool call over a loopback
SSH forward. It uses no llama.cpp RPC.

The feature is off unless `DOC_RETRIEVAL_URL` is set. While it is off the
assistant advertises no document tool and sends a byte-identical system prompt,
so a deployment without Machine B pays nothing for a capability it does not
have. While it is on, the added cost to Machine A is about 150 prompt tokens
per turn — roughly 0.1 s of prefill — plus one bounded round trip when the model
actually calls the tool. Generation speed is unchanged either way.

Because Machine B may sit powered off behind full-disk encryption for days, an
unreachable worker is capped at a 2 s connect budget, and three consecutive
failures stop the calls entirely for a 120 s cooldown. A retrieval failure is an
ordinary tool error: the model answers from other sources and says local
documents were not searched.

Local files have no public URL, so document results are cited inline by name and
contribute nothing to the browser's citation card, which only ever renders vetted
public `http(s)` links. Setup lives in
[infra/cluster/README.md](infra/cluster/README.md#optional-machine-b-document-retrieval).

## Project layout

- `services/assistant-web/` — authenticated FastAPI assistant with a llama.cpp
  OpenAI-compatible backend adapter.
- `services/live-tools/` — small read-only FastAPI tool service: Open-Meteo
  weather and forecast, and Hugging Face model discovery. It has no shell,
  filesystem, model-download, email, or account tools.
- `services/doc-retrieval/` — optional Machine B service: dense retrieval over
  your own documents, with bge-m3 embeddings and bge-reranker-v2-m3 reranking on
  the RTX 3070. Machine A never installs or runs it.
- `scripts/` — Machine A lifecycle helpers: setup, start, status, doctor, stop,
  autostart installation, and container-engine selection.
- `scripts/cluster/` — Ubuntu preparation, SSH hardening, pinned builds, model
  download, tunnel setup, service installation, and status checks.
- `systemd/cluster/` — hardened standalone, optional worker, tunnel, and
  coordinator templates.
- `scripts/windows/` — administration-laptop SSH setup and private forwarding.
- `infra/cluster/` — non-secret examples; active environment files are ignored.
- `infra/search/` — loopback-only SearXNG built from a pinned image digest, used
  both as a browser UI and as the model's search tool.
- `infra/cloudflare/` — the named-tunnel adapter for authenticated remote
  access; its token file and environment are ignored.
- `docs/` — source for the static
  [GitHub Pages landing site](https://bell-kevin.github.io/KevinBeLLM/) and the
  optional two-node setup guide.

KevinBeLLM retains Argon2 password hashing, hashed sessions, CSRF and origin
checks, a bounded tool loop, SearXNG integration, live-data tools, and a
rootless-container layout. Cloudflare Access supplements this application
login; it does not replace it.

## Tests and checks

GitHub Actions runs on pull requests and on pushes to `main`, with pinned action
digests and no persisted credentials. It installs the assistant's locked test
dependencies with `--require-hashes` and then runs:

- `pytest` for the assistant service;
- `pytest` for the optional Machine B retrieval service, against Python 3.12 to
  match Ubuntu 24.04 and its hash-pinned lock;
- `node --test` for the browser Markdown renderer;
- `bash -n` over every tracked shell script, and a PowerShell parse check over
  the Windows administration scripts;
- `scripts/check-standalone-contract.sh`, which pins the standalone unit's
  inference settings and its no-RPC security invariants, so retuning inference
  fails the build until the expected values are updated deliberately, and which
  also fails if that unit ever gains a retrieval argument or dependency;
- `scripts/check-retrieval-contract.sh`, which pins the optional retrieval
  profile: loopback-only endpoints, no RPC, pinned model digests, an SSH key
  restricted to one forward, the application default staying off, and no
  dependency that could make Machine A wait on Machine B;
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
./scripts/check-retrieval-contract.sh
./scripts/check-public-tree.sh
```

The optional retrieval suite uses its own environment, because its lock targets
Machine B's Python version rather than the assistant's:

```bash
/usr/bin/python3 -m venv .venv-doc-retrieval
.venv-doc-retrieval/bin/python -m pip install --require-hashes \
  -r services/doc-retrieval/requirements-dev.lock
(cd services/doc-retrieval && ../../.venv-doc-retrieval/bin/python -m pytest -q)
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
