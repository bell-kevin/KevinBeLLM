# Local SearXNG

This Compose project builds from an exact SearXNG image digest for browser use
and as an LLM search tool. Its tiny derived layer replaces the upstream image's
`h2` 4.4.0 with checksum-verified 4.4.1 to fix CVE-2026-71554 while waiting for
the next upstream SearXNG image. It publishes only on loopback, enables HTML and
JSON output, and uses a deliberately small set of general-web, news, and
Hugging Face engines.

## Run

```bash
./scripts/compose.sh -f infra/search/compose.yaml --env-file infra/search/.env -p asus-kevin-search up -d --build
./scripts/compose.sh -f infra/search/compose.yaml --env-file infra/search/.env -p asus-kevin-search ps
./infra/search/smoke-test.sh
```

`./scripts/setup.sh` creates the ignored local `.env`, generates its secret, and
records the actual UID/GID. If it is ever removed, run setup again. `LOCAL_UID`
and `LOCAL_GID` make the container run as the laptop user so bind-mounted files
stay editable.

## Endpoints

- Browser UI: `http://127.0.0.1:8888/`
- JSON search: `http://127.0.0.1:8888/search?q=weather&format=json`
- News JSON: `http://127.0.0.1:8888/search?q=artificial+intelligence&categories=news&time_range=day&format=json`

The KevinBeLLM web service on the host uses this query URL:

```text
http://127.0.0.1:8888/search?q=<query>&format=json
```

For a bridge-networked Docker Compose project, add this to its service if it
needs host access:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Then configure its SearXNG query URL as:

```text
http://host.docker.internal:8888/search?q=<query>&format=json
```

The service is intentionally not reachable from other machines on the LAN.
The included KevinBeLLM app instead uses host networking and calls
`127.0.0.1:8888`; this also works with rootless Podman.
