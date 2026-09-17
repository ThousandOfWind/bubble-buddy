"""Pause-triggered local drafts with bounded, overlapping audio context.

Each preview decodes at most 12 seconds, retaining about 6 seconds for revision.
Earlier words are frozen at timestamped word/pause boundaries only for display;
the full recording still receives its independent final ASR pass. If recognition
cannot keep up without skipping speech, preview fails nonfatally rather than
silently dropping text or building a backlog. No UI or cloud calls live here.
"""
from __future__ import annotations

import threading
from bisect import bisect_left, bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from statistics import median
from typing import Callable

import numpy as np

SAMPLE_RATE = 16_000


class AudioBuffer:
    """One take only. Snapshots own their data; callback locks never cover ASR."""

    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._ends: list[int] = []
        self._lock = threading.Lock()

    def append(self, audio: np.ndarray) -> None:
        chunk = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
        with self._lock:
            self._chunks.append(chunk)
            self._ends.append((self._ends[-1] if self._ends else 0) + chunk.size)

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._ends[-1] if self._ends else 0

    def snapshot(self, start: int = 0, end: int | None = None) -> np.ndarray:
        with self._lock:
            total = self._ends[-1] if self._ends else 0
            start = max(0, start)
            end = total if end is None else min(end, total)
            if start >= end:
                return np.empty(0, dtype=np.float32)
            first = bisect_right(self._ends, start)
            last = bisect_left(self._ends, end)
            base = self._ends[first - 1] if first else 0
            chunks = self._chunks[first:last + 1]
        # Copies only intersecting chunks. Full capture is concatenated once at
        # stop; readiness polling uses a fixed-size recent window instead.
        return np.concatenate(chunks)[start - base:end - base]


@dataclass
class PauseSchedule:
    """Use VAD pauses as soft endpoints; retain short phrases for more context.

    Speech ranges are unpadded sample offsets for the full take. Intra-phrase
    gaps adapt the debounce to the speaker; long thinking pauses do not inflate
    it. The continuous-speech deadline only requests a provisional hypothesis.
    """

    last_request_end: int = 0
    last_speech_end: int = 0
    continuous_seconds: float = 3.5

    def ready(self, sample_count: int, speech: list[dict]) -> bool:
        if not speech:
            return False
        speech_end = speech[-1]["end"]
        # Ignore tiny VAD boundary fluctuations and repeated trailing silence.
        if speech_end <= self.last_speech_end + int(0.16 * SAMPLE_RATE):
            return False
        spoken = sum(s["end"] - s["start"] for s in speech) / SAMPLE_RATE
        if spoken < 0.24:
            return False
        silence = (sample_count - speech_end) / SAMPLE_RATE
        gaps = [(b["start"] - a["end"]) / SAMPLE_RATE for a, b in zip(speech, speech[1:])]
        gaps = [gap for gap in gaps if 0.12 <= gap <= 1.0][-8:]
        pause = min(0.95, max(0.45, median(gaps) * 1.8)) if gaps else 0.6
        # A one-word utterance can be shown, but only after a deliberate pause.
        if spoken < 1.2:
            pause = max(pause, 1.2)
        enough_new_audio = sample_count - self.last_request_end >= int(0.5 * SAMPLE_RATE)
        continuous = sample_count - max(self.last_request_end, speech[0]["start"]) >= int(self.continuous_seconds * SAMPLE_RATE)
        if not enough_new_audio or not (silence >= pause or (spoken >= 1.2 and continuous)):
            return False
        self.last_request_end = sample_count
        self.last_speech_end = speech_end
        return True


def speech_timestamps(audio: np.ndarray) -> list[dict]:
    # Bundled with faster-whisper; never import local engines in a lean build
    # unless the user actually starts a local preview.
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        return get_speech_timestamps(audio, VadOptions(
            min_speech_duration_ms=120, min_silence_duration_ms=160, speech_pad_ms=0,
        ))
    except ImportError:
        from .i18n import t
        raise RuntimeError(t("msg.local_engine_missing")) from None


