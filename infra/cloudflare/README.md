# Authenticated remote access from Machine A

This deployment publishes the loopback-only KevinBeLLM web service on Machine A
through a **remotely managed, named Cloudflare Tunnel**. Cloudflare Access is the
outer identity gate and KevinBeLLM keeps its own login as a second gate. The
connector is outbound-only, so no router port-forward is required.

The `cloudflared` client is Apache-2.0 FLOSS. Cloudflare Access and its edge
network are hosted proprietary services. A self-managed VPN or public reverse
proxy can replace this adapter without changing KevinBeLLM's local API.

Do not use `cloudflared tunnel --url ...` or a temporary `trycloudflare.com`
Quick Tunnel for this deployment.

## Security boundary

```text
public GitHub Pages site
        | link only; no credentials or application traffic
        v
assistant.example.com
        | Cloudflare Access: narrowly scoped owner identity
        v
named Tunnel connector on Machine A (outbound connection only)
        | host networking -> http://127.0.0.1:3000
        v
KevinBeLLM application login
        |
        v
standalone llama.cpp on Machine A -> RTX 3060 + RTX 3070 everyday model
```

GitHub Pages is only a public landing page. It is not an authentication layer,
reverse proxy, or application host. It must never contain a tunnel token,
Access service token, cookie, API key, application password, or client-side
password prompt.

The connector deliberately uses host networking so that a rootless container
can reach Machine A's loopback-bound origin. The Compose service publishes no
ports, binds its metrics endpoint to loopback, runs as Machine A's unprivileged
UID/GID, drops every Linux capability, and uses a read-only root filesystem.
This deployment does not change the browser-facing origin.

## Prerequisites

- A domain in an active Cloudflare zone.
- A Cloudflare Zero Trust account with a configured login method.
- A self-hosted [Access application][access-app] whose Allow policy contains only the exact
  owner email address(es) or a narrowly scoped identity-provider group.
- KevinBeLLM healthy on Machine A at `http://127.0.0.1:3000`, with its own
  authentication enabled.
- Rootless Podman and `podman-compose` on Machine A.
- Systemd user lingering enabled for Machine A's account so the connector can
  return after an encrypted boot is physically unlocked.

Do not add `ufw allow 3000`, expose container port 3000, or create a router
port-forward. `cloudflared` makes outbound connections to Cloudflare, normally
on port 7844 over UDP (QUIC) or TCP (HTTP/2); it needs no inbound firewall rule.
See Cloudflare's [tunnel firewall guidance][firewall].

## Review the named Cloudflare deployment

Before starting the connector, audit these dashboard settings without copying
any credential into the repository:

1. In **Zero Trust -> Access controls -> Applications**, open the existing
   self-hosted application. Its hostname must be the intended assistant
   hostname and its Allow policy must name only authorized identities.
2. Remove any **Bypass** policy and any Allow rule using **Everyone** or an
   unrestricted login-method selector. An exact-email rule may use a one-time
   PIN login method; the unsafe configuration is allowing the login method
   itself instead of the intended identity. Cloudflare documents these cases in
   its [Access policy guide][policies].
3. In **Networking -> Tunnels**, open the existing named tunnel and its
   published application route. Set the service to
   `http://127.0.0.1:3000` and enable **Protect with Access**.
4. Confirm locally on Machine A that KevinBeLLM shows its application login:

   ```bash
   curl --fail --head http://127.0.0.1:3000/
   ```

If these resources are missing, follow the new-resource procedure below.

The dashboard work and secret entry require the account owner. This repository
cannot safely automate them without introducing long-lived Cloudflare account
credentials.

## Create resources only when reuse is impossible

Cloudflare's [self-hosted application guide][publish-app] recommends creating
the Access application before its public tunnel route; otherwise the route is
initially reachable without Access.

1. In **Zero Trust -> Access controls -> Applications**, create a self-hosted
   application for the intended assistant hostname.
2. Add an Allow policy for only the exact owner email address(es) or narrowly
   scoped identity-provider group. Set an appropriate session duration and use
   MFA when the chosen identity provider supports it.
3. Following the [named-tunnel setup][create-tunnel], create a remotely managed
   tunnel with a topology-neutral durable name such as `kevinbellm`.
