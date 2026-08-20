<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Public landing page

This directory is the GitHub Pages source for KevinBeLLM's public project
landing page. It is intentionally static and contains no JavaScript, analytics,
credential collection, runtime probes, model-health checks, or private
configuration.

The page explains the fully GPU-resident everyday service, which spans the
RTX 3060 and RTX 3070 in the single owned host, and links authorized users to
the protected assistant at:

```text
https://assistant.kevin-bell.com/
```

GitHub Pages does not host the assistant or perform authentication. The launch
link must reach Cloudflare Access first; after that identity check, KevinBeLLM
requires its own application login. Keep the hostname synchronized with the
outbound Cloudflare Tunnel route if the deployment address changes.

In the repository settings, choose **Deploy from a branch**, select the desired
branch, and use `/docs` as the Pages source. Before publishing, validate the
page at narrow and wide viewport sizes and confirm that every **Open assistant**
link reaches Cloudflare Access—not the KevinBeLLM origin directly.

Do not add uptime badges, unlock-state indicators, internal addresses, tunnel
identifiers, service logs, or client-side authentication to this public site.
