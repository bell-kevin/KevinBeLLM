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
                  Qwen3.8-27B Q5_K_S layer-split over an RTX 3060 12 GiB
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
loses about 11% of prefill and 7% of decode. Prefill remains the link-sensitive
part. The earlier IQ4_XS sweep found that raising `--ubatch-size` from 512 to
1024 reduced prefill from 470 to 458 tokens/second and 2048 exhausted VRAM. The
larger deployed Q5_K_S quant uses the separately tested, tighter batch 512 and
ubatch 128 configuration.

`CUDA_DEVICE_ORDER=PCI_BUS_ID` is set on the service because CUDA otherwise
orders devices fastest-first, which silently makes `CUDA0` the smaller 3070.

The server's deployed model is preset `27b-q5_k_s`:
`Qwen3.8-27B-UD-Q5_K_S.gguf`
from `unsloth/Qwen3.8-27B-GGUF`, pinned at immutable revision
`4ca720788d1e01f1bff70c033e0d0028fd02e502`, verified against its exact
18,665,753,504-byte size and
SHA-256 `d8d62ffcf84d42658dd6ccf9782b4d0404700af78b26d750507510c7597b5bfe`,
with mismatched files refused. Every model layer remains on the GPUs, split
`--tensor-split 67,33` across the two cards.

The download helper supports two checksum-pinned presets at that revision. The
deployed `27b-q5_k_s` preset is the measured everyday configuration;
`27b-iq4_xs` remains the smaller, checksum-verified rollback preset.

On the same original 14-case quality corpus and seed `424242`, IQ4_XS passed
13/14 (92.86%) and the stable all-GPU Q5_K_S configuration passed 14/14 (100%).
Q5_K_S gained the instruction-following case, lost none, and introduced no
regression in the protected calibration or long-context categories. This is a
useful same-hardware regression result, not a statistically broad estimate of
general intelligence.

The checked-in fixed-corpus throughput benchmark runs Qwen's official
non-thinking sampler (`temperature=0.7`, `top_p=0.8`, `top_k=20`, `min_p=0`,
`presence_penalty=1.5`, and `repeat_penalty=1`) with a fixed seed, one warm-up,
three measured repetitions, and exactly 128 output tokens per request.
The deployed Q5_K_S configuration has produced about 30-33 decode
tokens/second, while a 15,395-token prompt evaluated at 377.28 tokens/second.
These are observations from the deployment, not guarantees across output types
or other hosts.

For historical comparison, rebuilding llama.cpp for the FX-8370's actual
`bdver2` ISA (with its unavailable LWP instruction explicitly disabled) raised
the former IQ4_XS tool-schema path's median decode from 27.90 to 29.00
tokens/second (+4.0%). Its already GPU-sampled Fast path remained effectively
flat at 34.38 to 34.53 tokens/second (+0.4%), and its cold 14,879-token prompt
prefilled at about 466 tokens/second. Output hashes were identical across those
baseline and optimized IQ4_XS builds.

Speculative draft depth was separately swept over 1, 2, 3, 4, and 6; depth 2 is
the peak, while deeper drafts lose badly on this model. Raising the draft
probability threshold also reduced both sampling paths, so the service retains
`--spec-draft-n-max 2 --spec-draft-p-min 0` with Q5_K_S.

The earlier IQ4_XS cache probe measured about 1.6 s to first token for a short
prompt. On an 8.3k-token divergent prefix, `--ctx-checkpoints 8 --cache-ram
4096` restored 8,249 tokens and reduced its measured time to first token from
about 17.3 s to 1.8-1.9 s. The system prompt therefore stamps only the UTC
*date*; a per-request timestamp there would invalidate the reusable prefix on
every turn.

Run the same sequential Fast and tool-schema corpus through an SSH-forwarded
loopback endpoint with:

```bash
python3 scripts/cluster/benchmark-inference.py \
  --base-url http://127.0.0.1:18080
```

The harness rejects incomplete output and unexpected prompt-cache hits, reports
time to first token, prefill/decode rates, MTP acceptance, and output hashes, and
can emit JSON for before/after comparisons. Use `--help` for long-context and
explicit cache-reuse probes.

