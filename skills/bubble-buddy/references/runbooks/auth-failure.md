# Runbook: Azure sign-in / authentication failure

Applies when `backend: azure` (or `polish_engine: azure`) and the user sees
messages like "Not signed in to Azure", "Azure sign-in failed: …", or requests
fail with 401/403.

## 1. Confirm they need Azure at all
- Only the `azure` backend / polish engine requires sign-in. If they intended to
  run fully local, switch `backend` to `faster-whisper` (or `mlx`) — no auth.

## 2. Use the in-app sign-in
- In the overlay, click **"Sign in to Azure" (登录 Azure)**. This opens a browser
  for interactive sign-in and persists an auth record at
  `~/.copilot-voice-shell/auth_record.json`.
- After a successful sign-in it should show "Signed in to Azure".
- NEVER ask the user to paste an API key or token in clear text.

## 3. Sign-in opens but fails
- Verify they signed in with an account that has access to the configured Azure
  resource / tenant.
- Check `azure.endpoint` and `azure.scope` in config point at the right resource
  (see [`../config.md`](../config.md) for valid shapes).
- Corporate networks: a proxy may block the browser flow — retry off-VPN or with
  the proxy configured.

## 4. Was signed in before, now broken
- The cached credential may have expired or the auth record got stale. Delete
  `~/.copilot-voice-shell/auth_record.json` and sign in again.
- Silent-refresh order is: persisted browser cache → `az login`/env →
  on-demand interactive. If all fail, the interactive button is the reset path.

## 5. Wrong-tenant / access denied
- Confirm the tenant of the signed-in account matches the resource's tenant.
- Confirm the account has the needed data-plane role on the Azure OpenAI resource
  (auth uses the user's AAD credential, not an API key).

## 6. Still failing
- Collect the exact `msg.signin_failed` text and `azure.endpoint` (never the
  key). For deeper detail, reference `src/copilot_voice_shell/azure_client.py`
  (sign_in / auth_status / AuthRequiredError) for source-level lookup.
