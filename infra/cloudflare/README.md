# Authenticated remote access with Cloudflare

The `cloudflared` client is Apache-2.0 FLOSS. Cloudflare Access and its edge
network are hosted proprietary services; this optional adapter is therefore not
an end-to-end FLOSS path. A self-managed public VPS with a FLOSS reverse proxy,
VPN, or tunnel can replace it without changing KevinBeLLM's local API.

This template publishes the loopback-only KevinBeLLM web service through a
**remotely managed, named Cloudflare Tunnel**. Cloudflare Access authenticates
the user before traffic can reach the tunnel. No router port-forwarding or
public origin port is required.

Nothing in this directory creates an unauthenticated Quick Tunnel. Do not use
`cloudflared tunnel --url ...` or a temporary `trycloudflare.com` address for
this deployment.

## Security model

```text
public GitHub Pages site
        │ link only
        ▼
assistant.example.com
        │ Cloudflare Access identity policy
        ▼
named Cloudflare Tunnel (outbound from the laptop)
        │ http://127.0.0.1:3000 over host networking
        ▼
KevinBeLLM authentication → local model and tools
```

The landing page and assistant are different sites. GitHub Pages never proxies
the assistant and must never contain a tunnel token, Access service token,
cookie, API key, or imitation client-side password prompt.

Cloudflare recommends a remotely managed tunnel for most deployments. A tunnel
token can run that specific tunnel, so handle it as a secret and rotate it if it
is exposed. See Cloudflare's [named tunnel setup][create-tunnel], [tunnel token
permissions][tunnel-token], and [Access application guide][access-app].

## Prerequisites

- A domain in an active Cloudflare zone.
- A Cloudflare Zero Trust account and an identity provider. Exact-email rules
  can use an appropriate configured login method; an organizational IdP is
  preferable when available.
- KevinBeLLM working locally at `http://127.0.0.1:3000` with its own
  authentication enabled. Keep that host port bound to loopback.
- rootless Podman with podman-compose (preferred), or Docker Compose.

The Compose service uses host networking so `cloudflared` can reach an origin
that is deliberately bound to `127.0.0.1`. It publishes no container ports and
binds its metrics endpoint to loopback only. The connector runs as the local
unprivileged UID/GID, with a read-only filesystem and all Linux capabilities
dropped.

## 1. Prepare the local, non-secret settings

```bash
cd infra/cloudflare
cp .env.example .env
id -u
id -g
```

Set `LOCAL_UID` and `LOCAL_GID` in `.env` to those outputs. Choose the final
hostname and confirm the local KevinBeLLM origin. The hostname and origin values
in `.env` are a worksheet for the dashboard; a remotely managed route is stored
at Cloudflare rather than in this Compose file.

## 2. Create Access before publishing the route

This ordering matters. Without an Access application, a published tunnel route
is reachable by anyone who knows or discovers its hostname.

1. In Cloudflare Zero Trust, open **Access controls → Applications**.
2. Create a **Self-hosted and private** application and add the intended public
   hostname, for example `assistant.example.com`.
3. Add an **Allow** policy that includes only the exact owner email address(es)
   or a narrowly scoped IdP group.
4. Select the intended identity provider, set a sensible session duration, and
   enable independent MFA if appropriate.
5. Save the application before adding the tunnel's published application route.

Do not use an Allow policy with **Everyone**, all valid emails, or an unrestricted
one-time-PIN login. Do not create a Bypass rule for the application. Cloudflare
documents these as common policy mistakes in its [Access policy guide][policies].
Access applications deny by default; an authorized identity must match an Allow
policy.

## 3. Create the named tunnel and protected route

1. In the Cloudflare dashboard, open **Networking → Tunnels**.
2. Create a tunnel named `asus-kevin-llm` or another durable, descriptive name.
3. Add a **Published application** route:
   - Hostname: the same hostname protected by Access.
   - Service: `http://127.0.0.1:3000`.