4. Add a published application route for the same hostname with service
   `http://127.0.0.1:3000`.
5. Enable **Protect with Access** on that route so `cloudflared` validates the
   Access application token on behalf of the origin.
6. Use **Add a replica** to obtain the tunnel token for Machine A. Do not run a
   generated root-level install command and do not paste its token into Git,
   shell history, an issue, a screenshot, or a Pages build variable.

`remote-route.example.yml` is a review worksheet for these dashboard values.
It is not read by `cloudflared`.

## Zoo Code Service Auth path

The browser-facing application at `assistant.example.com/*` must keep its
interactive exact-email or narrow-group Allow policy. Zoo Code cannot complete
that browser redirect. To use the extension remotely, add a second,
more-specific Access application for `assistant.example.com/v1/*`. Cloudflare
evaluates the most-specific matching application, so the API path can require a
device credential without weakening the root browser policy.

Configure the API-path application as follows:

1. Create a separate Cloudflare Access service token for each Zoo Code
   installation. Give it a device-specific name and an appropriate expiry.
2. Add a **Service Auth** policy to the `assistant.example.com/v1/*`
   application. Its Include rule must select only the intended service token.
3. Do not add an **Allow Everyone**, **Bypass**, or reusable shared-token rule.
4. In Zoo Code's OpenAI Compatible provider, keep the KevinBeLLM personal token
   in **API Key**. Add the Cloudflare service token as two custom headers:
   `CF-Access-Client-Id` and `CF-Access-Client-Secret`.
5. Set the KevinBeLLM root `.env` value `ZOO_API_BASE_URL` to
   `https://assistant.example.com/v1` so the authenticated credential page
   displays the remote URL.
6. Copy the unique **Application Audience (AUD)** tag from both the root Access
   application and the `/v1/*` Access application. In the tunnel route's
   **Protect with Access** origin settings, keep validation required and add
   both AUD tags to its accepted audience list. If only the root AUD is
   accepted, valid Zoo service-auth requests will fail at the connector; never
   work around that failure by disabling Protect with Access. See Cloudflare's
   [origin parameters documentation][origin-parameters].

These are two independent gates: the Cloudflare headers authorize the VS Code
installation to reach the origin, while `Authorization: Bearer <KevinBeLLM
token>` authorizes the KevinBeLLM account. Revoking either credential blocks
access. Never put either value in this worksheet, `.env`, Compose, source
control, shell history, screenshots, or support messages.

Do not enable Cloudflare's optional single-header service-token mode on the
`Authorization` header. Zoo Code already needs that header for the KevinBeLLM
Bearer token; keep Cloudflare authentication in its two dedicated headers.

Zoo Code stores its API Key in VS Code Secret Storage, but its custom-header
configuration and exported provider profiles should still be treated as
sensitive. Prefer the repository's SSH-forward path or a private VPN when that
is practical; it avoids storing Cloudflare's client secret in the extension.

Before relying on the path, test all four cases with a disposable short prompt:

1. No credentials: Cloudflare denies the request.
2. Cloudflare headers only: the request reaches KevinBeLLM but `/v1/models`
   returns `401`.
3. KevinBeLLM token only: Cloudflare denies the request.
4. Both credential sets: `/v1/models` succeeds and Zoo Code can complete a
   tool-enabled request.

Then revoke the KevinBeLLM token and Cloudflare service token one at a time and
confirm each revocation fails closed. Keep the root interactive application in
the acceptance test below; the API-path application is an additional route,
not a replacement for browser login.

Also repeat the successful case after reviewing the tunnel route: its Protect
with Access audience allowlist must contain the AUD tags for both the root and
`/v1/*` applications.

## Configure Machine A

From the project checkout on Machine A:

```bash
cd infra/cloudflare
cp .env.example .env
id -u
id -g
```

Set `LOCAL_UID` and `LOCAL_GID` in `.env` to those numeric outputs. Set the
worksheet hostname to the existing assistant hostname and leave the origin
exactly `http://127.0.0.1:3000`. The token does not belong in `.env`.

Also set the application's root `.env` `PUBLIC_URL` to that external HTTPS URL
while retaining KevinBeLLM's application authentication.

Create the ignored token file without putting the token on a command line:

