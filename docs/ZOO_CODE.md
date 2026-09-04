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

To make the private forward automatic, install its per-user Windows logon task
once from the repository root:

```powershell
.\scripts\windows\Install-KevinBeLLMAutoForward.ps1
```

The task starts a hidden, loopback-only forward at sign-in and reconnects after
Wi-Fi changes, sleep, or temporary server unavailability. It runs as the current
user without elevation, uses the existing key and pinned SSH host identity, and
stores no password or KevinBeLLM/Zoo token. The server must already accept the
key without an interactive prompt. Inspect it with:

```powershell
.\scripts\windows\Get-KevinBeLLMAutoForwardStatus.ps1
```

Remove it with `Uninstall-KevinBeLLMAutoForward.ps1`. The scheduled action
references this checkout by absolute path; rerun the installer after moving the
repository. The foreground command remains useful for one-off diagnostics.

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
current Zoo Code 3.81 package declares that minimum even though the install
page still mentions an older VS Code baseline:

```powershell
code --install-extension ZooCodeOrganization.zoo-code
```

Or open Extensions (`Ctrl+Shift+X`), search for **Zoo Code**, verify the publisher
is **Zoo Code Organization** (`ZooCodeOrganization`), and install it. The
official installation instructions are at
<https://docs.zoocode.dev/getting-started/installing>.

In Zoo Code settings, enter the exact values shown on KevinBeLLM's **Zoo Code
access** page:

| Setting | Value |
| --- | --- |
| API Provider | `OpenAI Compatible` |
| Base URL | `http://127.0.0.1:3000/v1` or the configured HTTPS URL |
| API Key | The reusable `kbm_v1_...` API key whose value is shown once |
| Model | Explicitly select `kevinbellm-27b` (or the value shown by the page) |
| Context Window Size | `45056` |
| Max Output Tokens | `16384` |
| Image Support | Off (actively turn this off; Zoo defaults it on) |
| Prompt Caching | Off |
| Enable Reasoning Effort | On; choose `Max` for Qwen's `xhigh` tier (off also yields `xhigh`; `Minimal` is rejected) |
| Enable streaming | On |
| Include max output tokens | On |

Zoo's Model picker initially defaults to `gpt-4o`. After entering the Base URL
and API Key, explicitly choose the authenticated model returned by KevinBeLLM.
If it is not listed, type the exact displayed ID into Model search and select
**Use custom**. Leaving `gpt-4o` selected correctly produces `404 model_not_found`.

### Reasoning effort

Qwen3.8 officially supports three thinking depths: `low`, `medium`, and `xhigh`.
The gateway defaults an omitted `reasoning_effort` to `xhigh` and forwards the
selected official tier to llama.cpp. Zoo and other OpenAI clients commonly call
their top choices `high` or `max`; the gateway normalizes both aliases to
Qwen's `xhigh`, so only an official spelling reaches the model. Other values,
including `minimal`, are rejected. An explicit `reasoning_effort: none` remains
the per-request opt-out and selects Qwen's non-thinking mode.

For every request the gateway also supplies the template flags explicitly:
`enable_thinking=true` for the three thinking tiers or `false` for `none`, plus
`preserve_thinking=true` in both modes. A bounded `reasoning_content` string is
accepted only on assistant messages and forwarded when the client returns it in
later turns. This lets Qwen retain prior reasoning for agent consistency and
prefix-cache reuse without exposing arbitrary chat-template controls to clients.
Whether Zoo displays the streamed `reasoning_content` separately is a client UI
concern.

`ZOO_ENABLE_THINKING` defaults to `true` and acts as a deployment policy gate.
Setting it to `false` rejects omitted or non-`none` effort with a 400; clients
must then send `reasoning_effort: none` explicitly. Omitting the request field
never silently downgrades quality.

The gateway fills every omitted sampler with Qwen's official per-mode values:

| Mode | `temperature` | `top_p` | `top_k` | `min_p` | `presence_penalty` | `repeat_penalty` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Thinking (`low`/`medium`/`xhigh`) | `1.0` | `0.95` | `20` | `0.0` | `0.0` | `1.0` |
| Non-thinking (`none`) | `0.7` | `0.8` | `20` | `0.0` | `1.5` | `1.0` |

