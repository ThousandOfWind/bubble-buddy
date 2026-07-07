# Runbook: Speech model download fails or hangs

Applies to local backends (`backend: faster-whisper` or `mlx`) that download a
model on first use. Symptom: first run hangs, times out, or errors while
fetching the model.

## 1. Identify which model
- `faster-whisper`: uses `model` (e.g. `small`, `medium`).
- `mlx` (Apple silicon): uses `mlx_model` (e.g.
  `mlx-community/whisper-large-v3-turbo`).

## 2. Network / mirror
- Downloads come from Hugging Face. `hf_endpoint` selects the mirror; the default
  is a mirror (`https://hf-mirror.com`) for regions where the main host is slow.
- If downloads stall:
  - Try switching `hf_endpoint` between the mirror and `https://huggingface.co`.
  - Behind a proxy, ensure the environment proxy is set so the download can reach
    the endpoint.

## 3. Partial / corrupt download
- A killed download can leave a corrupt cache. Clear the Hugging Face cache
  (`~/.cache/huggingface`) and retry so it re-fetches cleanly.

## 4. Too large for the machine
- Very large models need significant disk + RAM. Pick a smaller `model`
  (e.g. `small`) to get running, then scale up.

## 5. Avoid the download entirely
- If the user has network constraints, switch `backend: azure` (cloud, no local
  model) — but that needs Azure sign-in (see `auth-failure.md`).

## 6. Still failing
- Collect: `backend`, `model`/`mlx_model`, `hf_endpoint`, OS, and the exact
  error. The download is handled by the faster-whisper / mlx libraries; for
  app-side wiring reference `src/copilot_voice_shell/qt_overlay.py`
  (model load path) for source-level lookup.
