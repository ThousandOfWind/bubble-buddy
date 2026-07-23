# Azure OpenAI backend (cloud transcription + polishing)

Set `backend` to `azure` (transcription) and/or `polish_engine` to `azure` (LLM
cleanup) in `config.json`, and fill in the `azure` section:

```json
{
  "backend": "azure",
  "polish": "copilot",
  "polish_engine": "azure",
  "azure": {
    "endpoint": "https://<your-resource>.cognitiveservices.azure.com/",
    "api_version": "2025-03-01-preview",
    "auth": "aad",
    "transcribe_deployment": "gpt-4o-transcribe",
    "transcribe_mode": "batch",
    "realtime_api_version": "2025-04-01-preview",
    "chat_deployment": "gpt-4.1"
  }
}
```

## Transcription modes

`transcribe_mode` controls how audio is transcribed:

- `batch` (default): one request, result returned when the whole clip is processed.
- `stream`: server-sent streaming of the transcription response (partial text as it arrives).
- `realtime`: uses the Azure OpenAI **Realtime API** (WebSocket) transcription session.
  It needs a realtime-capable api-version — set via `realtime_api_version`
  (the configured default `2025-04-01-preview` works). Supported api-versions
  vary by resource; if you see `400` errors, pick a version known to work for
  your resource or check the Azure OpenAI docs.
  Requires the `websockets` package (already a dependency).

## Authentication

Authentication defaults to `aad`, which uses your signed-in Azure user credential —
no secret is stored or committed. Sign-in is resolved silently in this order:

1. a **persisted browser sign-in** (an OS-encrypted token cache under
   `~/.bubble-buddy`, so it survives restarts — no daily re-login),
2. an existing `az login` / environment / managed-identity credential,
3. a one-time **interactive browser sign-in** (no Azure CLI required).

In the desktop overlay, if you are not signed in a **🔑 登录 Azure** button appears;
clicking it opens the system browser once and then persists the session. The hot
recording path and background token refresh never open a browser unexpectedly. To
use an API key instead, set `"auth": "api_key"` and put the key in the env var named
by `api_key_env` (default `AZURE_OPENAI_API_KEY`).

### Multi-tenant (`Token tenant ... does not match resource tenant`)

If your Azure OpenAI resource lives in a **different AAD tenant** than the one your
account signs into by default (common when you also `az login` to a corporate
tenant), tokens minted for your home tenant are rejected with HTTP 400
`Token tenant <id> does not match resource tenant`. Set the resource's tenant so
every credential is steered at it:

```json
{
  "azure": {
    "tenant_id": "<the resource's tenant id (GUID)>"
  }
}
```

The tenant is passed to the browser sign-in **and** to the `az` / `azd` CLI
credentials. Bubble Buddy also inspects each acquired token's `tid` claim and
discards any minted for a different tenant (logged in the diagnostics log), falling
back to a proper sign-in instead of failing mid-request. After setting `tenant_id`,
sign in again (the 🔑 button) so the cached token is re-minted for the right tenant.


## Running from the command line

Flags override config:

```bash
uv run bubble-buddy transcribe recordings/example.wav \
  --backend azure --polish copilot --polish-engine azure --plain
```
