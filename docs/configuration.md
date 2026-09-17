# Configuration

Bubble Buddy reads defaults for language, model, backend, hotkey, polishing and
Azure settings from a `config.json` file. Command-line flags always override
config values.

## Where the config is loaded from

The CLI looks for `config.json` in this order (first match wins):

1. the path in the `BUBBLE_BUDDY_CONFIG` environment variable
2. `./config.json` in the current directory
3. `config.json` in the project root
4. `~/.bubble-buddy/config.json`

Copy `config.example.json` to `config.json` and edit it. `config.json` is
gitignored so local settings stay out of source control.

## Editing from the desktop overlay

You can edit every setting from the desktop overlay: click **⚙ Settings** to open
a panel, change any value (backend, language preference, polish, model, hotkey,
Azure deployments, etc.), and click **Save**. Changes are written to `config.json`
and applied to the running overlay immediately (the hotkey is re-registered
automatically).

## Recording limit

`max_record_seconds` (default `120`) caps a single continuous recording/streaming
session: if you start recording and never stop, it auto-stops after this many
seconds to avoid accidental long captures. Set it to `0` to disable the limit,
except for the experimental `codex` desktop backend: its effective limit is at
most 119 seconds to stay below the 120-second upload cap.

## Local live drafts (Qt desktop)

With `backend: faster-whisper`, `local_preview` defaults to `true`. Toggle it in
Settings → Transcription, or set `speech.local_preview` in grouped JSON (the
flat `local_preview` key takes precedence). It takes effect on the next recording.
Other backends and the CLI are unchanged.

- Bundled Silero VAD detects speech and pauses locally, not semantic meaning or
  intonation. Ordinary pauses trigger drafts after roughly 0.45–0.95 seconds,
  adjusted to recent short gaps. Very short utterances wait for a longer pause
  (about 1.2 seconds) so isolated words are not decoded too eagerly.
- Continuous speech requests a provisional update after about 3.5 seconds of
  new audio. These are **recognition triggers, not hard audio cuts**.
- Each draft decodes **at most 12 seconds**, with about 6 seconds of overlapping
  context available for revision. Earlier words are temporarily frozen at
  aligned word or known pause boundaries, not arbitrary time cuts. Recent words
  and punctuation can be replaced, rather than appending independent guesses.
  A long, known silence can advance the window without repeating silence.
- There is one local decode at a time, with a reused model and latest-snapshot
  coalescing rather than a queue of outdated jobs. If the CPU falls too far behind
  or word alignment is incomplete, preview pauses instead of skipping undecoded
  speech. The **full recording** remains available for independent final ASR,
  which can correct even the temporarily frozen prefix.
- This is not zero-latency streaming: each bounded decode still takes time, and
  the final full-recording pass still scales with take length. Very long/unlimited
  recordings retain their full audio in memory. Disable preview on slow machines.
- Drafts only update the overlay. Stopping invalidates queued preview results,
  then runs full-recording ASR and the selected polish engine (e.g. Copilot).
  Only the successful final result is automatically copied/pasted/submitted.
  Preview errors are nonfatal: recording and final transcription remain available.

中文：按自然停顿触发预览，而不是固定切断句子。每次最多重算 12 秒，保留约 6 秒
重叠上下文让后文修订前文；更早文字暂时固定，停止后再用完整录音统一校正。
草稿只显示在悬浮窗，完整转写一出来就先显示，不必等云端润色才看到文字；
润色成功后再粘贴一次。停顿检测不等于理解语义或语气，CPU 推理仍会带来延迟。

## See also

- [Azure OpenAI backend](azure.md) — cloud transcription + LLM polishing
- [Codex / ChatGPT dictation](codex.md) — experimental `backend: codex`, browser login and automatic refresh; no Azure required when polish uses `rules`/`ollama`
- [GitHub Copilot polishing](copilot.md) — `polish_engine: copilot`; default GPT-5.6 Luna, `copilot_reasoning_effort: low`, `copilot_max_output_tokens: 2048`; pair with local ASR to avoid Azure
- [Context plugins](context-plugins.md) — extend per-app context extraction
