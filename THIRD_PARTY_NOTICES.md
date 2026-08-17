# Third-party notices

The original source, configuration, scripts, and documentation in this
repository are licensed under `AGPL-3.0-or-later`. The following independent
programs, model weights, data, and hosted services retain their own terms. They
are not relicensed by this repository.

The table inventories the standalone default and optional two-node deployment.
The application retains an explicit legacy Ollama API adapter, but this
repository neither installs nor pins an Ollama runtime or Ollama-hosted model
artifact for the new deployment.

## Software and model components

| Component | Pinned/recommended version | Upstream license | Source |
|---|---|---|---|
| llama.cpp | `b10451` | MIT | <https://github.com/ggml-org/llama.cpp/tree/b10451> |
| Qwen3.5 9B weights | official revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` | Apache-2.0 | <https://huggingface.co/Qwen/Qwen3.5-9B/tree/c202236235762e1c871ad0ccb60c8ee5ba337b9a> |
| Qwen3.5 9B Q6_K GGUF | `bartowski/Qwen_Qwen3.5-9B-GGUF` revision `182be2fd6c7bc44887d88a91cb03ff009cc9f549` | Apache-2.0 | <https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF/tree/182be2fd6c7bc44887d88a91cb03ff009cc9f549> |
| Qwen3.5 27B weights | official base | Apache-2.0 | <https://huggingface.co/Qwen/Qwen3.5-27B> |
| Qwen3.5 27B Q4_K_M GGUF | `bartowski/Qwen_Qwen3.5-27B-GGUF` revision `d7b113c40283f4d99f4eb0ec20d126ad653cc736` | Apache-2.0 | <https://huggingface.co/bartowski/Qwen_Qwen3.5-27B-GGUF/tree/d7b113c40283f4d99f4eb0ec20d126ad653cc736> |
| bge-m3 Q8_0 GGUF (optional retrieval) | `gpustack/bge-m3-GGUF` revision `2d48f1737679ad900d5c26c5aad5410e9c70fdca` | MIT | <https://huggingface.co/gpustack/bge-m3-GGUF/tree/2d48f1737679ad900d5c26c5aad5410e9c70fdca> |
| bge-reranker-v2-m3 Q8_0 GGUF (optional retrieval) | `gpustack/bge-reranker-v2-m3-GGUF` revision `3093af03b1a635e67b084b1d8c03c5f5e020fd05` | Apache-2.0 | <https://huggingface.co/gpustack/bge-reranker-v2-m3-GGUF/tree/3093af03b1a635e67b084b1d8c03c5f5e020fd05> |
| SearXNG | commit `c01178d03` | AGPL-3.0-or-later | <https://github.com/searxng/searxng/tree/c01178d03> |
| hyper-h2 | 4.4.1 security overlay | MIT | <https://github.com/python-hyper/h2/tree/v4.4.1> |
| cloudflared client | 2026.7.3 | Apache-2.0 | <https://github.com/cloudflare/cloudflared/tree/2026.7.3> |
| Podman | Ubuntu 24.04 package 4.9.3 | Apache-2.0 | <https://github.com/containers/podman> |
| podman-compose | 1.6.0 (1.4.1 minimum) | GPL-2.0-only | <https://github.com/containers/podman-compose> |
| Python | 3.13.15 (assistant), 3.12.13 (tools) | PSF-2.0 | <https://www.python.org/> |
| pip | 26.2.1 (live-tools build bootstrap) | MIT | <https://github.com/pypa/pip/tree/26.2.1> |
| FastAPI | 0.141.1 | MIT | <https://github.com/fastapi/fastapi> |
| Starlette | 1.6.0 | BSD-3-Clause | <https://github.com/encode/starlette> |
| Uvicorn | 0.52.1 | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| HTTPX | 0.28.1 | BSD-3-Clause | <https://github.com/encode/httpx> |
| NumPy (optional retrieval service) | 2.3.4 | BSD-3-Clause | <https://github.com/numpy/numpy> |
| aiosqlite | 0.22.1 | MIT | <https://github.com/omnilib/aiosqlite> |
| argon2-cffi | 25.1.0 | MIT | <https://github.com/hynek/argon2-cffi> |
| pytest (development only) | 9.0.3 | MIT | <https://github.com/pytest-dev/pytest> |

The two Python base images are pinned by digest. Direct and transitive Python
packages are pinned with distribution hashes in the checked-in lock files; the
lock files are the authoritative inventory of installed Python package
versions. Each dependency retains its upstream license.

The everyday GGUF is `Qwen_Qwen3.5-9B-Q6_K.gguf`, exactly 7,958,818,848 bytes
with SHA-256
`073a9275e65d9c8cd2819cf5f77b99fbaa6e87ba591da6bbaa86ec073a64bfef`.
The optional two-node GGUF is `Qwen_Qwen3.5-27B-Q4_K_M.gguf`, exactly
17,984,872,928 bytes with SHA-256
`81657841d62f1821c748d0fea6c260b7d3508844fe4e9250253ef81c4e4d9edf`.
The download helper pins both immutable repository revisions and refuses
artifacts that do not match both their byte count and digest.

The optional Machine B retrieval profile adds two further pinned artifacts, both
verified the same way. The embedding GGUF is `bge-m3-Q8_0.gguf`, exactly
634,553,760 bytes with SHA-256
`950f4a8e5e19477a6d3c26d2f162233c20002c601f75e4b002e3239997821167`. The
reranking GGUF is `bge-reranker-v2-m3-Q8_0.gguf`, exactly 635,676,416 bytes with
SHA-256 `a43c7c9b11a4c1517e5bf95151960e1621d1b72f7a493364b01e386cf1aaa1d3`.
These two carry different licenses from each other — MIT for bge-m3 and
Apache-2.0 for bge-reranker-v2-m3 — so do not treat them as one bundle when
redistributing. Neither is downloaded, installed, or loaded unless the optional
retrieval profile is deliberately enabled.

The model repository metadata and exact GGUF artifact should be checked again
before redistributing model blobs. Every model discovered through Hugging Face
has its own license; a repository being public or downloadable does not make it
FLOSS. Missing, custom, research-only, or noncommercial model licenses require
separate review.

The default standalone service does not start or connect to RPC. The optional
two-node path builds llama.cpp with its experimental RPC backend. Upstream calls
that backend insecure, and the pinned release is not represented here as a
security fix for `CVE-2026-34159`. See `SECURITY.md` and recheck upstream
advisories before every pin change. A separately packaged GGUF retains both the
base model's license and any applicable packaging terms; record the exact
repository, filename, size, and SHA-256 digest used by a real deployment.

## Hosted service and data boundaries

- Cloudflare Tunnel and Access are optional hosted proprietary services. The
  `cloudflared` connector is FLOSS, but the remote path is not end-to-end
  self-hosted. A public VPS running a FLOSS reverse proxy, VPN, or tunnel can
  replace it.
- GitHub Pages is a hosted static-site service. It provides no origin proxy or
  server-side authentication and never receives secrets in this design.
- The Hugging Face Hub API is an external hosted catalog. Its returned model
  artifacts retain per-repository terms.
- Open-Meteo's server software is AGPLv3, while hosted forecast data is
  attributed under CC BY 4.0 and its free hosted API is non-commercial-only,
  with usage terms and limits.
  Weather responses include upstream links; deployments should visibly retain
  “Weather data by Open-Meteo.com.” See <https://open-meteo.com/en/terms>.
- SearXNG queries third-party search engines. Those providers have their own
  service terms and receive the searches routed to them.

## Accepted upstream runtime findings

The August 12, 2026 release audit found no known advisories in either custom
Python application's locked dependency set. The official SearXNG image's
`h2` 4.4.0 finding is locally overlaid with fixed 4.4.1. The cloudflared build
still embeds some scanner-flagged Go modules for hostile
HTTP/3 peer/server paths that this client-only Cloudflare topology does not
expose. No newer official release was available during the audit. These are
accepted low-reach upstream risks, not a claim of zero vulnerabilities;
monitor and update the exact upstream pins when fixes are published.

The stack deliberately does not use Open WebUI v0.11.0 in its final design.
That release's branding-restricted license is source-available but not cleanly
OSI/FLOSS. The custom KevinBeLLM browser/server is AGPL-3.0-or-later.

The configured `SOURCE_URL` must resolve to the public, corresponding
KevinBeLLM source before a public deployment is offered. The GitHub repository,
Pages site, domain, Cloudflare Access policy, and tunnel credential are
deployment inputs; this repository does not create or embed them automatically.
