# Security policy

This repository is designed to be public; the running assistant and its host
configuration are not.

Never commit or publish:

- `.env`, `infra/search/.env`, or a populated `infra/cluster/*.env`;
- SSH private keys, `known_hosts`, host keys, or tunnel credentials;
- Cloudflare Tunnel tokens or origin certificates;
- KevinBeLLM's account database or data volume;
- GGUF/model storage, browser profiles, diagnostics containing identifiers, or
  personal files.

## Network boundary

Every application and inference port binds to loopback:

| Port | Service | Exposure |
|---|---|---|
| 3000 | KevinBeLLM web app | `127.0.0.1` only |
| 8080 | `llama-server` | `127.0.0.1` only |
| 50052 | `ggml-rpc-server` on Machine B | `127.0.0.1` only |
| 8090 | read-only live tools | `127.0.0.1` only |
| 8888 | SearXNG | `127.0.0.1` only |

The Windows laptop reaches port 3000 through an SSH local forward. Machine A
reaches Machine B's loopback RPC socket through a separate, restricted SSH
local forward. Never port-forward ports 3000, 8080, 50052, 8090, 8888, or
11434 on the home router. SSH itself should use keys only and be limited by the
host firewall to the trusted home subnet; use a VPN such as Tailscale rather
than an Internet-facing router rule if remote administration is later needed.

The optional browser-facing Cloudflare path must use a named Tunnel protected
by Cloudflare Access and then the application's own login. Do not point GitHub
Pages JavaScript directly at a private service port, and do not use a public
unauthenticated quick tunnel.

## llama.cpp RPC warning

Upstream describes the RPC backend as proof-of-concept, fragile, and insecure.
As of the pinned `b10451` deployment, upstream also lists
`CVE-2026-34159`/`GHSA-j8rj-fmpv-wcxw`, a critical unauthenticated RPC
remote-code-execution issue, with no patched version identified in the
advisory. The SSH tunnel limits who can reach the parser; it does not repair the
RPC implementation.

The cluster installer therefore requires an explicit risk acknowledgement,
binds RPC only to `127.0.0.1`, uses a narrowly restricted forwarding key, and
adds systemd sandboxing. Do not weaken those controls. Recheck the
[llama.cpp security advisories](https://github.com/ggml-org/llama.cpp/security)
before upgrading or deploying. Moving both GPUs into one suitable chassis is
the preferred way to eliminate this RPC boundary.

## Host and application boundary

Full-disk encryption protects powered-off storage, but a LUKS passphrase or
BitLocker startup PIN must be entered before Linux, networking, SSH, or these
services can start. Do not store a plaintext disk-unlock key in unencrypted
boot files. Keep recovery material offline.

Treat model output and fetched pages as untrusted. The included internet tools
are read-only and have no shell, filesystem-write, email, credential, or model
installation capability. The custom UI has no plug-in loader, shell tool, code
interpreter, or remote model installer. Review any future server-side tool
before adding it because tools can cross that boundary.

If a secret is accidentally committed, rotate it immediately; removing it
from the latest commit is insufficient because Git history retains it.