4. Turn on **Protect with Access** for the route so `cloudflared` validates the
   Access application token on behalf of the origin.
5. On the tunnel overview, choose **Add a replica** and copy only the long
   `eyJ...` tunnel token from the generated install command. Do not run that
   generated command and do not paste the token into the repository.

Cloudflare's public-hostname guide explicitly recommends creating Access before
the route and validating the Access token at the origin or tunnel. See [Publish
a self-hosted application][publish-app].

## 4. Store the token outside version control

Create the ignored token file without putting the token in shell history:

```bash
install -m 600 /dev/null secrets/tunnel-token
${EDITOR:-nano} secrets/tunnel-token
```

Paste the token as the only line, save, then verify permissions without printing
its contents:

```bash
test -s secrets/tunnel-token
test "$(stat -c '%a' secrets/tunnel-token)" = 600
```

The pinned connector supports `--token-file`; the token is mounted read-only at
runtime. `.gitignore` excludes both the token and the local `.env` file. Never
commit either file. Never paste the token into an issue, screenshot, log, or
GitHub Actions variable intended for a public Pages build.

## 5. Validate, then start intentionally

Review the fully resolved configuration. This command must not display the
tunnel token because Compose receives only its file path:

```bash
../../scripts/compose.sh -f compose.yaml --env-file .env config
```

Confirm KevinBeLLM is healthy first:

```bash
curl --fail --head http://127.0.0.1:3000/
```

Only after Access, the named route, and the token file are ready:

```bash
../../scripts/compose.sh -f compose.yaml --env-file .env up -d
../../scripts/compose.sh -f compose.yaml --env-file .env ps
../../scripts/compose.sh -f compose.yaml --env-file .env logs --tail=100 cloudflared
```

The service is healthy only after its local `/ready` endpoint reports an active
tunnel connection. This repository intentionally does not start the connector
without owner-provided credentials.

## 6. Verify the gate

1. Open the assistant hostname in a private browser window. It must show a
   Cloudflare Access authentication flow, not KevinBeLLM directly.
2. Try an identity not included by the policy and confirm access is denied.
3. Sign in with an allowed identity and confirm KevinBeLLM then requests or
   recognizes its own application login.
4. Start a short streamed response to verify WebSocket/streaming behavior.
5. From another LAN device, verify that the laptop's port `3000` is not directly
   reachable.
6. Confirm every **Open assistant** link in `docs/index.html` uses the hostname
   that just passed these Access checks.

If an unauthenticated request ever reaches KevinBeLLM, stop the connector with
`../../scripts/compose.sh -f compose.yaml --env-file .env down`, remove or disable the published route, and correct the
Access application before trying again.

## Operations

- Keep KevinBeLLM's own authentication enabled as defense in depth.
- Rotate the named tunnel token periodically and immediately after suspected
  exposure. Cloudflare supports refreshing it from the tunnel overview.
- Keep `CLOUDFLARED_LOG_LEVEL=info`; debug logging can contain request details.
- Upgrade the pinned image deliberately after reviewing Cloudflare release notes
  and updating the image digest.
- The connector needs outbound connectivity to Cloudflare, typically port 7844;
  it does not require inbound firewall rules.
- Stop remote access without disturbing the local assistant with
  `../../scripts/compose.sh -f compose.yaml --env-file .env down` from this directory.

## Alternative: shared container network

Host networking is used here because the primary service is expected to publish
KevinBeLLM on loopback. If `cloudflared` is later moved into the primary Compose
project, it can instead share that project's private network and route directly
to a service such as `http://assistant-web:3000`. In that design, remove
`network_mode: host`, keep the metrics port unexposed, and update the remotely
managed route. Do not make the origin port public merely to connect the two
containers.

[create-tunnel]: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/
[tunnel-token]: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/remote-tunnel-permissions/
[access-app]: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/
[policies]: https://developers.cloudflare.com/cloudflare-one/access-controls/policies/
[publish-app]: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/
