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
| 50052 | optional `ggml-rpc-server` on Machine B | `127.0.0.1` only |
| 50053 | optional SSH-forwarded RPC endpoint on Machine A | `127.0.0.1` only |
| 8090 | read-only live tools | `127.0.0.1` only |
| 8888 | SearXNG | `127.0.0.1` only |
| 8081 | optional embedding `llama-server` on Machine B | `127.0.0.1` only |
| 8082 | optional reranking `llama-server` on Machine B | `127.0.0.1` only |
| 8091 | optional retrieval API on Machine B, SSH-forwarded to Machine A | `127.0.0.1` only |

The Windows laptop reaches port 3000 through an SSH local forward. Everyday
inference uses only Machine A and has no listener on 50052 or 50053. The
optional two-node profile reaches Machine B's loopback RPC socket through a
separate, restricted SSH local forward. Never port-forward ports 3000, 8080,
8081, 8082, 8091, 50052, 50053, 8090, 8888, or 11434 on the home router. SSH
itself should use
keys only and be limited by the host firewall to the trusted home subnet; use a
VPN rather than an Internet-facing router rule if remote administration is
later needed.

The optional document-retrieval profile uses no RPC. Its three services bind
Machine B loopback only, and Machine A reaches port 8091 through a restricted
SSH local forward whose key has its own account, cannot request a shell, and
cannot open anything except `127.0.0.1:8091`. It is a separate key and account
from the RPC tunnel by design: enabling document retrieval must never enable the
RPC parser, and revoking one must not revoke the other. The installers refuse to
reuse one key for both. The retrieval index contains extracted private document
text in plain form, so it is written mode 0600 inside a mode 0700 directory;
treat that directory as sensitive and keep it off synchronised storage.

The optional browser-facing Cloudflare path must use a named Tunnel protected
by Cloudflare Access and then the application's own login. Do not point GitHub
Pages JavaScript directly at a private service port, and do not use a public
unauthenticated quick tunnel.

## llama.cpp RPC warning

The default standalone service neither launches `ggml-rpc-server` nor supplies
an RPC address to `llama-server`. Because this pinned revision auto-loads
llama.cpp configuration files, the systemd sandbox also hides both system and
user config locations, removes redirectable home/config values, and launches
the server with a cleared environment containing only its CUDA device selector.
It also fixes llama.cpp's offline and no-agent modes; ordinary request-level
OpenAI-compatible tool-call responses remain available to the application.
Its installer first disables the prior inference server and locally installed
RPC tunnel/worker units, then refuses migration if either RPC port remains
open. Its status check enforces the same port boundary. Compiling optional
RPC support into the pinned tool build does not create a reachable parser; an
RPC process or client address must still be deliberately started.

Upstream describes the RPC backend as proof-of-concept, fragile, and insecure.
As of the pinned `b10451` deployment, upstream also lists
`CVE-2026-34159`/`GHSA-j8rj-fmpv-wcxw`, a critical unauthenticated RPC
remote-code-execution issue, with no patched version identified in the
advisory. The SSH tunnel limits who can reach the parser; it does not repair the
RPC implementation.

The optional coordinator/worker installer therefore requires an explicit risk
acknowledgement, binds RPC only to `127.0.0.1`, uses a narrowly restricted
forwarding key, and adds systemd sandboxing. Standalone mode rejects that flag
because no acceptance is needed for a service that does not use RPC. Do not
weaken those controls. Recheck the
[llama.cpp security advisories](https://github.com/ggml-org/llama.cpp/security)
before upgrading or deploying. If one request must use both GPUs, moving them
into one suitable chassis is the preferred way to eliminate the network RPC
boundary.

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