Each client-supplied sampling field overrides only its own default after type
and range validation. `top_k`, `min_p`, and llama.cpp's `repeat_penalty` spelling
are supported in addition to the standard OpenAI fields; arbitrary llama.cpp
controls and client-supplied `chat_template_kwargs` remain rejected.

Deep `xhigh` reasoning can consume much of an output allowance before emitting
the answer or a tool call. Measured on the deployed model on 2026-09-02, an
ordinary "write a function and ten tests" request thought for about 12,200
tokens before answering, which is why the deployment runs at the 16,384-token
maximum: that leaves a 12,288-token thinking budget per turn and 28,672 tokens
of input room. The gateway attaches a
per-request reasoning budget to every thinking request: the client's output
allowance minus a 4,096-token answer reserve (or half the allowance when it is
smaller). llama.cpp counts only thinking tokens against it and, when it runs
out, injects the server's `REASONING_BUDGET_MESSAGE`, closes the thinking
block, and lets the model answer or call a tool with the reserved remainder
instead of returning empty content at `max_tokens`. Clients cannot set the
budget fields themselves. `ZOO_MAX_OUTPUT_TOKENS` cannot go above 16,384; if
long coding turns are still truncated, shorten the conversation instead.
Keep in mind that output and input share the 45,056-token context, along with
tool schemas, file contents, preserved reasoning, and conversation history.

`ZOO_MAX_OUTPUT_TOKENS` is a server-side ceiling, and Zoo's own **Max Output
Tokens** field is what the client actually sends. Raise the server first: a
client value above the ceiling is refused with `400 Output tokens must be
between 1 and <ceiling>`. After redeploying, the **Zoo Code access** page shows
the new ceiling to copy into Zoo. Raising the ceiling also spends context that
input can no longer use, because output and input share the same 45,056 tokens.

For the remote Cloudflare path, also add the two per-device Service Auth headers
described above. Do not place the KevinBeLLM token in a custom header; the API Key
field is what Zoo sends as `Authorization: Bearer ...` and stores as a secret.

Zoo Code requires native OpenAI tool calling. KevinBeLLM forwards Zoo's bounded
`tools`, `tool_choice`, streamed `tool_calls`, tool results, assistant
`reasoning_content`, usage, and `[DONE]` events without running the coding tools
on the server. The existing Qwen/llama.cpp deployment already uses native tool
calls in the browser assistant.

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
- **400 tool/request error:** turn Image Support off, keep Prompt Caching off, and
  use OpenAI-compatible native tools. Arbitrary llama.cpp fields are intentionally
  rejected.
- **400 on `reasoning_effort`:** use `low`, `medium`, `xhigh`, or `none` (`high`
  and `max` are accepted as `xhigh`). If the deployment set
  `ZOO_ENABLE_THINKING=false`, send `none` explicitly or re-enable thinking.
  See [Reasoning effort](#reasoning-effort).
- **429:** the account rate limit was reached.
- **503:** the one-slot local GPU queue is busy; retry after the supplied delay.

The public API intentionally contains only `GET /v1/models` and
`POST /v1/chat/completions`. Both require a live personal Bearer token. Browser
cookies are not accepted as API credentials, no CORS exception is added, and the
inference URL remains fixed to loopback.

This repository currently bootstraps one owner account and does not provide
public registration. Supporting other people requires an owner-controlled
account/invitation feature; sharing the owner password or token is not supported.

References: [Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[Zoo Code OpenAI-compatible provider](https://docs.zoocode.dev/providers/openai-compatible),
[Zoo Code Marketplace listing](https://marketplace.visualstudio.com/items?itemName=ZooCodeOrganization.zoo-code),
[Zoo Code installation](https://docs.zoocode.dev/getting-started/installing),
[current Zoo Code extension manifest](https://github.com/Zoo-Code-Org/Zoo-Code/blob/main/src/package.json),
[Zoo Code settings export warning](https://docs.zoocode.dev/features/settings-management),
[Zoo Code `.rooignore`](https://docs.zoocode.dev/features/rooignore),
[Cloudflare service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/),
[Cloudflare application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/),
and [Cloudflare Protect with Access audience validation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/).
