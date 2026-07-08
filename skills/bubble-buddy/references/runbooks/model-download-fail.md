# Runbook: Speech model download fails or hangs

Applies to local backends (`backend: faster-whisper` or `mlx`) that download a
model on first use. Symptom: first run hangs, times out, or errors while
fetching the model.

## 1. Identify which model
- `mlx` (Apple Silicon): runtime local path is `mlx_model.path`; download source
  is `mlx_model.repo` (e.g. `mlx-community/whisper-large-v3-turbo`).
- `faster-whisper`: use `faster_whisper_model` only if `speech.backend` is
  `faster-whisper`.

## 2. Network / mirror
- Downloads come from Hugging Face. `mlx_model.hf_endpoint` (or legacy
  `hf_endpoint`) selects the mirror; the default is a mirror
  (`https://hf-mirror.com`) for regions where the main host is slow.
- If downloads stall:
  - Try switching `hf_endpoint` between the mirror and `https://huggingface.co`.
  - Behind a proxy, ensure the environment proxy is set so the download can reach
    the endpoint.

If you have file access, update the user's config directly:

```json
{
  "mlx_model": {
    "repo": "mlx-community/whisper-large-v3-turbo",
    "hf_endpoint": "https://hf-mirror.com"
  }
}
```

If that mirror fails, switch `hf_endpoint` to `https://huggingface.co` and retry.

## 3. Partial / corrupt download
- A killed download can leave a corrupt cache. Clear the Hugging Face cache
  (`~/.cache/huggingface`) and retry so it re-fetches cleanly.

Ask before deleting caches. With approval, remove only the relevant Hugging Face
cache/model directory, not unrelated user files.

## 4. Too large for the machine
- Very large models need significant disk + RAM. Pick a smaller model/repo to get
  running, then scale up.

## 5. Avoid the download entirely
- If the user has network constraints, switch `backend: azure` (cloud, no local
  model) — but that needs Azure sign-in (see `auth-failure.md`).

## 6. Still failing
- Collect: `speech.backend`, `mlx_model.path`/`mlx_model.repo`,
  `faster_whisper_model`, `hf_endpoint`, OS, and the exact
  error. The download is handled by the faster-whisper / mlx libraries; for
  app-side wiring reference `src/copilot_voice_shell/qt_overlay.py`
  (model load path) for source-level lookup.
