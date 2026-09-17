"""Local drafts: deterministic audio/ASR stand-ins, no mic/model/network calls."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from module_stubs import stub_modules
from PySide6.QtCore import Qt

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from bubble_buddy import config
from bubble_buddy.local_preview import AudioBuffer, LocalWhisper, PauseSchedule, PreviewDecoder, RollingDraft, SAMPLE_RATE, speech_timestamps
from bubble_buddy.qt_overlay import AudioRecorder, LocalPreviewWorker, TranscribeWorker, VoiceDesktop, _field_applies


def samples(seconds):
    return round(seconds * SAMPLE_RATE)


def speech(*ranges):
    return [{"start": samples(a), "end": samples(b)} for a, b in ranges]


def audio(seconds):
    return np.full(samples(seconds), 0.1, dtype=np.float32)


class PauseScheduleTest(unittest.TestCase):
    def test_silence_never_requests_recognition(self):
        schedule = PauseSchedule()
        for seconds in (1, 4, 20, 120):
            self.assertFalse(schedule.ready(samples(seconds), []))

    def test_short_hesitation_does_not_cut_a_phrase(self):
        schedule = PauseSchedule()
        self.assertFalse(schedule.ready(samples(1.15), speech((0, 1))))
        self.assertFalse(schedule.ready(samples(2.6), speech((0, 1), (1.2, 2.4))))
        self.assertTrue(schedule.ready(samples(3.0), speech((0, 1), (1.2, 2.4))))

    def test_pause_debounce_adapts_to_recent_gaps(self):
        ranges = speech((0, 1), (1.5, 2.6))
        schedule = PauseSchedule()
        self.assertFalse(schedule.ready(samples(3.3), ranges))  # .7s < adapted .9s
        self.assertTrue(schedule.ready(samples(3.55), ranges))

    def test_tiny_utterance_waits_but_eventually_shows(self):
        schedule = PauseSchedule()
        ranges = speech((0.2, 0.6))
        self.assertFalse(schedule.ready(samples(1.3), ranges))
        self.assertTrue(schedule.ready(samples(1.85), ranges))

    def test_continuous_speech_gets_provisional_updates(self):
        schedule = PauseSchedule()
        self.assertFalse(schedule.ready(samples(3.0), speech((0, 3.0))))
        self.assertTrue(schedule.ready(samples(3.5), speech((0, 3.5))))
        self.assertFalse(schedule.ready(samples(4), speech((0, 4))))
        self.assertTrue(schedule.ready(samples(7), speech((0, 7))))

    def test_long_silence_does_not_queue_repeated_decodes(self):
        schedule = PauseSchedule()
        ranges = speech((0, 2))
        self.assertTrue(schedule.ready(samples(2.7), ranges))
        for seconds in (3, 4, 8, 30):
            self.assertFalse(schedule.ready(samples(seconds), ranges))
        # Speech after a long thinking pause still yields another whole-take draft.
        self.assertTrue(schedule.ready(samples(32), speech((0, 2), (30, 31.3))))


class BufferAndDecoderTest(unittest.TestCase):
    def test_snapshots_are_owned_and_take_isolation_survives_restart(self):
        first, second = AudioBuffer(), AudioBuffer()
        chunk = audio(1).reshape(-1, 1)
        first.append(chunk)
        chunk[:] = 0
        snapshot = first.snapshot()
        self.assertTrue(np.all(snapshot == 0.1))
        snapshot[:] = -1
        second.append(audio(2))
        self.assertEqual(first.snapshot().size, samples(1))
        self.assertTrue(np.all(first.snapshot() == 0.1))
        self.assertEqual(second.snapshot().size, samples(2))

    def test_bounded_snapshot_only_materializes_intersecting_chunks(self):
        buffer = AudioBuffer()
        original = np.arange(samples(100), dtype=np.float32)
        for chunk in np.array_split(original, 1000):
            buffer.append(chunk)
        concatenate = np.concatenate
        with patch("bubble_buddy.local_preview.np.concatenate", wraps=concatenate) as join:
            self.assertEqual(buffer.sample_count, original.size)
            join.assert_not_called()
            result = buffer.snapshot(samples(90.05), samples(95.05))
        np.testing.assert_array_equal(result, original[samples(90.05):samples(95.05)])
        self.assertLessEqual(sum(part.size for part in join.call_args.args[0]), samples(5.2))
        self.assertEqual(buffer.snapshot(samples(100), samples(110)).size, 0)

    def test_missing_vad_uses_localized_local_engine_error(self):
        from bubble_buddy.i18n import t
        with stub_modules({"faster_whisper": None, "faster_whisper.vad": None}), \
                self.assertRaises(RuntimeError) as caught:
            speech_timestamps(audio(1))
        self.assertEqual(str(caught.exception), t("msg.local_engine_missing"))

    def test_unchanged_audio_does_not_copy_or_schedule_again(self):
        buffer = AudioBuffer()
        buffer.append(audio(3))
        recognizer = Mock()
        recognizer.transcribe.return_value = [SimpleNamespace(text="draft")]
        worker = LocalPreviewWorker(buffer, recognizer, "zh", [], None)
        worker._stop_event = Mock()
        worker._stop_event.is_set.return_value = False
        worker._stop_event.wait.side_effect = [False, False, True]
        detector = lambda data: speech((0, 2))
        with patch.object(buffer, "snapshot", wraps=buffer.snapshot) as snapshot, \
                patch("bubble_buddy.qt_overlay.PreviewDecoder", side_effect=lambda r, l, c: PreviewDecoder(r, l, c, detector)):
            worker.run()
        snapshot.assert_called_once_with(0, samples(3))
        recognizer.transcribe.assert_called_once()
        self.assertTrue(all(call.args == (0.5,) for call in worker._stop_event.wait.call_args_list))

    def test_short_decoder_retains_earlier_context_for_revision(self):
        recognizer = Mock()
        recognizer.transcribe.side_effect = [
            [SimpleNamespace(text="错误")], [SimpleNamespace(text="正确的完整句子")],
        ]
        detector = lambda data: speech((0, data.size / SAMPLE_RATE - 0.7))
        decoder = PreviewDecoder(recognizer, "zh", lambda: False, detector)
        self.assertEqual(decoder.update(audio(3))[0].text, "错误")
        self.assertEqual(decoder.update(audio(10))[0].text, "正确的完整句子")
        calls = recognizer.transcribe.call_args_list
        self.assertEqual([c.args[0].size for c in calls], [samples(3), samples(10)])
        self.assertEqual(calls[1].args[0][0], np.float32(0.1))
        self.assertIsNone(decoder.update(audio(10)))
        self.assertEqual(recognizer.transcribe.call_count, 2)

    def test_cancelled_decoder_does_no_vad_or_inference(self):
        detector, recognizer = Mock(), Mock()
        decoder = PreviewDecoder(recognizer, None, lambda: True, detector)
        self.assertIsNone(decoder.update(audio(3)))
        detector.assert_not_called()
        recognizer.transcribe.assert_not_called()

    def test_worker_coalesces_to_latest_snapshot_and_replaces_draft(self):
        buffer = AudioBuffer()
        buffer.append(audio(3))
        recognizer = Mock()
        seen_lengths = []

        def infer(data, *_args, **_kwargs):
            seen_lengths.append(data.size)
            if len(seen_lengths) == 1:
                # Simulate capture continuing during slow ASR: no queued 4s/5s jobs.
                buffer.append(audio(1))
                buffer.append(audio(1))
                buffer.append(audio(3))
                return [SimpleNamespace(text="错误的前半句")]
            return [SimpleNamespace(text="修正的前半句和后半句")]

        recognizer.transcribe.side_effect = infer
        worker = LocalPreviewWorker(buffer, recognizer, "zh", [], None)
        worker._stop_event = Mock()
        worker._stop_event.is_set.return_value = False
        worker._stop_event.wait.side_effect = [False, False, True]
        output, failures = [], []
        worker.partial.connect(output.append)
        worker.failed.connect(failures.append)
        detector = lambda data: speech((0, data.size / SAMPLE_RATE - 0.7))
        with patch("bubble_buddy.qt_overlay.PreviewDecoder", side_effect=lambda r, l, c: PreviewDecoder(r, l, c, detector)), \
                patch("bubble_buddy.qt_overlay.polish_text") as polish:
            worker.run()
        self.assertEqual(seen_lengths, [samples(3), samples(8)])
        self.assertEqual(output, ["错误的前半句", "修正的前半句和后半句"])
        self.assertEqual(failures, [])
        polish.assert_not_called()

    def test_worker_preview_error_preserves_audio_for_batch_final(self):
        buffer = AudioBuffer()
        buffer.append(audio(3))
        worker = LocalPreviewWorker(buffer, Mock(), "zh", [], None)
        worker._stop_event = Mock()
        worker._stop_event.is_set.return_value = False
        worker._stop_event.wait.return_value = False
        output, errors = [], []
        worker.partial.connect(output.append)
        worker.failed.connect(errors.append)
        with patch("bubble_buddy.qt_overlay.PreviewDecoder") as decoder:
            decoder.return_value.observation_samples = 20 * SAMPLE_RATE
            decoder.return_value.update.side_effect = RuntimeError("VAD unavailable")
            worker.run()
        self.assertEqual(output, [])
        self.assertEqual(errors, ["RuntimeError: VAD unavailable"])
        self.assertEqual(buffer.snapshot().size, samples(3))

    def test_cancellation_during_inference_suppresses_its_result(self):
        buffer = AudioBuffer()
        buffer.append(audio(3))
        recognizer = Mock()
        worker = LocalPreviewWorker(buffer, recognizer, "zh", [], None)
        output, errors = [], []
        worker.partial.connect(output.append, Qt.ConnectionType.DirectConnection)
        worker.failed.connect(errors.append, Qt.ConnectionType.DirectConnection)

        def cancelled_result(*args, **kwargs):
            worker.stop()
            return [SimpleNamespace(text="too late")]

        recognizer.transcribe.side_effect = cancelled_result
        self.addCleanup(recognizer.reset_mock, side_effect=True)
        detector = lambda data: speech((0, 2))
        with patch("bubble_buddy.qt_overlay.PreviewDecoder", side_effect=lambda r, l, c: PreviewDecoder(r, l, c, detector)):
            # Exercise interruption on a real QThread, not a manual run() call
            # whose native thread has never started.
            worker.start()
            finished = worker.wait(3000)
            worker.stop()
            self.assertTrue(worker.wait(3000))
            self.assertTrue(finished)
        # Release test-owned signal connections after the native thread ends.
        worker.partial.disconnect()
        worker.failed.disconnect()
        recognizer.reset_mock(side_effect=True)
        self.assertEqual(errors, [])
        self.assertEqual(output, [])


class RollingDraftTest(unittest.TestCase):
    @staticmethod
    def segment(items):
        words = [SimpleNamespace(start=start, end=end, word=" " + text) for start, end, text in items]
        return [SimpleNamespace(text="".join(w.word for w in words), words=words)]

    def test_forty_second_take_has_bounded_inference_and_no_lost_or_duplicate_words(self):
        recognizer = Mock()
        windows = []
        detector = lambda data: speech((0, data.size / SAMPLE_RATE - 0.7))
        decoder = PreviewDecoder(recognizer, "en", lambda: False, detector)

        def infer(data, *_args, **_kwargs):
            start = decoder.draft.offset // SAMPLE_RATE
            count = data.size // SAMPLE_RATE
            windows.append((start, count))
            items = [(i, i + 1, "corrected" if start + i == 2 and len(windows) > 1 else f"w{start+i}")
                     for i in range(count)]
            return self.segment(items)

        recognizer.transcribe.side_effect = infer
        for end in range(4, 41, 4):
            offset = max(0, end - 20)
            result = decoder.update(audio(end - offset), offset=samples(offset))
            expected = ["corrected" if i == 2 and end > 4 else f"w{i}" for i in range(end)]
            self.assertEqual(result[0].text.split(), expected)
        self.assertTrue(all(count <= 12 for start, count in windows))
        self.assertGreater(windows[-1][0], 0)
        for (old_start, old_size), (new_start, _) in zip(windows, windows[1:]):
            self.assertGreaterEqual(old_start + old_size - new_start, min(6, old_size))

    def test_long_silence_can_roll_without_dropping_the_prior_phrase(self):
        draft = RollingDraft()
        draft.accept(self.segment([(0, 1, "hello"), (1, 2, "world")]), samples(3))
        start = draft.window_start(samples(30), speech((0, 2), (28, 29)))
        self.assertEqual(start, samples(24))
        result = draft.accept(self.segment([(4, 5, "again")]), samples(30))
        self.assertEqual(result[0].text, "hello world again")

    def test_falling_far_behind_fails_preview_instead_of_skipping_speech(self):
        recognizer = Mock()
        recognizer.transcribe.return_value = self.segment([(0, 1, "first"), (1, 2, "phrase")])
        detector = lambda data: speech((0, data.size / SAMPLE_RATE - 0.7))
        decoder = PreviewDecoder(recognizer, "en", lambda: False, detector)
        decoder.update(audio(3))
        with self.assertRaisesRegex(RuntimeError, "cannot keep up"):
            decoder.update(audio(40))
        self.assertEqual(recognizer.transcribe.call_count, 1)
        self.assertEqual(decoder.draft.offset, 0)

    def test_bounded_observation_remembers_a_phrase_across_long_silence(self):
        cursor = {"offset": 0, "end": 0}
        def detector(data):
            lo, hi = cursor["offset"], cursor["end"]
            return [{"start": max(a, lo) - lo, "end": min(b, hi) - lo}
                    for a, b in ((0, samples(2)), (samples(38), samples(39)))
                    if a < hi and b > lo]
        recognizer = Mock()
        decoder = PreviewDecoder(recognizer, "en", lambda: False, detector)
        def infer(data, *_args, **_kwargs):
            if decoder.draft.offset == 0:
                return self.segment([(0, 1, "hello"), (1, 2, "world")])
            start = 38 - decoder.draft.offset / SAMPLE_RATE
            end = min(39, cursor["end"] / SAMPLE_RATE) - decoder.draft.offset / SAMPLE_RATE
            return self.segment([(start, end, "again")])
        recognizer.transcribe.side_effect = infer
        latest = ""
        for seconds in range(3, 41):
            offset = max(0, seconds - 20)
            cursor.update(offset=samples(offset), end=samples(seconds))
            result = decoder.update(audio(seconds - offset), offset=samples(offset))
            if result:
                latest = result[0].text
        self.assertEqual(latest, "hello world again")
        self.assertGreater(decoder.draft.offset, samples(20))

    def test_unobserved_window_gap_is_not_assumed_to_be_silence(self):
        recognizer = Mock()
        recognizer.transcribe.return_value = self.segment([(0, 1, "first")])
        decoder = PreviewDecoder(recognizer, "en", lambda: False, lambda data: speech((0, 2)))
        decoder.update(audio(3))
        with self.assertRaisesRegex(RuntimeError, "cannot keep up"):
            decoder.update(audio(20), offset=samples(10))
        self.assertEqual(recognizer.transcribe.call_count, 1)

    def test_incomplete_alignment_cannot_silently_discard_words(self):
        draft = RollingDraft()
        segments = self.segment([(0, 1, "only")])
        segments[0].text = "only part of the sentence"
        self.assertEqual(draft.accept(segments, samples(10))[0].text, "only part of the sentence")
        with self.assertRaisesRegex(RuntimeError, "cannot keep up"):
            draft.window_start(samples(14), speech((0, 14)))
        self.assertEqual(draft.prefix, "")

    def test_empty_decode_does_not_erase_saved_mutable_words(self):
        draft = RollingDraft()
        draft.accept(self.segment([(0, 1, "keep")]), samples(3))
        self.assertEqual(draft.accept([], samples(7)), [])
        self.assertEqual([w.text for w in draft.words], [" keep"])
        self.assertEqual(draft.decoded_end, samples(3))

    def test_chinese_word_boundary_does_not_insert_english_spaces(self):
        draft = RollingDraft()
        words = [SimpleNamespace(start=i, end=i+1, word=char) for i, char in enumerate("前面的文字后面改")]
        draft.accept([SimpleNamespace(text="前面的文字后面改", words=words)], samples(10))
        start = draft.window_start(samples(14), speech((0, 14)))
        self.assertEqual(start, samples(4))
        new = [SimpleNamespace(text="字可以再修订", words=[])]
        self.assertEqual(draft.accept(new, samples(14))[0].text, "前面的文字可以再修订")


class ModelReuseTest(unittest.TestCase):
    def test_model_loaded_once_and_final_uses_full_quality_settings(self):
        model = Mock()
        model.transcribe.side_effect = lambda *a, **k: (iter([SimpleNamespace(text="text")]), None)
        factory = Mock(return_value=model)
        with stub_modules({"faster_whisper": SimpleNamespace(WhisperModel=factory)}):
            engine = LocalWhisper("small")
            engine.warmup(lambda: False)
            model.transcribe.assert_not_called()
            engine.transcribe(audio(3), "zh", preview=True)
            engine.transcribe("complete.wav", "zh")
        factory.assert_called_once_with("small", device="cpu", compute_type="int8")
        self.assertEqual(model.transcribe.call_args_list[0].kwargs["beam_size"], 1)
        self.assertEqual(model.transcribe.call_args_list[1].kwargs, {"language": "zh"})
        self.assertEqual(model.transcribe.call_args_list[1].args, ("complete.wav",))

    def test_generator_is_serialized_final_precedes_waiting_preview(self):
        engine = LocalWhisper("small")
        entered, release = threading.Event(), threading.Event()
        order, errors = [], []

        def infer(value, **kwargs):
            def segments():
                order.append(value)
                if value == "first-preview":
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("test release timed out")
                yield SimpleNamespace(text=value)
            return segments(), None

        engine._model = SimpleNamespace(transcribe=infer)

        def run(value, preview):
            try:
                engine.transcribe(value, "zh", preview=preview)
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=run, args=("first-preview", True))
        final = threading.Thread(target=run, args=("final", False))
        latest = threading.Thread(target=run, args=("latest-preview", True))
        first.start()
        self.assertTrue(entered.wait(2))
        try:
            final.start()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with engine._condition:
                    if engine._final_waiters:
                        break
                time.sleep(0.005)
            else:
                self.fail("final never joined the inference waiters")
            latest.start()
            self.assertEqual(order, ["first-preview"])
        finally:
            release.set()
            for thread in (first, final, latest):
                if thread.ident is not None:
                    thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(order, ["first-preview", "final", "latest-preview"])

    def test_failed_preview_releases_model_for_final_retry(self):
        engine = LocalWhisper("small")
        model = Mock()
        model.transcribe.side_effect = [RuntimeError("preview failure"), (iter([SimpleNamespace(text="final")]), None)]
        engine._model = model
        with self.assertRaisesRegex(RuntimeError, "preview failure"):
            engine.transcribe(audio(3), "zh", preview=True)
        self.assertFalse(engine._busy)
        self.assertEqual(engine.transcribe("whole.wav", "zh")[0].text, "final")

    def test_waiting_preview_can_cancel_without_loading_model(self):
        engine = LocalWhisper("small")
        engine._busy = True
        cancelled = threading.Event()
        result = []
        thread = threading.Thread(target=lambda: result.append(engine.transcribe(audio(3), "zh", preview=True, cancelled=cancelled.is_set)))
        thread.start()
        cancelled.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [None])
        self.assertIsNone(engine._model)


class PreviewUiTest(unittest.TestCase):
    def desktop(self):
        desktop = SimpleNamespace(
            preview_worker=None, _recording_generation=2, transcript=Mock(), polished=Mock(),
            _show_bubble=Mock(), error=Mock(), recorder=Mock(), _paste_text=Mock(), _set_stage=Mock(), polish="auto",
        )
        desktop._job_is_current = lambda worker: VoiceDesktop._job_is_current(desktop, worker)
        return desktop

    def test_live_updates_replace_text_and_never_deliver_or_change_stage(self):
        desktop = self.desktop()
        worker = SimpleNamespace(recording_generation=2)
        desktop.preview_worker = worker
        VoiceDesktop._on_local_preview(desktop, "错误", worker)
        VoiceDesktop._on_local_preview(desktop, "完整语境修正", worker)
        self.assertEqual([c.args[0] for c in desktop.transcript.setPlainText.call_args_list], ["错误", "完整语境修正"])
        desktop._paste_text.assert_not_called()
        desktop._set_stage.assert_not_called()
        desktop.polished.setPlainText.assert_not_called()

    def test_stop_invalidates_already_queued_preview_even_in_same_generation(self):
        desktop = self.desktop()
        worker = SimpleNamespace(recording_generation=2, stop=Mock())
        desktop.preview_worker = worker
        VoiceDesktop._stop_local_preview(desktop)
        worker.stop.assert_called_once()
        self.assertIsNone(desktop.preview_worker)
        VoiceDesktop._on_raw_transcribed(desktop, "final whole recording", worker)
        VoiceDesktop._on_local_preview(desktop, "queued old draft", worker)
        VoiceDesktop._on_local_preview_failed(desktop, "queued old error", worker)
        desktop.transcript.setPlainText.assert_called_once_with("final whole recording")
        self.assertEqual(desktop.error.setText.call_count, 1)  # raw is visible while polish runs
        desktop._show_bubble.assert_called_once_with("final whole recording")

    def test_old_generation_and_close_reject_updates(self):
        desktop = self.desktop()
        worker = SimpleNamespace(recording_generation=1)
        desktop.preview_worker = worker
        VoiceDesktop._on_local_preview(desktop, "old", worker)
        desktop._closing = True
        worker.recording_generation = 2
        VoiceDesktop._on_local_preview(desktop, "closed", worker)
        desktop.transcript.setPlainText.assert_not_called()

    def test_preview_failure_only_shows_notice_and_capture_can_continue(self):
        desktop = self.desktop()
        worker = SimpleNamespace(recording_generation=2)
        desktop.preview_worker = worker
        VoiceDesktop._on_local_preview_failed(desktop, "VAD failed", worker)
        self.assertIn("VAD failed", desktop.error.setText.call_args.args[0])
        desktop.recorder.stop.assert_not_called()
        desktop._paste_text.assert_not_called()
        desktop._set_stage.assert_not_called()

    def test_close_cancels_preview_and_retains_thread_until_native_finish(self):
        desktop = self.desktop()
        worker = LocalPreviewWorker(AudioBuffer(), Mock(), "zh", [], None)
        desktop.preview_worker = worker
        desktop._active_workers = {worker}
        desktop.setEnabled = Mock()
        desktop.close = Mock()
        desktop.recorder.is_recording.return_value = False
        event = Mock()
        with patch.object(worker, "isRunning", return_value=True), \
                patch("bubble_buddy.qt_overlay.QTimer.singleShot"):
            VoiceDesktop.closeEvent(desktop, event)
        self.assertTrue(worker.cancelled())
        self.assertIsNone(desktop.preview_worker)
        self.assertIn(worker, desktop._closing_workers)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        with patch.object(worker, "isRunning", return_value=False):
            VoiceDesktop.closeEvent(desktop, event)
        event.accept.assert_called_once()

    def test_disabled_and_nonlocal_backends_do_not_start_preview(self):
        desktop = self.desktop()
        for backend, enabled in (("mlx", True), ("azure", True), ("codex", True), ("faster-whisper", False)):
            desktop.backend = backend
            with patch.object(config, "load_config", return_value={"local_preview": enabled}), \
                    patch("bubble_buddy.qt_overlay.LocalPreviewWorker") as factory:
                VoiceDesktop._start_local_preview(desktop)
            factory.assert_not_called()

    def test_stop_still_starts_full_recording_worker_using_cached_model(self):
        desktop = self.desktop()
        preview = SimpleNamespace(recording_generation=2, stop=Mock())
        desktop.preview_worker = preview
        desktop._stop_local_preview = lambda: VoiceDesktop._stop_local_preview(desktop)
        desktop._max_record_timer = Mock()
        desktop._warmup_copilot = Mock()
        desktop._context_bubble = Mock()
        desktop.stream_worker = None
        desktop._recording_target = None
        desktop.recorder.stop.return_value = Path("entire-recording.wav")
        desktop._get_local_recognizer = Mock(return_value=object())
        desktop._live_context_text = Mock(return_value="")
        desktop._register_worker = Mock()
        desktop._on_raw_transcribed = Mock()
        desktop._on_transcribed = Mock()
        desktop._on_failed = Mock()
        for key, value in dict(model_name="small", backend="faster-whisper", mlx_model="unused", language="zh",
                               hf_endpoint="unused", replacement_pairs=[], replacements_file=None,
                               polish="auto", context_file=None, session_context=False, language_preference="zh-en",
                               polish_engine="copilot", ollama_model="unused").items():
            setattr(desktop, key, value)
        with patch("bubble_buddy.qt_overlay.TranscribeWorker") as factory:
            VoiceDesktop.stop_recording(desktop)
        self.assertIsNone(desktop.preview_worker)
        self.assertEqual(factory.call_args.args[0], Path("entire-recording.wav"))
        self.assertIs(factory.return_value.local_recognizer, desktop._get_local_recognizer.return_value)
        factory.return_value.start.assert_called_once()
        desktop._paste_text.assert_not_called()

    def test_final_worker_polishes_complete_asr_once_never_preview(self):
        worker = TranscribeWorker(Path("entire.wav"), "small", "faster-whisper", "unused", "zh", "unused",
                                  [], None, "auto", None, False, "zh-en", "copilot", "unused")
        recognizer = Mock()
        recognizer.transcribe.return_value = [SimpleNamespace(text="完整的最终识别")]
        worker.local_recognizer = recognizer
        raw, done = [], []
        worker.raw_text_ready.connect(raw.append)
        worker.finished_text.connect(lambda *args: done.append(args))
        with patch("bubble_buddy.qt_overlay.polish_text", return_value="最终润色") as polish:
            worker.run()
        self.assertEqual(raw, ["完整的最终识别"])
        self.assertEqual(done, [("完整的最终识别", "最终润色")])
        recognizer.transcribe.assert_called_once_with("entire.wav", None)
        polish.assert_called_once()
        self.assertEqual(polish.call_args.args[0], "完整的最终识别")

    def test_recorder_new_take_does_not_change_previous_worker_source(self):
        recorder = AudioRecorder()
        fake_stream = Mock()
        with patch("bubble_buddy.qt_overlay.sd.InputStream", return_value=fake_stream), \
                patch("bubble_buddy.cli.resolve_input_device", return_value=(0, "fake")), \
                patch("bubble_buddy.qt_overlay.sf.write"):
            recorder.start()
            first_buffer = recorder.buffer
            recorder._on_audio(audio(1), 16000, None, None)
            first_path = recorder.stop()
            recorder.start()
            recorder._on_audio(audio(2), 32000, None, None)
            second_path = recorder.stop()
        self.assertEqual(first_buffer.snapshot().size, samples(1))
        self.assertEqual(recorder.buffer.snapshot().size, samples(2))
        self.assertNotEqual(first_path, second_path)

    def test_toggle_is_local_only_and_grouped_config_respects_flat_override(self):
        self.assertTrue(config.DEFAULTS["local_preview"])
        for backend in ("azure", "codex", "mlx"):
            self.assertFalse(_field_applies("local_preview", backend, "copilot"))
        self.assertTrue(_field_applies("local_preview", "faster-whisper", "copilot"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            try:
                with patch.dict(os.environ, {"BUBBLE_BUDDY_CONFIG": str(path)}):
                    path.write_text(json.dumps({"speech": {"local_preview": False}}))
                    self.assertFalse(config.load_config(reload=True)["local_preview"])
                    path.write_text(json.dumps({"speech": {"local_preview": False}, "local_preview": True}))
                    self.assertTrue(config.load_config(reload=True)["local_preview"])
            finally:
                config.load_config(reload=True)


if __name__ == "__main__":
    unittest.main()
