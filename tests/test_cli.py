from pathlib import Path
import io
import numpy as np
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest import mock

import copilot_voice_shell.cli as cli
from copilot_voice_shell.cli import (
    apply_replacements,
    load_replacements,
    merge_segment_text,
    normalize_hotkey,
    parse_replacement_pair,
    resolve_send_text,
)
from copilot_voice_shell.polish import cleanup_dictation, polish_text


class CliHelpersTest(unittest.TestCase):
    def test_parse_replacement_pair(self) -> None:
        self.assertEqual(parse_replacement_pair("Scale=skill"), ("Scale", "skill"))

    def test_apply_replacements(self) -> None:
        result = apply_replacements(
            "I want a new Scale for cloud code in copilot",
            {"Scale": "skill", "cloud code": "Claude Code", "copilot": "Copilot"},
        )
        self.assertEqual(result, "I want a new skill for Claude Code in Copilot")

    def test_merge_segment_text(self) -> None:
        self.assertEqual(merge_segment_text([" hello ", "", "world  "]), "hello world")

    def test_load_replacements_from_file_and_flags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "replacements.json"
            path.write_text('{"Scale": "skill", "copilot": "Copilot"}', encoding="utf-8")
            loaded = load_replacements(path, ["github=GitHub"])

        self.assertEqual(
            loaded,
            {"Scale": "skill", "copilot": "Copilot", "github": "GitHub"},
        )

    def test_resolve_send_text_from_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "prompt.txt"
            path.write_text("hello copilot\n", encoding="utf-8")
            self.assertEqual(resolve_send_text(None, path), "hello copilot")

    def test_normalize_hotkey(self) -> None:
        self.assertEqual(normalize_hotkey("cmd+shift+space"), "<cmd>+<shift>+<space>")

    def test_normalize_hotkey_with_letters(self) -> None:
        self.assertEqual(normalize_hotkey("ctrl+alt+r"), "<ctrl>+<alt>+r")

    def test_polish_copilot_instruction(self) -> None:
        result = polish_text("呃 帮我修一下 copilot skill 的 streaming", "copilot")
        self.assertNotIn("请执行下面的语音指令", result)
        self.assertIn("Copilot", result)
        self.assertIn("skill", result)
        self.assertIn("streaming", result)

    def test_cleanup_dictation_normalizes_terms(self) -> None:
        self.assertEqual(cleanup_dictation("cloud code 和 github api"), "Claude Code 和 GitHub API")

    def test_cleanup_removes_prompt_prefix_and_repetition(self) -> None:
        self.assertEqual(
            cleanup_dictation("请执行下面的语音指令：默认打开默认打开 dashboard dashboard"),
            "默认打开 dashboard",
        )

    def test_cleanup_keeps_wrong_scripts_by_default(self) -> None:
        self.assertEqual(cleanup_dictation("优化 Copilot 세션 แล้วทดสอบ"), "优化 Copilot 세션 แล้วทดสอบ")

    def test_cleanup_reduces_single_char_stutter(self) -> None:
        self.assertEqual(cleanup_dictation("我希望你你你继续优化"), "我希望你继续优化")

    def test_polish_adds_sentence_punctuation(self) -> None:
        self.assertTrue(polish_text("默认打开 dashboard", "copilot").endswith("。"))
        self.assertTrue(polish_text("你能不能默认打开 dashboard", "copilot").endswith("？"))

    def test_app_to_polish_mode_mapping(self) -> None:
        from copilot_voice_shell.polish import map_app_to_polish_mode
        self.assertEqual(map_app_to_polish_mode("VS Code", "com.microsoft.VSCode"), "dev")
        self.assertEqual(map_app_to_polish_mode("iTerm2", "com.googlecode.iterm2"), "dev")
        self.assertEqual(map_app_to_polish_mode("WeChat", "com.tencent.xinWeChat"), "im")
        self.assertEqual(map_app_to_polish_mode("Lark", "com.electron.lark"), "im")
        self.assertEqual(map_app_to_polish_mode("Notion", "notion.id"), "notes")
        self.assertEqual(map_app_to_polish_mode("Outlook", "com.microsoft.Outlook"), "email")
        self.assertEqual(map_app_to_polish_mode("Safari", "com.apple.Safari"), "browser")
        self.assertEqual(map_app_to_polish_mode("Chrome", "com.google.Chrome"), "browser")
        self.assertEqual(map_app_to_polish_mode("UnknownApp"), "copilot")

    def test_polish_modes_formatting(self) -> None:
        # Dev and browser modes should NOT end with automatic sentence punctuation in rules engine
        self.assertEqual(polish_text("git status", "dev"), "git status")
        self.assertEqual(polish_text("python handle json", "browser"), "python handle json")
        # Other modes should have standard punctuation in rules engine
        self.assertTrue(polish_text("好的我马上去办", "im").endswith("。"))
        self.assertTrue(polish_text("这个是会议纪要", "notes").endswith("。"))

    def test_desktop_uses_native_overlay_on_macos(self) -> None:
        calls: list[dict] = []

        qt_overlay = types.ModuleType("copilot_voice_shell.qt_overlay")
        qt_overlay.run_qt_overlay = mock.Mock(side_effect=AssertionError("Qt overlay should not be used on macOS fullscreen"))
        native_overlay = types.ModuleType("copilot_voice_shell.overlay")
        native_overlay.run_overlay = lambda **kwargs: calls.append(kwargs)

        with (
            mock.patch.object(cli.sys, "platform", "darwin"),
            mock.patch.object(cli._config, "load_config", return_value=dict(cli._config.DEFAULTS)),
            mock.patch.dict(
                sys.modules,
                {
                    "copilot_voice_shell.qt_overlay": qt_overlay,
                    "copilot_voice_shell.overlay": native_overlay,
                },
            ),
        ):
            cli.main(["desktop", "--backend", "mlx", "--mlx-model", "/tmp/local-mlx-model"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["backend"], "mlx")
        self.assertEqual(calls[0]["mlx_model"], "/tmp/local-mlx-model")

    def test_native_overlay_delivery_flags_use_config_when_unset(self) -> None:
        from copilot_voice_shell.overlay import resolve_delivery_flags

        cfg = {
            "copy_to_clipboard": False,
            "paste_to_active_app": True,
            "submit_to_active_app": False,
        }
        self.assertEqual(resolve_delivery_flags(cfg, None, None, None), (False, True, False))
        self.assertEqual(resolve_delivery_flags(cfg, True, None, None), (True, True, False))
        self.assertEqual(resolve_delivery_flags(cfg, None, False, True), (False, True, True))

    def test_doctor_reports_local_model_stack(self) -> None:
        buf = io.StringIO()
        with (
            mock.patch.object(cli.sys, "platform", "darwin"),
            mock.patch("sys.stdout", buf),
        ):
            cli.run_doctor()

        output = buf.getvalue()
        self.assertIn("Default backend:", output)
        self.assertIn("Default MLX model:", output)
        self.assertIn("Default polish engine:", output)
        self.assertIn("Default Ollama model:", output)
        self.assertIn("faster-whisper:", output)
        self.assertIn("mlx-whisper:", output)
        self.assertIn("ollama:", output)

    def test_stop_streaming_audio_creates_recording_parent(self) -> None:
        session = cli.HotkeySession(
            language="zh",
            model_name="small",
            backend="mlx",
            mlx_model="models/mlx-whisper-large-v3-turbo",
            copy_to_clipboard=False,
            paste_to_active_app=False,
            submit_to_active_app=False,
            plain=True,
            save_text=None,
            hf_endpoint="https://hf-mirror.com",
            replacement_pairs=[],
            replacements_file=None,
        )
        stream = mock.Mock()
        session._audio_stream = stream
        session._audio_chunks = [np.ones((160, 1), dtype=np.float32) * 0.01]
        writes: list[tuple[Path, object, int]] = []

        def fake_write(path: Path, audio: object, samplerate: int) -> None:
            self.assertTrue(path.parent.is_dir())
            writes.append((path, audio, samplerate))

        fake_soundfile = types.SimpleNamespace(write=fake_write)
        with TemporaryDirectory() as temp_dir, mock.patch.dict(
            sys.modules,
            {"soundfile": fake_soundfile},
        ):
            session._current_audio_path = Path(temp_dir) / "missing" / "recording.wav"
            session._stop_streaming_audio()

        stream.stop.assert_called_once()
        stream.close.assert_called_once()
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2], 16_000)

    def test_stop_streaming_audio_rejects_silence(self) -> None:
        session = cli.HotkeySession(
            language="zh",
            model_name="small",
            backend="mlx",
            mlx_model="models/mlx-whisper-large-v3-turbo",
            copy_to_clipboard=False,
            paste_to_active_app=False,
            submit_to_active_app=False,
            plain=True,
            save_text=None,
            hf_endpoint="https://hf-mirror.com",
            replacement_pairs=[],
            replacements_file=None,
        )
        session._audio_stream = mock.Mock()
        session._audio_chunks = [np.zeros((160, 1), dtype=np.float32)]
        session._input_device_label = "WH-1000XM3 (#2)"
        with TemporaryDirectory() as temp_dir:
            session._current_audio_path = Path(temp_dir) / "recording.wav"
            with self.assertRaisesRegex(RuntimeError, "only silence.*WH-1000XM3"):
                session._stop_streaming_audio()

    def test_stop_and_process_recording_stops_preview_worker_on_silence(self) -> None:
        session = cli.HotkeySession(
            language="zh",
            model_name="small",
            backend="mlx",
            mlx_model="models/mlx-whisper-large-v3-turbo",
            copy_to_clipboard=False,
            paste_to_active_app=False,
            submit_to_active_app=False,
            plain=True,
            save_text=None,
            hf_endpoint="https://hf-mirror.com",
            replacement_pairs=[],
            replacements_file=None,
            streaming=True,
        )
        session._audio_stream = mock.Mock()
        session._audio_chunks = [np.zeros((160, 1), dtype=np.float32)]
        session._stream_worker = mock.Mock()
        session._input_device_label = "WH-1000XM3 (#2)"
        with TemporaryDirectory() as temp_dir:
            session._current_audio_path = Path(temp_dir) / "recording.wav"
            with self.assertRaisesRegex(RuntimeError, "only silence.*WH-1000XM3"):
                session._stop_and_process_recording()

        self.assertTrue(session._stream_stop.is_set())
        session._stream_worker.join.assert_called_once_with(timeout=5)

    def test_select_streaming_input_skips_virtual_default(self) -> None:
        session = cli.HotkeySession(
            language="zh",
            model_name="small",
            backend="mlx",
            mlx_model="models/mlx-whisper-large-v3-turbo",
            copy_to_clipboard=False,
            paste_to_active_app=False,
            submit_to_active_app=False,
            plain=True,
            save_text=None,
            hf_endpoint="https://hf-mirror.com",
            replacement_pairs=[],
            replacements_file=None,
        )

        class FakeSoundDevice:
            default = types.SimpleNamespace(device=[1, 0])
            _devices = [
                {"name": "WH-1000XM3", "max_input_channels": 1},
                {"name": "Microsoft Teams Audio", "max_input_channels": 2},
            ]

            @classmethod
            def query_devices(cls, index=None):
                return cls._devices if index is None else cls._devices[index]

        with mock.patch.object(cli._config, "load_config", return_value={"input_device": ""}):
            self.assertEqual(session._select_streaming_input_device(FakeSoundDevice), (0, "WH-1000XM3"))

    def test_select_streaming_input_uses_configured_device(self) -> None:
        session = cli.HotkeySession(
            language="zh",
            model_name="small",
            backend="mlx",
            mlx_model="models/mlx-whisper-large-v3-turbo",
            copy_to_clipboard=False,
            paste_to_active_app=False,
            submit_to_active_app=False,
            plain=True,
            save_text=None,
            hf_endpoint="https://hf-mirror.com",
            replacement_pairs=[],
            replacements_file=None,
        )

        class FakeSoundDevice:
            default = types.SimpleNamespace(device=[0, 0])
            _devices = [
                {"name": "WH-1000XM3", "max_input_channels": 1},
                {"name": "Studio Display Microphone", "max_input_channels": 1},
            ]

            @classmethod
            def query_devices(cls, index=None):
                return cls._devices if index is None else cls._devices[index]

        with mock.patch.object(cli._config, "load_config", return_value={"input_device": "Studio"}):
            self.assertEqual(
                session._select_streaming_input_device(FakeSoundDevice),
                (1, "Studio Display Microphone"),
            )


class ConfigTest(unittest.TestCase):
    def test_max_record_seconds_default(self) -> None:
        from copilot_voice_shell import config

        self.assertEqual(config.DEFAULTS["max_record_seconds"], 120)

    def test_max_record_seconds_override_from_file(self) -> None:
        import os
        from copilot_voice_shell import config

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text('{"max_record_seconds": 30}', encoding="utf-8")
            prev = os.environ.get("COPILOT_VOICE_SHELL_CONFIG")
            os.environ["COPILOT_VOICE_SHELL_CONFIG"] = str(path)
            try:
                cfg = config.load_config(reload=True)
                self.assertEqual(cfg["max_record_seconds"], 30)
            finally:
                if prev is None:
                    os.environ.pop("COPILOT_VOICE_SHELL_CONFIG", None)
                else:
                    os.environ["COPILOT_VOICE_SHELL_CONFIG"] = prev
                config.load_config(reload=True)


if __name__ == "__main__":
    unittest.main()
