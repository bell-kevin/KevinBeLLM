# kevinBeLLM

Licensed `AGPL-3.0-or-later`; see [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Third-party programs, model
weights, hosted services, and data keep their own licenses.

KevinBeLLM turns this ASUS laptop into a private, hardware-accelerated local AI
assistant with a custom AGPL browser interface, selectable models, live
web/news search, weather forecasts, and read-only Hugging Face model discovery.

The default is `qwen3.6:35b-a3b-q4_K_M`, the best practical balance found for
this laptop's 32 GB RAM, i7-4720HQ, and 3 GB GTX 970M. The custom application's
model menu shows every model installed in Ollama and starts on that
recommendation. The smaller `qwen3.5:9b-q4_K_M` is the faster preferred option.

## Use it

The stack is already installed on this laptop. Start it with:

```bash
./scripts/start.sh
./scripts/show-login.sh
```

Open <http://127.0.0.1:3000>, sign in, and choose a model from the selector at
the top of a new chat. The large default can take several minutes to load on
first use. Only one model stays loaded at a time to avoid exhausting RAM.

Useful commands:

```bash
./scripts/status.sh       # service state and installed model IDs
./scripts/doctor.sh       # live search/weather/HF/UI checks
./scripts/stop.sh         # stop UI, tools, tunnel, and search
./scripts/install-autostart.sh  # start automatically after desktop login
```

After the protected tunnel is configured, use
`./scripts/install-autostart.sh remote` instead to autostart both the local
stack and authenticated remote connector. The default `local` mode never
starts a tunnel.

The generated `.env` contains the one-time credentials used by the explicit
initial-account bootstrap. It is mode `0600` and excluded from git. Change the
initial password in KevinBeLLM after signing in. Public account signup does not
exist. The normal application startup path will not create an administrator;
it fails closed when the account database is empty. Session cookies contain
random opaque tokens; only their SHA-256 digests are stored in the database.

## What runs where

```text
Browser (login + model picker)
        |
        v
KevinBeLLM web app on 127.0.0.1:3000
   |             |                 |
   v             v                 v
Ollama       SearXNG          Live-data tools
127.0.0.1    127.0.0.1        127.0.0.1
GPU/CPU      web + news       Open-Meteo + HF Hub
```

- Ollama runs natively so it can use the GTX 970M CUDA backend. Its API is
  loopback-only.
- The custom FastAPI/vanilla-JS web app is AGPL-3.0-or-later. It stores only
  Argon2 password hashes and hashed sessions in a named container volume; chat
  history stays in the current browser tab.
- SearXNG is self-hosted and provides JSON search results. It is also
  loopback-only.
- The custom tool service is read-only, non-root, has all Linux capabilities
  dropped, and can neither execute code nor install models.
- Search pages are untrusted evidence. The assistant is configured for native
  tool calling and a 16K working context, not unlimited host access.

## Remote access and GitHub Pages

GitHub Pages cannot run the model, hold a private password, or securely proxy
requests to a laptop: it only serves static public files. The safe layout is:

```text
public GitHub Pages launch page
             |
             v
Cloudflare Access authentication
             |
             v
named Cloudflare Tunnel -> KevinBeLLM login -> local Ollama
```

The `docs/` folder is safe to publish as a Pages site. Follow
[`infra/cloudflare/README.md`](infra/cloudflare/README.md) to create the named
tunnel and Access policy. Remote activation still requires an owner-controlled
domain, a Cloudflare Access identity policy, and a local tunnel credential.
Only then use `./scripts/start-remote.sh`. This script refuses to start if the
URL is not HTTPS or the secret tunnel token is missing.

Before advertising the public endpoint, publish this repository and make
`SOURCE_URL` point to the matching public source. Replace the Pages launch-link
placeholder only after the protected assistant hostname has passed the access
checks in the Cloudflare guide. The Pages repository contains neither the
application password nor a browser-side substitute for server authentication.

Never commit `.env`, a tunnel token, model blobs, the KevinBeLLM data volume, or
chat exports. Do not use an unauthenticated "quick tunnel" for this service.

## Fresh installation

On a compatible Ubuntu host with rootless Podman (preferred) or Docker and the
user-local Ollama service:

```bash
./scripts/setup.sh
/home/$USER/.local/bin/ollama pull qwen3.6:35b-a3b-q4_K_M
/home/$USER/.local/bin/ollama pull qwen3.5:9b-q4_K_M
./scripts/start.sh
```

Ollama tags are mutable. The artifacts reviewed and benchmarked for this host
had these exact manifest digests; verify them after pulling:

```text
qwen3.6:35b-a3b-q4_K_M  07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522
qwen3.5:9b-q4_K_M       6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7
```

Run `ollama list` and `curl -s http://127.0.0.1:11434/api/tags | jq` to
compare the local manifests. Review the upstream license again whenever a tag
resolves to a different digest.

With Podman, install `podman-compose` 1.4.1 or newer separately from Ubuntu
24.04's outdated 1.0.6 package, then run
`./scripts/select-container-engine.sh podman`. The script refuses older versions
because they do not enforce healthy dependency ordering. On this laptop Docker
is the current runtime fallback because installing the Podman system packages
requires an administrator password; the same Compose projects include reviewed
rootless-Podman user-namespace overrides.

Configuration is in [`compose.yaml`](compose.yaml), the search service under
[`infra/search`](infra/search/README.md), and the OpenAPI service under
[`services/live-tools`](services/live-tools/README.md).

Container builds install hash-locked direct and transitive Python dependencies.
The assistant image is digest-pinned to Python 3.13.15 slim Bookworm and the
live-tools image to Python 3.12.13 slim Bookworm. The latter first upgrades to
the integrity-pinned pip 26.2.1 wheel before installing its application lock.
Maintainers can deliberately refresh the locks with
`./scripts/update-locks.sh`, then must rerun the security scan and test suite
before committing them.

KevinBeLLM stores its Argon2-hashed account and hashed login sessions in the
`asus-kevin-bellm-data` volume. Deleting that volume erases the account database;
chat transcripts are not stored server-side by this application. An empty
database is not silently repopulated from stale bootstrap credentials. If you
intentionally delete the data volume, choose a fresh `ADMIN_PASSWORD` in the
private `.env`, delete the ignored empty `.bootstrap-complete` marker, and run
`./scripts/start.sh`; that explicitly performs the one-shot bootstrap again.

## Measured model behavior on this laptop

Both preferred choices are installed and were exercised against the local
Ollama runtime. These are one-run, very-short-output measurements rather than
promises for a long conversation; prompt length, context use, and GPU offload
will change the observed rate.

| Model | Role | Measured behavior |
|---|---|---|
| `qwen3.6:35b-a3b-q4_K_M` | recommended default | first cold load: 174.97 s; warmed, thinking-disabled three-token check: 3.144 s total and 9.687 output tokens/s; native weather-tool call: 6.334 output tokens/s |
| `qwen3.5:9b-q4_K_M` | smaller fallback | cold load: 34.25 s; three-token check: 38.25 s total and 5.243 output tokens/s |

The stack uses a 16K working context and keeps one model loaded at a time.
Browser requests disable the Qwen thinking mode so a small response budget is
not consumed solely by hidden reasoning. Training a new foundation model is
unnecessary for current facts: the read-only internet tools supply those at
request time; fine-tuning would instead change behavior or style.