class LocalWhisper:
    """Desktop-owned lazy model, serialized across drafts and final ASR jobs.

    Waiting finals take precedence over previews. Cancellation is checked while
    waiting and between decoded segments; native model loading/inference itself
    cannot be forcibly interrupted. The GUI never waits on this condition.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._condition = threading.Condition()
        self._busy = False
        self._final_waiters = 0

    @contextmanager
    def _access(self, preview: bool, cancelled: Callable[[], bool]):
        acquired = False
        with self._condition:
            if not preview:
                self._final_waiters += 1
            try:
                while self._busy or (preview and self._final_waiters):
                    if cancelled():
                        break
                    self._condition.wait(0.05)
                if not cancelled():
                    self._busy = acquired = True
            finally:
                if not preview:
                    self._final_waiters -= 1
        if not acquired:
            yield None
            return
        try:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    from .i18n import t
                    raise RuntimeError(t("msg.local_engine_missing")) from None
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            yield None if cancelled() else self._model
        finally:
            with self._condition:
                self._busy = False
                self._condition.notify_all()

    def warmup(self, cancelled: Callable[[], bool]) -> None:
        # Run on the preview thread while the user begins speaking, not after
        # the first pause. Warmup does not perform inference on empty audio.
        with self._access(True, cancelled):
            pass

    def transcribe(self, audio, language: str | None, *, preview: bool = False,
                   cancelled: Callable[[], bool] = lambda: False):
        with self._access(preview, cancelled) as model:
            if model is None:
                return None
            options = {"beam_size": 1, "temperature": 0, "condition_on_previous_text": False,
                       "word_timestamps": True} if preview else {}
            segments, _info = model.transcribe(audio, language=language, **options)
            result = []
            # faster-whisper returns a lazy generator: keep serialization until
            # it has been consumed, not just until transcribe() returns.
            for segment in segments:
                if cancelled():
                    return None
                result.append(segment)
            return result


@dataclass(frozen=True)
class DraftText:
    text: str


@dataclass(frozen=True)
class TimedWord:
    start: int
    end: int
    text: str


def _join_draft(prefix: str, tail: str) -> str:
    prefix, tail = prefix.rstrip(), tail.lstrip()
    separator = " " if prefix and tail and prefix[-1].isascii() and tail[0].isascii() else ""
    return prefix + separator + tail


class RollingDraft:
    """Only freeze decoded words; never skip undecoded speech to catch up."""

    window_samples = 12 * SAMPLE_RATE
    revision_samples = 6 * SAMPLE_RATE

    def __init__(self) -> None:
        self.offset = 0
        self.decoded_end = 0
        self.prefix = ""
        self.words: list[TimedWord] = []

    def window_start(self, end: int, speech: list[dict]) -> int:
        if end - self.offset <= self.window_samples:
            return self.offset
        earliest = end - self.window_samples
        latest = end - self.revision_samples
        candidates = [w.end for w in self.words
                      if earliest <= w.end <= latest and w.end <= self.decoded_end - self.revision_samples]
        # A known long silence may be skipped even if no recognition was needed
        # during it. Do not treat unobserved/missing speech as a silence gap.
        for left, right in zip(speech, speech[1:]):
            lo = max(earliest, left["end"] + int(0.15 * SAMPLE_RATE))
            hi = min(latest, right["start"] - int(0.15 * SAMPLE_RATE))
            if lo <= hi and left["end"] <= self.decoded_end:
                candidates.append(hi)
        candidates = [cut for cut in candidates if cut > self.offset and
                      not any(w.start < cut < w.end for w in self.words) and
                      not (cut > self.decoded_end and any(
                          s["start"] < cut and s["end"] > self.decoded_end for s in speech))]
        if not candidates or not self.words:
            raise RuntimeError("Local preview cannot keep up safely; final transcription will use the full recording.")
        # Leave headroom for new audio while keeping an overlapping revisable tail.
        cut = max(candidates)
        before = [w for w in self.words if w.end <= cut]
        if not before:
            raise RuntimeError("Local preview has no safe word boundary; final transcription is unaffected.")
        self.prefix = _join_draft(self.prefix, "".join(w.text for w in before))
        self.words = [w for w in self.words if w.end > cut]
        self.offset = cut
        return cut

    def accept(self, segments: list, end: int) -> list[DraftText]:
        text = " ".join(s.text.strip() for s in segments if s.text.strip())
        if not text:
            return []
        words = []
        for segment in segments:
            for word in getattr(segment, "words", None) or []:
                start = self.offset + round(word.start * SAMPLE_RATE)
                finish = self.offset + round(word.end * SAMPLE_RATE)
                if self.offset <= start <= end and start <= finish <= end + int(0.1 * SAMPLE_RATE):
                    words.append(TimedWord(start, min(finish, end), word.word))
        # A missing timestamp/alignment must never let rollover silently omit
        # words that were shown. Short previews can still work without alignment.
        compact = lambda value: "".join(value.split())
        if compact("".join(w.text for w in words)) != compact(text):
            words = []
        self.words = words
        self.decoded_end = end
        return [DraftText(_join_draft(self.prefix, text))]


class PreviewDecoder:
    """Latest-snapshot consumer; bounded re-decode REPLACES the mutable tail."""

    observation_samples = 20 * SAMPLE_RATE  # VAD context, larger than the 12s ASR window

    def __init__(self, recognizer: LocalWhisper, language: str | None,
                 cancelled: Callable[[], bool], detector=speech_timestamps) -> None:
        self.recognizer = recognizer
        self.language = language
        self.cancelled = cancelled
        self.detector = detector
        self.schedule = PauseSchedule()
        self.draft = RollingDraft()
        self._sample_count = 0
        self.last_window_samples = 0
        self._speech: list[dict] = []

    def update(self, audio: np.ndarray, *, offset: int = 0) -> list | None:
        end = offset + audio.size
        if self.cancelled() or end <= self._sample_count:
            return None
        if offset > self._sample_count:
            # No observation covers the gap. Never pretend potentially missed
            # speech was silence just to move the rolling window forward.
            raise RuntimeError("Local preview cannot keep up safely; final transcription will use the full recording.")
        self._sample_count = end
        # Keep compact speech timestamps, not old audio copies. Replace the
        # overlapping observation region; retain older ranges for known pauses.
        speech = [{"start": s["start"], "end": min(s["end"], offset)}
                  for s in self._speech if s["start"] < offset][-1:]
        # Only the last older range is needed to recognize a long silence gap;
        # do not accumulate an ever-growing history of every pause either.
        recent = [{"start": s["start"] + offset, "end": s["end"] + offset} for s in self.detector(audio)]
        for item in recent:
            if speech and item["start"] <= speech[-1]["end"]:
                speech[-1]["end"] = max(speech[-1]["end"], item["end"])
            else:
                speech.append(item)
        self._speech = speech
        if not self.schedule.ready(end, speech) or self.cancelled():
            return None
        start = self.draft.window_start(end, speech)
        if start < offset:
            raise RuntimeError("Local preview lost its overlap; final transcription is unaffected.")
        window = audio[start - offset:]
        self.last_window_samples = window.size
        segments = self.recognizer.transcribe(window, self.language, preview=True, cancelled=self.cancelled)
        if segments is None or self.cancelled():
            return None
        return self.draft.accept(segments, end)
