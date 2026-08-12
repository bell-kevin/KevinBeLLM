<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Public landing page

This directory is safe to publish with GitHub Pages. It is a static site with
no JavaScript, analytics, credential collection, health probes, or private
configuration.

The published launch links target the Access-protected assistant at:

```text
https://assistant.kevin-bell.com/
```

Keep that hostname synchronized with the protected Cloudflare Tunnel route if
the deployment address changes.

In the repository settings, choose **Deploy from a branch**, select the desired
branch, and select `/docs` as the Pages source. Test the built site at narrow and
wide viewport sizes, and confirm that each **Open assistant** link reaches the
Cloudflare Access sign-in page—not KevinBeLLM directly.
