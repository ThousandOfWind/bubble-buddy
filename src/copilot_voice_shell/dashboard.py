from __future__ import annotations

import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pynput import keyboard

from .cli import DEFAULT_HOTKEY, HotkeySession, normalize_hotkey


class DashboardState:
    def __init__(self, hotkey: str) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, object] = {
            "stage": "idle",
            "hotkey": hotkey,
            "plain_text": "",
            "audio_path": "",
            "error": "",
            "copied": False,
            "pasted": False,
            "submitted": False,
            "target_app": "",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def update(self, patch: dict[str, object]) -> None:
        with self._lock:
            self._state.update(patch)
            self._state["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)


SPRITE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Copilot Voice Sprite</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b1020;
      --panel: rgba(16, 24, 48, 0.88);
      --text: #e7ecff;
      --muted: #9fb0e0;
      --idle: #6ea8fe;
      --recording: #ff5d73;
      --transcribing: #ffd166;
      --done: #57cc99;
      --error: #ff6b6b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #18274f 0, var(--bg) 60%);
      color: var(--text);
      display: grid;
      place-items: center;
      padding: 24px;
    }
    .shell {
      width: min(920px, 100%);
      display: grid;
      grid-template-columns: 260px 1fr;
      gap: 20px;
      align-items: stretch;
    }
    .card {
      background: var(--panel);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 24px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.35);
      backdrop-filter: blur(18px);
    }
    .sprite-card {
      padding: 24px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 18px;
      text-align: center;
    }
    .sprite {
      width: 160px;
      height: 160px;
      border-radius: 999px;
      position: relative;
      background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.65), rgba(255,255,255,0.06) 35%), var(--idle);
      box-shadow: 0 0 0 10px rgba(255,255,255,0.05), 0 20px 80px rgba(110,168,254,0.35);
      transition: transform .18s ease, background .2s ease, box-shadow .2s ease;
      animation: floaty 2.4s ease-in-out infinite;
    }
    .sprite.recording { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.7), rgba(255,255,255,0.08) 35%), var(--recording); box-shadow: 0 0 0 10px rgba(255,93,115,0.18), 0 20px 80px rgba(255,93,115,0.35); transform: scale(1.03); }
    .sprite.transcribing { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.7), rgba(255,255,255,0.08) 35%), var(--transcribing); box-shadow: 0 0 0 10px rgba(255,209,102,0.18), 0 20px 80px rgba(255,209,102,0.35); }
    .sprite.done { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.7), rgba(255,255,255,0.08) 35%), var(--done); box-shadow: 0 0 0 10px rgba(87,204,153,0.18), 0 20px 80px rgba(87,204,153,0.35); }
    .sprite.error { background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.7), rgba(255,255,255,0.08) 35%), var(--error); box-shadow: 0 0 0 10px rgba(255,107,107,0.18), 0 20px 80px rgba(255,107,107,0.35); }
    .eye {
      position: absolute;
      top: 58px;
      width: 18px;
      height: 22px;
      background: #09111f;
      border-radius: 999px;
    }
    .eye.left { left: 48px; }
    .eye.right { right: 48px; }
    .mouth {
      position: absolute;
      left: 50%;
      bottom: 44px;
      width: 54px;
      height: 24px;
      transform: translateX(-50%);
      border-bottom: 5px solid #09111f;
      border-radius: 0 0 999px 999px;
    }
    .sprite.recording .mouth { border-bottom-color: transparent; width: 24px; height: 24px; border: none; background: #09111f; border-radius: 999px; }
    .sprite.error .mouth { height: 20px; border-bottom: none; border-top: 5px solid #09111f; border-radius: 999px 999px 0 0; bottom: 38px; }
    .title { font-size: 1.2rem; font-weight: 700; }
    .subtitle { color: var(--muted); font-size: .95rem; line-height: 1.45; }
    .info-card { padding: 22px; display: grid; gap: 16px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; }
    button {
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.12);
      color: var(--text);
      padding: 10px 14px;
      border-radius: 999px;
      cursor: pointer;
    }
    button:hover { background: rgba(255,255,255,0.14); }
    .status-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .status-item {
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.08);
    }
    .label { color: var(--muted); font-size: .82rem; text-transform: uppercase; letter-spacing: .08em; }
    .value { margin-top: 6px; font-size: 1rem; word-break: break-word; }
    .output {
      min-height: 140px;
      padding: 18px;
      border-radius: 18px;
      background: rgba(5, 8, 18, 0.68);
      border: 1px solid rgba(255,255,255,0.08);
      white-space: pre-wrap;
      line-height: 1.55;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .error { color: #ffbcc4; }
    @keyframes floaty {
      0%,100% { transform: translateY(0); }
      50% { transform: translateY(-4px); }
    }
    @media (max-width: 820px) {
      .shell { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="card sprite-card">
      <div id="sprite" class="sprite idle">
        <div class="eye left"></div>
        <div class="eye right"></div>
        <div class="mouth"></div>
      </div>
      <div class="title" id="status-title">Idle</div>
      <div class="subtitle">Press <strong id="hotkey-text"></strong> to start and stop recording. Keep your target app focused if you want auto-paste to land there.</div>
    </section>

    <section class="card info-card">
      <div class="toolbar">
        <button onclick="postAction('/api/toggle')">Toggle recording</button>
        <button onclick="postAction('/api/start')">Start</button>
        <button onclick="postAction('/api/stop')">Stop</button>
      </div>

      <div class="status-grid">
        <div class="status-item"><div class="label">Stage</div><div class="value" id="stage"></div></div>
        <div class="status-item"><div class="label">Target app</div><div class="value" id="target-app"></div></div>
        <div class="status-item"><div class="label">Updated</div><div class="value" id="updated-at"></div></div>
        <div class="status-item"><div class="label">Audio file</div><div class="value" id="audio-path"></div></div>
      </div>

      <div>
        <div class="label">Transcript</div>
        <div class="output" id="transcript">Waiting for speech…</div>
      </div>

      <div>
        <div class="label">Errors</div>
        <div class="output error" id="error-box">No errors.</div>
      </div>
    </section>
  </div>

  <script>
    async function postAction(path) {
      await fetch(path, { method: 'POST' });
      await refresh();
    }

    function spriteClass(stage) {
      if (stage === 'recording') return 'sprite recording';
      if (stage === 'transcribing' || stage === 'transcribed') return 'sprite transcribing';
      if (stage === 'done') return 'sprite done';
      if (stage === 'error') return 'sprite error';
      return 'sprite idle';
    }

    async function refresh() {
      const state = await fetch('/api/state').then(r => r.json());
      document.getElementById('sprite').className = spriteClass(state.stage);
      document.getElementById('status-title').textContent = (state.stage || 'idle').toUpperCase();
      document.getElementById('hotkey-text').textContent = state.hotkey || '';
      document.getElementById('stage').textContent = state.stage || '';
      document.getElementById('target-app').textContent = state.target_app || '—';
      document.getElementById('updated-at').textContent = state.updated_at || '—';
      document.getElementById('audio-path').textContent = state.audio_path || '—';
      document.getElementById('transcript').textContent = state.plain_text || 'Waiting for speech…';
      document.getElementById('error-box').textContent = state.error || 'No errors.';
    }

    refresh();
    setInterval(refresh, 700);
  </script>
</body>
</html>
"""


def run_dashboard_server(
    *,
    host: str,
    port: int,
    open_browser: bool,
    hotkey: str,
    language: str,
    model_name: str,
    backend: str,
    mlx_model: str,
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    submit_to_active_app: bool,
    plain: bool,
    save_text: Path | None,
    hf_endpoint: str,
    replacement_pairs: list[str],
    replacements_file: Path | None,
    streaming: bool,
) -> None:
    state = DashboardState(hotkey)
    should_copy = copy_to_clipboard or not (paste_to_active_app or submit_to_active_app)
    session = HotkeySession(
        language=language,
        model_name=model_name,
        backend=backend,
        mlx_model=mlx_model,
        copy_to_clipboard=should_copy,
        paste_to_active_app=paste_to_active_app or submit_to_active_app,
        submit_to_active_app=submit_to_active_app,
        plain=plain,
        save_text=save_text,
        hf_endpoint=hf_endpoint,
        replacement_pairs=replacement_pairs,
        replacements_file=replacements_file,
        status_reporter=state.update,
        streaming=streaming,
    )

    app = create_dashboard_app(state, session)
    hotkey_listener = keyboard.GlobalHotKeys({normalize_hotkey(hotkey): session.toggle_recording})
    hotkey_listener.start()

    dashboard_url = f"http://{host}:{port}"
    print(f"Dashboard running at {dashboard_url}")
    print(f"Global hotkey: {hotkey}")
    if open_browser:
        webbrowser.open(dashboard_url)

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        hotkey_listener.stop()
        session.stop_if_recording()


def create_dashboard_app(state: DashboardState, session: HotkeySession) -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(SPRITE_HTML)

    @app.get("/api/state")
    async def api_state() -> dict[str, object]:
        return state.snapshot()

    @app.post("/api/toggle")
    async def api_toggle() -> dict[str, object]:
        threading.Thread(target=session.toggle_recording, daemon=True).start()
        return {"ok": True}

    @app.post("/api/start")
    async def api_start() -> dict[str, object]:
        threading.Thread(target=session.start_recording, daemon=True).start()
        return {"ok": True}

    @app.post("/api/stop")
    async def api_stop() -> dict[str, object]:
        threading.Thread(target=session.stop_recording, daemon=True).start()
        return {"ok": True}

    return app