```bash
install -d -m 700 secrets
install -m 600 /dev/null secrets/tunnel-token
${EDITOR:-nano} secrets/tunnel-token
```

Paste the token as the only line, save it, and verify only metadata—not its
contents:

```bash
test -s secrets/tunnel-token
test "$(stat -c '%a' secrets/tunnel-token)" = 600
test "$(stat -c '%u' secrets/tunnel-token)" = "$(id -u)"
```

The pinned connector supports [`--token-file`][run-parameters], so Compose
receives only the secret's file path. `.gitignore` excludes `.env` and
everything in `secrets/` except `.gitkeep`.

## Validate and start

Render the merged rootless-Podman configuration before starting it:

```bash
../../scripts/compose.sh \
  -f compose.yaml \
  --env-file .env \
  -p kevinbellm-remote-access \
  config
```

The output must show all of the following:

- `network_mode: host` and no `ports` entry;
- the origin is not present because the remotely managed route stores it at
  Cloudflare;
- metrics listen only on `127.0.0.1`;
- `/run/secrets/tunnel-token` is a file mount or Compose secret reference, and
  the token value itself is absent;
- the rootless `keep-id` override is active.

Start only after the Access policy and protected route have been reviewed:

```bash
../../scripts/start-remote.sh
../../scripts/compose.sh \
  -f compose.yaml \
  --env-file .env \
  -p kevinbellm-remote-access \
  ps
../../scripts/compose.sh \
  -f compose.yaml \
  --env-file .env \
  -p kevinbellm-remote-access \
  logs --tail=100 cloudflared
curl --fail http://127.0.0.1:20241/ready
```

The service is healthy only when its loopback `/ready` endpoint reports an
active tunnel connection. `start-remote.sh` waits for that endpoint and fails
closed if it never becomes ready. If you changed `CLOUDFLARED_METRICS_PORT`,
use that loopback port in the manual `curl` check. Keep log level at `info`;
debug logging can contain request details.

For automatic recovery after Machine A is powered on and its encrypted disk is
physically unlocked, use the repository's remote systemd-user autostart flow:

```bash
cd ../..
sudo loginctl enable-linger "$(id -un)"
./scripts/install-autostart.sh remote
systemctl --user start kevinbellm-remote.service
```

Full-disk encryption still requires physical unlock after a cold boot. Neither
Cloudflare Tunnel nor systemd can bypass that prompt.

## End-to-end acceptance test

Perform this test from a device that is not relying on the home LAN path, such
as a phone with Wi-Fi disabled:

1. Open the assistant hostname in a private browser window. Cloudflare Access
   must appear before KevinBeLLM.
2. Try an identity outside the Allow policy and confirm it is denied.
3. Sign in as an allowed identity and confirm KevinBeLLM then requires or
   recognizes its own application login.
4. Start a short streamed answer and confirm the response completes.
5. Sign out of both layers and confirm a new private session is challenged.
6. From another LAN device, verify Machine A's port 3000 is not reachable by its
   LAN address. Also verify there is no router port-forward for 3000 or 8080.
7. Confirm every public **Open assistant** link uses only the hostname that
   passed these checks.

If an unauthenticated request reaches KevinBeLLM, immediately stop the connector
and disable or remove the published route before correcting Access:

```bash
../../scripts/compose.sh \
  -f compose.yaml \
  --env-file .env \
  -p kevinbellm-remote-access \
  down
```

## Operations

- Keep KevinBeLLM's login enabled as defense in depth.
- Rotate the named tunnel token immediately after suspected exposure.
- Upgrade the pinned image deliberately after reviewing Cloudflare release
  notes and replacing both the tag and Linux/AMD64 digest.
- Check connector health in the tunnel overview and locally with Compose `ps`.
- Run only one connector for this route, and do not make Machine A's origin
  listen on `0.0.0.0` merely to satisfy the tunnel.
- Stopping the connector removes remote access without stopping local inference.

[create-tunnel]: https://developers.cloudflare.com/tunnel/setup/
[tunnel-token]: https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/
[access-app]: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/
[policies]: https://developers.cloudflare.com/cloudflare-one/access-controls/policies/
[publish-app]: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/
[firewall]: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/
[origin-parameters]: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/
[run-parameters]: https://developers.cloudflare.com/tunnel/advanced/run-parameters/
