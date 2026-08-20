<a name="readme-top"></a>

# KevinBeLLM: private local AI

https://bell-kevin.github.io/KevinBeLLM/

https://assistant.kevin-bell.com/

KevinBeLLM is an AGPL-licensed, self-hosted AI workspace served from owned
Ubuntu hardware. The everyday architecture is deliberately simple:

```text
Remote browser
  -> Cloudflare Access
  -> outbound-only Cloudflare Tunnel
  -> Server host: KevinBeLLM login + app + standalone llama-server
                  Qwen3.8-27B IQ4_XS layer-split over an RTX 3060 12 GiB
                  and an RTX 3070 8 GiB in the same host
```

Both GPUs sit in the server: the RTX 3070 occupies its PCIEX1_1 slot on a riser,
so the everyday model spans the two cards without a network hop. The app and
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
| Server, primary | NVIDIA GeForce RTX 3060 | 12 GiB | PCIe 2.0 x16 | App, and layers 0-n of the everyday model |
| Server, secondary | NVIDIA GeForce RTX 3070 | 8 GiB | PCIe 2.0 x1 riser | Remaining layers of the same model |

Both GPUs are Ampere devices with CUDA compute capability 8.6, so they share the
same pinned llama.cpp source revision and CUDA build configuration. Their 20 GiB
of nominal VRAM is about 19.3 GiB usable by llama.cpp and remains two separate
memory pools: capacity adds, bandwidth does not, and CUDA contexts, compute
buffers, KV cache, and display use consume part of each card.

The x1 riser costs far less than it looks like it should. Layer splitting moves
only one hidden-state vector per boundary, so against the 3070 alone the split
loses about 11% of prefill and 7% of decode. Prefill is the part that is
link-sensitive: raising `--ubatch-size` from 512 to 1024 sends twice as much
across the x1 link per crossing and makes prefill *worse* (470 to 458
tokens/second), and 2048 exhausts VRAM outright. 512 is the tuned value.

`CUDA_DEVICE_ORDER=PCI_BUS_ID` is set on the service because CUDA otherwise
orders devices fastest-first, which silently makes `CUDA0` the smaller 3070.

The server's deployed model is preset `27b-iq4_xs`: `Qwen3.8-27B-UD-IQ4_XS.gguf`
from `unsloth/Qwen3.8-27B-GGUF`, pinned at immutable revision
`4ca720788d1e01f1bff70c033e0d0028fd02e502`, verified against its exact
14,252,845,984-byte size and SHA-256, with mismatched files refused. It occupies
13.27 GiB and is layer-split `--tensor-split 64,36` across the two cards.

At the application's current temperature of 0.3, the tuning corpus on the normal
tool-capable sampling path generated roughly 22-27 tokens/second, with a median
near 24. The spread follows how predictable the output is rather than how long
it is: code drafts well at about 26 tokens/second while discursive prose and
factual answers sit near 23. Fast mode uses a different sampling path and is
benchmarked below. Speculative draft depth was swept over 1, 2, 3, 4, and 6;
depth 2 is the peak at 24.3 and depth 6 collapses to 17.6. Prefill runs at about
595 tokens/second on a short prompt and 470 at 8k. These are measurements from
this deployment, not guarantees or a benchmark harness shipped with the source.

Time to first token is about 1.6 s for a short prompt. A cold 8.4k-token prompt
costs about 18 s, but that is a worst case rather than the usual one: llama.cpp
reuses the cached prefix, so the next turn of the same conversation starts in
about 3.8 s and an unchanged prompt in about 1.6 s. The system prompt therefore
stamps only the UTC *date*; a per-request timestamp there would invalidate the
whole prefix on every turn and silently reintroduce the cold cost.

The KV cache is `q8_0` rather than `f16`. This model spends 0.25 MiB per token of
KV, so 32,768 tokens of `f16` would need 8 GiB on top of the weights and does not
fit; `q8_0` halves that. At a full 32k context the peak measured use is 9,234
MiB of the 3060 and 7,029 MiB of the 3070, leaving roughly 2.6 GiB and 0.8 GiB
free. Results are local measurements, not a guarantee for other systems.

## Cold boots and remote availability

The server uses full-disk encryption. After power loss or shutdown, someone must
physically switch on the server and enter its LUKS passphrase; Wake-on-LAN and
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
- the standalone llama.cpp systemd user service;
- checksum-verified model installation;
- KevinBeLLM startup, validation, benchmarking, and rollback;
- an outbound Cloudflare Tunnel protected by Cloudflare Access.

