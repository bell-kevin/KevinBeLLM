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
| 8090 | read-only live tools | `127.0.0.1` only |
| 8888 | SearXNG | `127.0.0.1` only |

The Windows laptop reaches port 3000 through an SSH local forward. Never
port-forward ports 3000, 8080, 8090, or 8888 on the home router. SSH itself
should use keys only and be limited by the host firewall to the trusted home
subnet; use a VPN rather than an Internet-facing router rule if remote
administration is later needed.


The optional browser-facing Cloudflare path must use a named Tunnel protected
by Cloudflare Access and then the application's own login. Do not point GitHub
Pages JavaScript directly at a private service port, and do not use a public
unauthenticated quick tunnel.

## llama.cpp service boundary

The pinned build sets `GGML_RPC=OFF` and produces only `llama-server`,
`llama-cli`, and `llama-bench`. The systemd unit fixes the server endpoint to
IPv4 loopback, runs in offline/no-agent mode, and exposes neither the embedded
web UI nor model-download routes. Ordinary OpenAI-compatible tool-call
responses remain available to the application.

Because this llama.cpp revision auto-loads configuration files, the systemd
sandbox hides its system and user configuration locations, removes redirectable
home/config values, and launches the server with a cleared environment. Recheck
the [llama.cpp security advisories](https://github.com/ggml-org/llama.cpp/security)
before upgrading the pinned revision.

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