Throughput is not intelligence. Run the separate deterministic, exact-answer
quality corpus before and after a model or inference change:

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

The 14 original cases cover reasoning, coding, instruction following,
calibration, grounded tool use, and long-context retrieval. Two long-thinking
reasoning cases added on 2026-09-02, an integer-area triangle count and a
five-house logic grid, each need roughly 9,000 to 10,500 tokens of `xhigh`
thinking at seed `424242`, so the 16-case gate now exercises the Think ceiling
and the forced-answer reasoning budget that the shorter cases never reach; each
result records whether llama.cpp had to force the answer. The harness uses
the production `xhigh` thinking profile, output ceiling, and reasoning budget
by default, scores only the answer after its required `FINAL:` marker, and
refuses non-loopback endpoints. Its **Local
Quality Score** is a regression signal for this deployment, not an Artificial
Analysis Intelligence Index score.

The Q5_K_S deployment uses a `q4_0` K/V cache to retain a 32,768-token context
while keeping every model layer on the GPUs. After the full quality run, peak
memory was 11,872/12,288 MiB on the RTX 3060 and 7,353/8,192 MiB on the RTX
3070. That leaves little primary-GPU margin, which is why the measured batch,
ubatch, cache precision, and `67,33` split belong to this quant as one tested
configuration rather than independent knobs.

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

For daily Zoo Code use, install the per-user automatic forward once:

```powershell
.\scripts\windows\Install-KevinBeLLMAutoForward.ps1
```

It starts in the background at Windows logon, reconnects after network or sleep
interruptions, and stores no SSH password or Zoo API token. Check it with
`Get-KevinBeLLMAutoForwardStatus.ps1`; remove it with
`Uninstall-KevinBeLLMAutoForward.ps1`. The task runs without elevation and
references this checkout by absolute path, so rerun the installer if the
repository moves.

Then visit <http://127.0.0.1:3000>. Direct forwarding of the llama API is
available only as an explicit foreground diagnostics option:

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

## Zoo Code for VS Code

KevinBeLLM now exposes a Bearer-authenticated OpenAI Chat Completions adapter for
the official Zoo Code extension. A signed-in user re-enters their password to
create a named per-installation token, then configures Zoo Code's **OpenAI
Compatible** provider with the displayed Base URL, token, and model ID. Tokens
are shown once, stored only as digests on the server, expire, can be revoked
individually, and are revoked wholesale on password change.

The adapter authenticates both `/v1/models` and `/v1/chat/completions`, preserves
Zoo's native streamed tool calls, and reuses KevinBeLLM's model allowlist, GPU
queue, deadlines, response bounds, and per-user rate limit. It never exposes the
raw llama.cpp listener.

Follow [the complete Zoo Code guide](docs/ZOO_CODE.md) for Marketplace
installation, token creation, exact provider settings, the recommended SSH path,
the Cloudflare Service Auth path, rotation, and safe coding-agent permissions.
Never point Zoo Code at port 8080 or the diagnostic port 18080.

## Assistant behavior

Answers stream from llama.cpp as the model produces them, so visible text starts
at the model's time to first token instead of after the whole generation. The
terminating message carries the authoritative answer, so the live preview can
never diverge from the stored reply.

The opt-in `Fast` toggle disables live tools for that request and keeps sampling
on the GPUs. On the former IQ4_XS configuration, a fixed-seed 256-token probe
improved from 23.13 to 34.24 tokens/s (48%) with identical output. Leave it off
when an answer needs current web, news, weather, or model data; tool schemas
require a grammar, which llama.cpp currently samples on the CPU.

The Zoo Code gateway defaults omitted reasoning effort to Qwen's deepest
official `xhigh` tier, preserves assistant reasoning across agent turns, and
selects the GPU sampling path automatically for plain-text requests with no
active tool or JSON grammar, including requests that explicitly set
`tool_choice: "none"`. Native tool-call requests retain the grammar-capable CPU
sampling path. Zoo's `high` and `max` names map to `xhigh`; an explicit `none`
selects non-thinking mode.