The checked-in cluster helpers and ignored private environment files are
documented in [infra/cluster/README.md](infra/cluster/README.md).

For private LAN maintenance from the Windows administration laptop:

```powershell
ssh kevinbellm-a
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

On the server, the application lifecycle is:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/doctor.sh
./scripts/stop.sh
```

The deployed application uses the local llama.cpp OpenAI-compatible endpoint:

```dotenv
INFERENCE_BASE_URL=http://127.0.0.1:8080
DEFAULT_MODEL=kevinbellm-27b
PREFERRED_MODELS=kevinbellm-27b
```

The inference address is required to remain on loopback.

## Assistant behavior

Answers stream from llama.cpp as the model produces them, so visible text starts
at the model's time to first token instead of after the whole generation. The
terminating message carries the authoritative answer, so the live preview can
never diverge from the stored reply.

The opt-in `Fast` toggle disables live tools for that request and keeps sampling
on the GPUs. On the deployed host, a fixed-seed 256-token probe improved from
23.13 to 34.24 tokens/s (48%) with identical output. Leave it off when an answer
needs current web, news, weather, or model data; tool schemas require a grammar,
which llama.cpp currently samples on the CPU.

With Fast off, the bounded tool loop can search the web and news, fetch a public
page, get current weather, and discover Hugging Face models. Its deployed limits
are 12 calls over 8 rounds. Tool use sends queries or URLs to public upstream
services, then feeds their returned data into local inference. Conversation
history is retained only in the signed-in browser's memory; a bounded history is
sent with each chat request, and the app does not persist transcripts in its
database.

Finished answers render as Markdown in the browser. The model may cite a tool's
public URL inline when it relies on that source; the app does not mechanically
append a `Sources` list to the answer. Retrieved links also appear in a separate
citation card. The renderer is a deferred script that degrades safely: if it
fails to load, answers remain readable as the plain streamed text and the
citation card is dropped rather than rendered through an unvetted URL check.

Qwen3.8's extended thinking is off by default and enabled per request with the
`Think` toggle, which is slower to start and better on hard questions. When it
is on, the thinking text streams into a collapsible block above the answer.
Each non-Think llama.cpp completion is capped at 2,048 output tokens and each
Think completion at 8,192. A tool-enabled browser request can invoke several
completions across the bounded loop. The llama.cpp context is 32,768 tokens.
Typical requests fit, but the app bounds browser history by 48,000 characters
rather than pre-tokenizing it, so an unusually token-dense history is not
guaranteed to fit the model context.

## Project layout

- `services/assistant-web/` — authenticated FastAPI assistant with a llama.cpp
  OpenAI-compatible backend adapter.
- `services/live-tools/` — small read-only FastAPI tool service: Open-Meteo
  weather and forecast, and Hugging Face model discovery. It has no shell,
  filesystem, model-download, email, or account tools.
- `scripts/` — server lifecycle helpers: setup, start, status, doctor, stop,
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
- `node --check` for the browser scripts and `node --test` for the Markdown
  renderer;
- `bash -n` over every tracked shell script, and a PowerShell parse check over
  the Windows administration scripts;
- `scripts/check-standalone-contract.sh`, which pins the standalone unit's
  inference settings, local-only build, and loopback endpoint so retuning
  inference fails the build until the expected values are updated deliberately;
- `scripts/check-public-tree.sh`, which fails if a private runtime file, model,
  database, key, host-trust file, or tunnel-token-shaped credential is ever
  tracked.

The full CI definition is [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
CI runs on Ubuntu 24.04. To reproduce every check from the repository root, use
Ubuntu 24.04 or an equivalent environment with Python 3.13, Node.js, Bash and
standard POSIX utilities, PowerShell, Git, and `systemd-analyze`. Install the
locked Python test dependencies, then run:

```bash
python -m pip install --require-hashes -r services/assistant-web/requirements-dev.lock
(cd services/assistant-web && python -m pytest -q)
git ls-files -z '*.sh' | xargs -0 -n1 bash -n
node --check services/assistant-web/static/app.js
node --check services/assistant-web/static/login.js
node --check services/assistant-web/static/markdown.js
node --test services/assistant-web/tests/markdown.test.mjs
./scripts/check-standalone-contract.sh
./scripts/check-public-tree.sh
```

Run the same PowerShell parser check used by CI:

```powershell
$failed = $false
Get-ChildItem scripts/windows -Filter *.ps1 | ForEach-Object {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile(
    $_.FullName,
    [ref]$tokens,
    [ref]$errors
  )
  if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_.Message }
    $failed = $true
  }
}
if ($failed) { exit 1 }
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
