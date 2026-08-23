# Use KevinBeLLM from Zoo Code

KevinBeLLM exposes a deliberately small, authenticated OpenAI-compatible API for
the official Zoo Code VS Code extension. Zoo Code talks to the application on
port 3000; it must never talk directly to the unauthenticated llama.cpp process
on port 8080.

The supported sign-in flow is:

```text
KevinBeLLM web login + current-password confirmation
  -> reusable personal API token whose value is shown once
  -> Zoo Code's password-masked API Key field
  -> Authorization: Bearer <token> on every /v1 request
  -> KevinBeLLM authorization, limits, and model allowlist
  -> loopback-only llama.cpp
```

Zoo Code's own **Sign in** button is for Zoo Gateway. The stock extension does
not offer custom-provider OAuth, so a revocable personal token is the secure
OpenAI-compatible credential. A token authenticates an account; it is not a
shared application secret or a way to identify the open-source extension.

## 1. Choose the network path

### Private SSH path (recommended for the owner)

On the Windows workstation, open the existing authenticated web forward:

```powershell
.\scripts\windows\Open-KevinBeLLMForward.ps1
```

Keep that window open. Use this Zoo Code Base URL:

```text
http://127.0.0.1:3000/v1
```

Do **not** add `-ForwardLlamaApi`, and do not use port 18080. That diagnostic
forward reaches raw llama.cpp and bypasses KevinBeLLM authentication.

### Remote Cloudflare path

An interactive Cloudflare Access application redirects non-browser clients to a
login page, which an OpenAI-compatible Zoo Code request cannot complete. Keep the
normal root application protected by its exact-email/group Allow policy, then
create a second, more-specific Access application for:

```text
assistant.example.com/v1/*
```

Give that path a **Service Auth** policy which includes only a short-lived,
per-device service token. Do not use Bypass, Everyone, or a shared organization-
wide token. In Zoo Code's Custom Headers, add the generated
`CF-Access-Client-Id` and `CF-Access-Client-Secret`; the KevinBeLLM personal token
still goes in **API Key**. Use two separate header rows, and do not configure
Cloudflare's optional single-header mode on `Authorization`, because that header
belongs to the KevinBeLLM Bearer token. A remote call must pass both gates.

Set the private root `.env` file on the server to the actual HTTPS API URL:

```dotenv
ZOO_API_BASE_URL=https://assistant.example.com/v1
```

