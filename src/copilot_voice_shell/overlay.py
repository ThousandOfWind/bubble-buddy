from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path

from pynput import keyboard

from .cli import DEFAULT_HOTKEY, HotkeySession, normalize_hotkey


class OverlayState:
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


class SpriteOverlay:
    def __init__(self, state: OverlayState, session: HotkeySession) -> None:
        self.state = state
        self.session = session
        self.root = tk.Tk()
        self.root.title("Copilot Voice Sprite")
        self.root.geometry("360x420+40+40")
        self.root.configure(bg="#0b1020")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.card = tk.Frame(self.root, bg="#121a33", padx=18, pady=18)
        self.card.pack(fill="both", expand=True, padx=14, pady=14)

        self.canvas = tk.Canvas(self.card, width=160, height=160, bg="#121a33", highlightthickness=0)
        self.canvas.pack(pady=(4, 12))
        self.status_label = tk.Label(self.card, text="IDLE", fg="#e7ecff", bg="#121a33", font=("SF Pro Display", 16, "bold"))
        self.status_label.pack()
        self.tip_label = tk.Label(
            self.card,
            text=f"Hotkey: {self.state.snapshot()['hotkey']}",
            fg="#9fb0e0",
            bg="#121a33",
            font=("SF Pro Text", 11),
        )
        self.tip_label.pack(pady=(6, 12))

        self.transcript_label = tk.Label(self.card, text="Transcript", fg="#9fb0e0", bg="#121a33", anchor="w")
        self.transcript_label.pack(fill="x")
        self.transcript_box = tk.Text(
            self.card,
            height=8,
            wrap="word",
            bg="#0a1020",
            fg="#e7ecff",
            relief="flat",
            padx=12,
            pady=10,
        )
        self.transcript_box.pack(fill="both", expand=True, pady=(6, 10))
        self.transcript_box.insert("1.0", "Waiting for speech…")
        self.transcript_box.configure(state="disabled")

        self.error_label = tk.Label(self.card, text="Error", fg="#9fb0e0", bg="#121a33", anchor="w")
        self.error_label.pack(fill="x")
        self.error_value = tk.Label(
            self.card,
            text="No errors.",
            fg="#ffbcc4",
            bg="#121a33",
            justify="left",
            wraplength=300,
            anchor="w",
        )
        self.error_value.pack(fill="x", pady=(6, 0))

        self._drag_start: tuple[int, int] | None = None
        self.root.bind("<ButtonPress-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._on_drag)

        self._draw_sprite("idle")
        self._schedule_refresh()

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        self.session.stop_if_recording()
        self.root.destroy()

    def _schedule_refresh(self) -> None:
        self._refresh()
        self.root.after(250, self._schedule_refresh)

    def _refresh(self) -> None:
        state = self.state.snapshot()
        stage = str(state.get("stage", "idle"))
        plain_text = str(state.get("plain_text", "") or "Waiting for speech…")
        error = str(state.get("error", "") or "No errors.")
        target_app = str(state.get("target_app", "")).strip()

        status_text = stage.upper()
        if stage == "done" and target_app:
            status_text = f"DONE -> {target_app}"
        self.status_label.configure(text=status_text)
        self._draw_sprite(stage)

        self.transcript_box.configure(state="normal")
        self.transcript_box.delete("1.0", "end")
        self.transcript_box.insert("1.0", plain_text)
        self.transcript_box.configure(state="disabled")
        self.error_value.configure(text=error)

    def _draw_sprite(self, stage: str) -> None:
        colors = {
            "idle": "#6ea8fe",
            "recording": "#ff5d73",
            "transcribing": "#ffd166",
            "transcribed": "#ffd166",
            "done": "#57cc99",
            "error": "#ff6b6b",
        }
        fill = colors.get(stage, "#6ea8fe")
        self.canvas.delete("all")
        self.canvas.create_oval(18, 18, 142, 142, fill=fill, outline="")
        self.canvas.create_oval(52, 58, 66, 78, fill="#09111f", outline="")
        self.canvas.create_oval(94, 58, 108, 78, fill="#09111f", outline="")
        if stage == "recording":
            self.canvas.create_oval(68, 92, 92, 116, fill="#09111f", outline="")
        elif stage == "error":
            self.canvas.create_arc(60, 92, 100, 118, start=30, extent=120, style="arc", outline="#09111f", width=5)
        else:
            self.canvas.create_arc(54, 82, 106, 122, start=200, extent=140, style="arc", outline="#09111f", width=5)

    def _start_drag(self, event: tk.Event[tk.Misc]) -> None:
        self._drag_start = (event.x_root, event.y_root)

    def _on_drag(self, event: tk.Event[tk.Misc]) -> None:
        if self._drag_start is None:
            return
        start_x, start_y = self._drag_start
        delta_x = event.x_root - start_x
        delta_y = event.y_root - start_y
        x = self.root.winfo_x() + delta_x
        y = self.root.winfo_y() + delta_y
        self.root.geometry(f"+{x}+{y}")
        self._drag_start = (event.x_root, event.y_root)


def run_overlay(
    *,
    hotkey: str = DEFAULT_HOTKEY,
    language: str,
    model_name: str,
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    submit_to_active_app: bool,
    plain: bool,
    save_text: Path | None,
    hf_endpoint: str,
    replacement_pairs: list[str],
    replacements_file: Path | None,
) -> None:
    state = OverlayState(hotkey)
    should_copy = copy_to_clipboard or not (paste_to_active_app or submit_to_active_app)
    session = HotkeySession(
        language=language,
        model_name=model_name,
        copy_to_clipboard=should_copy,
        paste_to_active_app=paste_to_active_app or submit_to_active_app,
        submit_to_active_app=submit_to_active_app,
        plain=plain,
        save_text=save_text,
        hf_endpoint=hf_endpoint,
        replacement_pairs=replacement_pairs,
        replacements_file=replacements_file,
        status_reporter=state.update,
    )
    listener = keyboard.GlobalHotKeys({normalize_hotkey(hotkey): session.toggle_recording})
    listener.start()

    print(f"Overlay is running. Press {hotkey} to start/stop recording.")
    try:
        SpriteOverlay(state, session).run()
    finally:
        listener.stop()
        session.stop_if_recording()
