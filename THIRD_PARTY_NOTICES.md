# Third-party notices

The original source, configuration, scripts, and documentation in this
repository are licensed under `AGPL-3.0-or-later`. The following independent
programs, model weights, data, and hosted services retain their own terms. They
are not relicensed by this repository.

## Software and model components

| Component | Pinned/recommended version | Upstream license | Source |
|---|---|---|---|
| Ollama | 0.32.9 | MIT | <https://github.com/ollama/ollama/tree/v0.32.9> |
| Qwen3.6 35B-A3B weights | official base, Q4_K_M packaging | Apache-2.0 | <https://huggingface.co/Qwen/Qwen3.6-35B-A3B> |
| Qwen3.5 9B weights | official base, Q4_K_M packaging | Apache-2.0 | <https://huggingface.co/Qwen/Qwen3.5-9B> |
| SearXNG | commit `c01178d03` | AGPL-3.0-or-later | <https://github.com/searxng/searxng/tree/c01178d03> |
| hyper-h2 | 4.4.1 security overlay | MIT | <https://github.com/python-hyper/h2/tree/v4.4.1> |
| cloudflared client | 2026.7.3 | Apache-2.0 | <https://github.com/cloudflare/cloudflared/tree/2026.7.3> |
| Podman | Ubuntu 24.04 package 4.9.3 | Apache-2.0 | <https://github.com/containers/podman> |
| podman-compose | 1.4.1 or newer | GPL-2.0-only | <https://github.com/containers/podman-compose> |
| Python | 3.13.15 (assistant), 3.12.13 (tools) | PSF-2.0 | <https://www.python.org/> |
| pip | 26.2.1 (live-tools build bootstrap) | MIT | <https://github.com/pypa/pip/tree/26.2.1> |
| FastAPI | 0.141.1 | MIT | <https://github.com/fastapi/fastapi> |
| Starlette | 1.6.0 | BSD-3-Clause | <https://github.com/encode/starlette> |
| Uvicorn | 0.52.1 | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| HTTPX | 0.28.1 | BSD-3-Clause | <https://github.com/encode/httpx> |
| aiosqlite | 0.22.1 | MIT | <https://github.com/omnilib/aiosqlite> |
| argon2-cffi | 25.1.0 | MIT | <https://github.com/hynek/argon2-cffi> |
| pytest (development only) | 9.0.3 | MIT | <https://github.com/pytest-dev/pytest> |

The two Python base images are pinned by digest. Direct and transitive Python
packages are pinned with distribution hashes in the checked-in lock files; the
lock files are the authoritative inventory of installed Python package
versions. Each dependency retains its upstream license.

The locally reviewed Ollama manifests were
`07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522`
for Qwen3.6 and
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`
for Qwen3.5. Ollama registry tags are mutable; verify both manifest digest and
license metadata after pulling or before redistributing an artifact.

The model repository metadata and exact Ollama manifest should be checked again
before redistributing model blobs. Every model discovered through Hugging Face
has its own license; a repository being public or downloadable does not make it
FLOSS. Missing, custom, research-only, or noncommercial model licenses require
separate review.

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
`h2` 4.4.0 finding is locally overlaid with fixed 4.4.1. Current official
Ollama and cloudflared builds still embed some scanner-flagged Go modules for
image/SSH or hostile HTTP/3 peer/server paths that this text-only, loopback
Ollama and client-only Cloudflare topology does not expose. No newer official
release was available during the audit. These are accepted low-reach upstream
risks, not a claim of zero vulnerabilities; monitor and update the exact
upstream pins when fixes are published.

The stack deliberately does not use Open WebUI v0.11.0 in its final design.
That release's branding-restricted license is source-available but not cleanly
OSI/FLOSS. The custom KevinBeLLM browser/server is AGPL-3.0-or-later.

The configured `SOURCE_URL` must resolve to the public, corresponding
KevinBeLLM source before a public deployment is offered. The GitHub repository,
Pages site, domain, Cloudflare Access policy, and tunnel credential are
deployment inputs; this repository does not create or embed them automatically.