Then redeploy `assistant-web`. See
[the Cloudflare deployment guide](../infra/cloudflare/README.md#zoo-code-service-auth-path)
for the policy worksheet and acceptance test. Keep **Protect with Access**
required on the tunnel route and configure it to accept the Application Audience
(AUD) tags from both the root and `/v1/*` applications.

Zoo Code stores its API Key through VS Code Secret Storage. Custom header values
and exported Zoo Code profiles do not have the same guarantee: use a trusted
workstation, keep the Cloudflare service token short-lived and per device, never
share/export that profile, and revoke it with the KevinBeLLM token when the
device is lost. Use the private SSH path when that tradeoff is unnecessary.

## 2. Sign in and create a named API token

1. Open KevinBeLLM through the selected protected route and sign in.
2. Open **Zoo Code access** from the account links.
3. Choose a device-specific name such as `Kevin desktop - VS Code`.
4. Re-enter the current KevinBeLLM password and select **Create credential**.
5. Copy the token immediately. Its plaintext is shown once and is never stored
   by KevinBeLLM. Creating a replacement is the recovery path if it is lost.

Tokens use 256 bits of randomness, are stored in SQLite only as SHA-256 digests,
expire after 30 days by default, and have last-used metadata. Each account may
have up to ten active tokens. A user can revoke a credential from the same page;
changing the account password revokes every browser session and every API token.
The credential name is only an inventory label, not client attestation: anyone
who steals the token can use it until it expires or is revoked.

`ZOO_TOKEN_TTL_DAYS` can be set from 1 through 365. Shorter is safer. Never put a
token in Git, `.env`, `.vscode`, screenshots, shell history, issue text, logs, or
a preconfigured Zoo settings export.

## 3. Install and configure Zoo Code

Install the official Marketplace extension in VS Code 1.100 or newer. The
current Zoo Code v3.80 package declares that minimum even though the install
page still mentions an older VS Code baseline:

```powershell
code --install-extension ZooCodeOrganization.zoo-code
```

Or open Extensions (`Ctrl+Shift+X`), search for **Zoo Code**, verify the publisher
is **ZooCodeOrg**, and install it. The official installation instructions are at
<https://docs.zoocode.dev/getting-started/installing>.

In Zoo Code settings, enter the exact values shown on KevinBeLLM's **Zoo Code
access** page:

| Setting | Value |
| --- | --- |
| API Provider | `OpenAI Compatible` |
| Base URL | `http://127.0.0.1:3000/v1` or the configured HTTPS URL |
| API Key | The reusable `kbm_v1_...` API key whose value is shown once |
| Model | Explicitly select `kevinbellm-27b` (or the value shown by the page) |
| Context Window Size | `32768` |
| Max Output Tokens | `8192` |
| Image Support | Off (actively turn this off; Zoo defaults it on) |
| Prompt Caching | Off |
| Enable Reasoning Effort | Off |
| Enable streaming | On |
| Include max output tokens | On |

Zoo's Model picker initially defaults to `gpt-4o`. After entering the Base URL
and API Key, explicitly choose the authenticated model returned by KevinBeLLM.
If it is not listed, type the exact displayed ID into Model search and select
**Use custom**. Leaving `gpt-4o` selected correctly produces `404 model_not_found`.

For the remote Cloudflare path, also add the two per-device Service Auth headers
described above. Do not place the KevinBeLLM token in a custom header; the API Key
field is what Zoo sends as `Authorization: Bearer ...` and stores as a secret.

Zoo Code requires native OpenAI tool calling. KevinBeLLM forwards Zoo's bounded
`tools`, `tool_choice`, streamed `tool_calls`, tool results, usage, and `[DONE]`
events without running the coding tools on the server. The existing Qwen/llama.cpp
deployment already uses native tool calls in the browser assistant.

## 4. Start with conservative Zoo permissions

Authentication stops outsiders from using the GPUs; it does not make an
autonomous coding agent harmless. Zoo executes approved filesystem and terminal
tools on the VS Code computer, and repository text can contain prompt injection.

- Use only trusted workspaces and review diffs and commands.
- Keep terminal, write, browser, and MCP auto-approval off at first.
- In Auto-Approve settings, turn on **Enable destructive command guard** and
  verify it remains enabled; Zoo defaults it off, and helper setup can fail.
- Use Zoo's separate read and write allowlists.
- Keep the repository's default `.rooignore` rules for `.env*`, keys, credential
  directories, databases, logs, and model weights; extend them for any private
  project data. `.rooignore` is not an operating-system sandbox.
- Disable tools the task does not need.
- Treat everything sent in the prompt or repository context as data disclosed to
  the KevinBeLLM host.

## Troubleshooting

- **HTML, 302, or Cloudflare login page:** the `/v1/*` Service Auth application or
  its two custom headers are missing. Never fix this with a Bypass rule.
- **401 `invalid_api_key`:** the KevinBeLLM token is missing, expired, malformed,
  or revoked. Sign in on the web and create a replacement.
- **404 `model_not_found`:** use the exact model ID displayed by KevinBeLLM.
- **400 tool/request error:** turn Image Support off, keep Prompt Caching and
  Enable Reasoning Effort off, and use OpenAI-compatible native tools. Arbitrary llama.cpp fields
  are intentionally rejected.
- **429:** the account rate limit was reached.
- **503:** the one-slot local GPU queue is busy; retry after the supplied delay.

The public API intentionally contains only `GET /v1/models` and
`POST /v1/chat/completions`. Both require a live personal Bearer token. Browser
cookies are not accepted as API credentials, no CORS exception is added, and the
inference URL remains fixed to loopback.

This repository currently bootstraps one owner account and does not provide
public registration. Supporting other people requires an owner-controlled
account/invitation feature; sharing the owner password or token is not supported.

References: [Zoo Code OpenAI-compatible provider](https://docs.zoocode.dev/providers/openai-compatible),
[Zoo Code Marketplace listing](https://marketplace.visualstudio.com/items?itemName=ZooCodeOrganization.zoo-code),
[Zoo Code installation](https://docs.zoocode.dev/getting-started/installing),
[current Zoo Code extension manifest](https://github.com/Zoo-Code-Org/Zoo-Code/blob/main/src/package.json),
[Zoo Code settings export warning](https://docs.zoocode.dev/features/settings-management),
[Zoo Code `.rooignore`](https://docs.zoocode.dev/features/rooignore),
[Cloudflare service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/),
[Cloudflare application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/),
and [Cloudflare Protect with Access audience validation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/).