With Fast off, the bounded tool loop can search the web and news, fetch a public
page, get current weather, and discover Hugging Face models. Its deployed limits
are 20 calls over 12 rounds (`MAX_TOOL_CALLS`, `MAX_TOOL_ROUNDS`), set above the
6-11 calls measured on representative research questions so a harder one is not
cut off mid-investigation; a question that finishes sooner pays nothing for the
headroom. Tool use sends queries or URLs to public upstream
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

Qwen3.8's extended thinking is **on** by default and uses its deepest official
`xhigh` effort. Artificial Analysis reports an Intelligence Index of
[35 in non-reasoning mode](https://artificialanalysis.ai/models/qwen3-8-27b-non-reasoning)
and [52 at `xhigh`](https://artificialanalysis.ai/models/qwen3-8-27b) for its
hosted Qwen3.8-27B configurations. Those are provider benchmark results, **not**
scores for this local Q5_K_S deployment or its IQ4_XS fallback; only the local
quality harness can compare these deployment configurations.

The browser sends Qwen's official thinking sampler (`temperature=1`,
`top_p=0.95`, `top_k=20`, `min_p=0`, `presence_penalty=0`, and
`repeat_penalty=1`) with explicit `xhigh` effort and preserved thinking. The
`Think` toggle selects the separate official non-thinking sampler documented
above for answer-first latency, and `DEFAULT_REASONING=false` restores opt-in
thinking for every browser client at once. The standalone server itself starts
with `--reasoning-effort xhigh --reasoning-preserve` so the default is explicit.

When thinking is on, its text streams into a collapsible block above the answer.
The current trace is fed into later rounds of the bounded tool loop; after the
answer, at most 12,000 characters are retained in the signed-in browser's
in-memory conversation and may be sent with a later turn. It is never written
to the account database, but preserved reasoning does share the model's context
budget.

Each non-Think llama.cpp completion is capped at 4,096 output tokens and each
Think completion at 28,672 (`ANSWER_MAX_TOKENS` and `REASONING_MAX_TOKENS`).
These are ceilings, not targets: the model stops at its own stop token, so a
larger budget costs nothing on answers that never reach it and only removes
mid-sentence truncation on long code and multi-part analysis. A tool-enabled
browser request can invoke several completions across the bounded loop. The
llama.cpp context is 49,152 tokens, which the 28,672-token Think ceiling shares
with the prompt, leaving 20,480 tokens of prompt room.
Typical requests fit, but the app bounds browser history by 48,000 characters
rather than pre-tokenizing it, so an unusually token-dense history is not
guaranteed to fit the model context.

The Think ceiling was raised from 12,288 after measuring the deployed Q5_K_S
model at `xhigh` on 2026-09-02: an ordinary "write a function and ten tests"
request generated 12,999 tokens, about 12,200 of them reasoning, before its
correct answer, and a harder counting problem exhausted 12,288 tokens while
still thinking and returned empty content. The context was then raised from
32,768 to 49,152 tokens, which the hybrid model's small KV cache makes cheap,
so the ceiling could grow past 20,480 without shrinking the prompt room below
its former 20,480. Every Think request also
carries a per-request reasoning budget (`REASONING_BUDGET_TOKENS`, default
`REASONING_MAX_TOKENS` minus `ANSWER_MAX_TOKENS`) and a budget message
(`REASONING_BUDGET_MESSAGE`). llama.cpp counts only thinking tokens against the
budget; when it runs out it injects the message, closes the thinking block, and
lets the model write the answer with the remaining allowance, so a hard question
ends in a best-effort answer rather than nothing. The standalone unit leaves
`--reasoning-budget` unrestricted, which is what makes llama.cpp honor the
per-request fields.

## Project layout

- `services/assistant-web/` — authenticated FastAPI assistant with a llama.cpp
  backend adapter and a Bearer-authenticated Zoo Code compatibility gateway.
- `services/live-tools/` — small read-only FastAPI tool service: Open-Meteo
  weather and forecast, and Hugging Face model discovery. It has no shell,
  filesystem, model-download, email, or account tools.
- `scripts/` — server lifecycle helpers: setup, start, status, doctor, stop,
  autostart installation, and container-engine selection.
- `scripts/cluster/` — Ubuntu preparation, SSH hardening, pinned builds, model
  download, service installation, status checks, and GPU lighting control.
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
