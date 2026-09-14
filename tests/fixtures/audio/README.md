# Two public speech fixtures

These are **real downloaded speech recordings**, not mock audio or generated
silence. Both WAV files are copied byte-for-byte from the pinned WeNet revision
below. Total audio size: **416,440 bytes (~407 KiB)**. No complete corpus or model
weights are included.

| File | Corpus / language | Duration | Corpus license |
| --- | --- | --- | --- |
| `aishell-BAC009S0724W0121.wav` | AISHELL-1 / Mandarin | 4.281 s | Apache-2.0 |
| `librispeech-1995-1837-0001.wav` | LibriSpeech / English | 8.730 s | CC-BY-4.0 |

Both are mono, 16 kHz, 16-bit PCM WAV. `manifest.json` records exact URLs,
SHA-256 hashes, WAV metadata, expected transcripts and acceptance thresholds.
The checked-in `upstream-transcripts.txt` is unmodified upstream text. The repo's
`.gitattributes` preserves audio/transcript bytes even on Windows CRLF checkouts. The
additional traditional-Chinese reference variant in the manifest is our
script-equivalent rendering, not a separate upstream transcript.

## Provenance and attribution

Download mirror: **WeNet contributors**, [wenet-e2e/wenet](https://github.com/wenet-e2e/wenet),
revision `d17059667d6afe0680d19b3a4948ab825ef25105`:

- [Mandarin WAV](https://raw.githubusercontent.com/wenet-e2e/wenet/d17059667d6afe0680d19b3a4948ab825ef25105/test/resources/aishell-BAC009S0724W0121.wav)
- [English WAV](https://raw.githubusercontent.com/wenet-e2e/wenet/d17059667d6afe0680d19b3a4948ab825ef25105/test/resources/librispeech-1995-1837-0001.wav)
- [Reference transcripts](https://raw.githubusercontent.com/wenet-e2e/wenet/d17059667d6afe0680d19b3a4948ab825ef25105/test/resources/dataset/text)
- [WeNet Apache license](https://github.com/wenet-e2e/wenet/blob/d17059667d6afe0680d19b3a4948ab825ef25105/LICENSE)

### Mandarin: AISHELL-1

Publisher: **Beijing Shell Shell Technology Co., Ltd.**
[OpenSLR SLR33](https://www.openslr.org/33) identifies the corpus license as
**Apache License v2.0**. A copy is included in
[`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt).

Dataset citation: Hui Bu, Jiayu Du, Xingyu Na, Bengu Wu, Hao Zheng,
*AIShell-1: An Open-Source Mandarin Speech Corpus and A Speech Recognition
Baseline*, Oriental COCOSDA 2017.

Reference: `广州市房地产中介协会分析`

### English: LibriSpeech

Corpus creators: **Vassil Panayotov, Guoguo Chen, Daniel Povey and Sanjeev
Khudanpur**, with readings derived from the **LibriVox project**.
[OpenSLR SLR12](https://www.openslr.org/12) identifies the corpus license as
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
Recording ID: `1995-1837-0001` (speaker 1995, chapter 1837).

Dataset citation: *LibriSpeech: An ASR corpus based on public domain audio books*,
ICASSP 2015. The upstream sample is provided as WAV; Bubble Buddy did not trim,
resample or otherwise modify the downloaded recording. The corpus's original
FLAC-to-WAV representation is supplied by WeNet.

A license copy is included in [`licenses/CC-BY-4.0.txt`](licenses/CC-BY-4.0.txt),
obtained from the [SPDX v3.27.0 license text](https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/CC-BY-4.0.txt).
The audio/transcription retain their corpus license, not a new Bubble Buddy
license. They are provided **as-is, without warranties**. No endorsement of this
project by the corpus authors, speakers, WeNet or Creative Commons is implied.

Reference:

> IT WAS THE FIRST GREAT SORROW OF HIS LIFE IT WAS NOT SO MUCH THE LOSS OF THE
> COTTON ITSELF BUT THE FANTASY THE HOPES THE DREAMS BUILT AROUND IT

## Use

See [`docs/audio-e2e.md`](../../../docs/audio-e2e.md) for the opt-in real processing
pipeline. Ordinary unit tests only validate these files and acceptance logic;
they do not download models, record audio, call Copilot or access the clipboard.
