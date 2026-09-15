# Runbook: Azure sign-in / authentication failure

Applies when `backend: azure` (or `polish_engine: azure`) and the user sees
messages like "Not signed in to Azure", "Azure sign-in failed: …", or requests
fail with 401/403. First identify the failing component/provider: for Copilot or
Codex account failures use [`../accounts.md`](../accounts.md), not this runbook.

## 1. Confirm they need Azure at all
- Only an active `azure` recognizer/polisher needs **Azure** sign-in. A local
  recognizer can still use Copilot/Azure polish. Confirm both choices before
  changing config; fully offline means local recognition plus off/rules/Ollama.

## 2. Use the in-app sign-in
- **Qt desktop:** the **Sign in to Azure** banner appears above the pet when
  signed out, in either collapsed or expanded state; it hides after sign-in.
- **Legacy native macOS overlay:** expand the pet, then click **Account**. This
  control is hidden while collapsed or when no provider is required, and remains
  available after sign-in in the expanded view. Mixed-provider setups can route
  to another required account first; follow [`../accounts.md`](../accounts.md).
- Click it: this opens a browser for interactive sign-in and persists an auth
  record at `~/.bubble-buddy/auth_record.json`.
- After a successful sign-in it should show "Signed in to Azure".
- If the button never appears, the app may not be using Azure yet — confirm
  `backend: azure` (or `polish_engine: azure`) in config; see the "Azure
  first-run setup" section of [`../install.md`](../install.md).
- NEVER ask the user to paste an API key or token in clear text.

## 3. Sign-in opens but fails
- Verify they signed in with an account that has access to the configured Azure
  resource / tenant.
- Check `azure.endpoint` and `azure.scope` in config point at the right resource
  (see [`../config.md`](../config.md) for valid shapes).
- Corporate networks: a proxy may block the browser flow — retry off-VPN or with
  the proxy configured.

## 4. Was signed in before, now broken
- Try the in-app sign-in first. Do not delete an auth record as the default fix;
  only reset it with consent after diagnosing stale state. Token renewal cannot
  extend an expired resource role/PIM assignment or bypass tenant sign-in policy.
- Silent-refresh order is: persisted browser cache → `az login`/env →
  on-demand interactive. If all fail, the interactive button is the reset path.

## 5. Wrong-tenant: 400 "Token tenant ... does not match resource tenant"
This is a very common corporate case: the user is signed in to (or `az login`'d
to) their **home/corp tenant**, but the Azure OpenAI resource lives in a
**different tenant**, so the minted token is for the wrong tenant and the resource
rejects it with HTTP 400 `Token tenant <id> does not match resource tenant`.

**Do NOT tell the user to just switch accounts, and do NOT say "tenant isn't a
config key."** Bubble Buddy resolves the *resource* tenant and steers every
credential (browser sign-in + `az`/`azd` CLI) at it:
- **v0.1.6+ auto-discovers** the resource tenant from `azure.endpoint` (via the
  `WWW-Authenticate` challenge) — usually **no config needed**. If it can't reach
  the endpoint (offline/proxy/firewall), set it explicitly.
- Set the resource's tenant GUID with **`azure.tenant_id`** (also accepted:
  `azure.tenant`, a top-level `tenant_id`/`tenant`, or the `AZURE_TENANT_ID` env
  var). Example:
  ```json
  { "azure": { "tenant_id": "<resource-tenant-guid>" } }
  ```
- After setting it (or upgrading), **sign in again (🔑)** so the cached token is
  re-minted for the right tenant.
- The diagnostics log prints the token's tenant, the effective resource tenant,
  and which credential was used — read it to confirm which tenant is being used.
- Finding the GUID: it's the tenant that owns the Azure OpenAI resource (Azure
  portal → the resource → its subscription's directory), not necessarily the
  user's default sign-in tenant.
- Also confirm the account has the needed data-plane role on the resource.

## 6. Still failing
- Collect the exact `msg.signin_failed` text and `azure.endpoint` (never the
  key). For deeper detail, reference `src/bubble_buddy/azure_client.py`
  (sign_in / auth_status / AuthRequiredError) for source-level lookup.
