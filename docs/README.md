<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Public landing page

This directory is the GitHub Pages source for KevinBeLLM's public project
landing page. It is intentionally static and contains no JavaScript, analytics,
credential collection, runtime probes, model-health checks, or private
configuration.

The page explains the Qwen3.8-27B Q5_K_S service and its fully GPU-resident
model, xhigh reasoning profile, clearly qualified local quality measurements,
and RTX 3060 plus RTX 3070 deployment in the single owned host. It links
authorized users to the protected assistant at:

```text
https://assistant.kevin-bell.com/
```

GitHub Pages does not host the assistant or perform authentication. The launch
link must reach Cloudflare Access first; after that identity check, KevinBeLLM
requires its own application login. Keep the hostname synchronized with the
outbound Cloudflare Tunnel route if the deployment address changes.

The repository publishes from the `main` branch's `/docs` directory. Before
publishing, validate the page at narrow and wide viewport sizes and confirm that
every **Open assistant** link reaches Cloudflare Access—not the KevinBeLLM
origin directly.

Do not add uptime badges, unlock-state indicators, internal addresses, tunnel
identifiers, service logs, or client-side authentication to this public site.
Keep estimated intelligence figures visibly labeled as estimates, and never
present the local fixed-corpus regression result as a standardized benchmark.
