# reelforge

You give it a script. You get back a ready-to-post 9:16 Reel/Short in a moody,
cinematic voiceover style (the @jackkgriffith look): a serious AI voiceover from
Higgsfield, dark atmospheric stock B-roll cut on the voice, and bold Montserrat
captions that pop in word by word, with the key emotional words in yellow or red.

```
script.txt
  │
  ├─► script.py      words, sentences, emphasis words, stock search terms
  ├─► Higgsfield     voiceover (through the Claude connector) ─► voice.wav, loudness-normalised
  ├─► alignment.py   Whisper word times, aligned back onto the script's exact words
  ├─► captions.py    caption chunks, timing, pop-in animation, captions.srt
  ├─► assemble.py    shot plan: cuts on sentence starts
  ├─► footage.py     Pexels/Pixabay search ─► download ─► 1080x1920 graded clips
  └─► assemble.py    MoviePy timeline, crossfades, caption overlay, voice + music ─► <slug>.mp4
```

## Setup

```bash
cd reelforge
pip install -r requirements.txt        # moviepy 2, numpy, Pillow, requests, faster-whisper
cp .env.example .env                   # then fill in your keys (see below)
```

FFmpeg must be on your `PATH`. The Montserrat Black font downloads itself on
the first run; pass `--font` to use another font, such as The Bold Font.

### API keys and environment variables

| Variable | Needed for | Where to get it |
|---|---|---|
| `PEXELS_API_KEY` | stock footage (set this one, Pixabay, or both) | <https://www.pexels.com/api/>, free, 200 requests/hour |
| `PIXABAY_API_KEY` | stock footage | <https://pixabay.com/api/docs/>, free; the key is shown when you're logged in |
| `HIGGSFIELD_VOICE_ID` | optional; the narrator voice | `list_voices` in the Higgsfield connector (each voice has a `preview_url`) |
| `HF_KEY`, `HIGGSFIELD_TTS_ENDPOINT` | optional headless route only (`--tts higgsfield-api`) | <https://console.higgsfield.ai> |
| `WHISPER_MODEL` / `WHISPER_DEVICE` | optional; default `base.en` / `cpu` | `small.en` is more accurate; `cuda` if you have a GPU |
| `REELFORGE_CACHE` | optional; default `~/.cache/reelforge` | downloads, graded clips, search cache |

The Higgsfield connector needs no key. It uses the Higgsfield account you
connected to Claude.

## Usage

### With Claude and the Higgsfield connector (recommended)

In Claude Code, open this repo and say *"make a reel from this script: …"*.
The project skill at `.claude/skills/jackk-reel/SKILL.md` runs the whole flow:

1. `python -m reelforge plan scripts/<slug>.txt` previews the caption chunks
   and the emphasis words, and writes `output/<slug>/higgsfield_request.json`.
2. Claude calls the connector's `generate_audio` with exactly those params
   (default: `text2speech_v2` with the ElevenLabs engine, about 1 credit for
   50 words). It then waits with `jobs_wait` and receives an `.mp3` `result_url`.
3. `python -m reelforge render scripts/<slug>.txt --voiceover "<result_url>"`
   builds the video.

Why the hand-off: a Python process can't call Claude's MCP connectors, and the
connector's sign-in never leaves Claude. Higgsfield's public REST API documents
only image and video models (as of Oct 2026), so the connector is the supported
TTS route. When Higgsfield publishes a speech endpoint for your API key, the
experimental `--tts higgsfield-api` route covers headless runs. It submits,
polls `status_url` with backoff, and reads `audio.url`.

### By hand

```bash
python -m reelforge plan   scripts/last-conversation.txt
python -m reelforge render scripts/last-conversation.txt --voiceover voice.mp3
python -m reelforge render scripts/last-conversation.txt --voiceover voice.mp3 \
    --footage-dir ~/broll --music ~/ambient.mp3 --words-per-caption 1
python -m reelforge render scripts/last-conversation.txt --tts piper \
    --piper-model en_US-ryan-high.onnx     # free offline draft voice for iterating on visuals
```

Each render writes `output/<slug>/` containing `<slug>.mp4` (H.264 High,
yuv420p, 30 fps, AAC, faststart), `captions.srt`, `timings.json` and
`credits.txt` (stock attribution).

Useful flags: `--queries "rain window night;empty road night"` overrides
the stock searches. `--reveal phrase` pops the whole caption at once.
`--no-uppercase`, `--y 0.45` and `--font-size 84` adjust the text.
`--portrait-only` rejects landscape stock clips. `--no-auto-emphasis` colours
only the words you mark.

**Script markup (optional):** `*word*` or `*several words*` is coloured
yellow, and `**word**` red. The asterisks are removed before the text goes to
the voice model.

## How it works, step by step

### 1. Script analysis (`script.py`, `lexicon.py`)
* The script is split into words. Each word keeps its punctuation and knows
  whether it ends a sentence (`.!?`) or a clause (`, ; : —`).
* **Emphasis:** phrases from the lexicon are matched first, such as
  "thank you", "i'm \*proud\* of you" and "last time". Single words follow:
  finality words like *last, never, nobody, gone* go **red**, and emotional
  anchors like *today, sorry, proud, stop* go **yellow**. Each match becomes a
  *group* with a priority: phrases score 10 + 2·level + length, words
  2·level, plus 1 when the word ends a sentence.
* **Stock searches:** script words trigger visual concepts. *last* gives
  "clock ticking dark", *sorry* gives "rain on window night", and so on.
  The concepts are used in story order, alternating with an atmosphere palette
  (night drives, rain, fog, dark ocean, city lights).

### 2. Voiceover
The script text is sent to Higgsfield, and the returned audio goes through
`highpass 70 Hz → compressor 3:1 → loudnorm −16 LUFS`, giving a 48 kHz WAV.

### 3. Word timing: the sync maths (`alignment.py`)
Whisper gives timestamps but mishears stylised lines. On the test read it
heard "The I'm sorry" as "Dan, sorry". So the captions always show the
**script's** words and borrow only Whisper's **clock**:

1. Normalise both word lists (lower case, no punctuation or apostrophes) and
   align them with `difflib.SequenceMatcher`, which finds the longest common
   subsequences.
2. For **equal** runs, copy the times across.
3. For **replace** runs of the same length ("I'm" heard as "aim"), copy one to one.
4. For **replace** runs of different lengths ("The I'm" heard as "Dan,"),
   spread the heard span [a, b] over the script words by weight
   wᵢ = len(wordᵢ) + 2:

   `startᵢ = a + (b − a) · Σⱼ<ᵢ wⱼ / Σ w`
5. For **deleted** words (never heard), estimate their duration as
   `ρ · Σ w`, where ρ is the median seconds-per-weight of the matched words.
   If the run starts a sentence, place it just before the next heard word,
   because the gap is the pause. Otherwise place it just after the previous
   heard word.
   Whisper words with zero length are treated the same way. Whisper tends to
   return them right after a pause.
6. **Monotonic pass:** each start is at least the previous word's start, and
   each word lasts at least 80 ms.

With the real ElevenLabs voiceover, 100% of the script's words matched exactly.

**Without Whisper (fallback):** energy-based voice detection finds speech
regions. A frame of 20 ms counts as speech when its RMS level is above
`max(p95 − 35 dB, p10 + 6 dB)`. A dynamic program then cuts sentences and
regions into matching groups, minimising

`Σ ((V − E)/σ)² + 2·(sentence breaks with no pause) − Σ gap/longest_gap`

where V is the voiced time of a region group, `E = T·W_g/W` is the expected
time at the average speaking rate, and `σ = 0.3 s + 0.25·E`. Words are then
laid out by weight along the voiced timeline, with silences skipped. Against
Whisper on the test reads this lands about 150–260 ms off on average.

### 4. Captions (`captions.py`)
**Chunking rules:**
* At most 3 words or 18 characters per caption.
* A caption always breaks at `. ! ? , ;` and at any pause longer than 0.35 s.
* An emphasis phrase is never split, and may stretch a caption to 5 words.
* A caption doesn't end on *a / the / of …*.
* A sentence's last word is never left alone: it is absorbed if it fits,
  otherwise the word before it moves down with it.
* At most one emphasis group per caption: the highest priority wins, and
  marked words always stay.

For the example script this gives "YOUR **LAST CONVERSATION**",
"SO **STOP** SAVING IT.", "THE I'M **PROUD** OF YOU." and
"**NOBODY** GETS / A **WARNING** BEFORE / THE **LAST TIME.**"

**Timing** (λ = 50 ms lead, h = 0.45 s hold):
* Caption start: `Sₖ = max(0, t_first − λ)`.
* Caption end: `Eₖ = min(t_last_end + h, Sₖ₊₁)`. If the gap left before the
  next caption is under 0.2 s, `Eₖ = Sₖ₊₁` so captions don't blink.
* Each word appears at `aᵢ = max(Sₖ, tᵢ − λ)`, which builds the caption up as
  the words are spoken.
* Text 50 ms early reads as in sync. Text that arrives late reads as lag.

**Pop-in animation** at time t, with p = (t − aᵢ) / 0.18 s:
* `scale = 0.55 + 0.45 · easeOutBack(p)`, where
  `easeOutBack(p) = 1 + 2.70158·(p − 1)³ + 1.70158·(p − 1)²`
* `opacity = min(1, p / 0.35)`

This overshoots to about 1.045× at p ≈ 0.6 and settles at exactly 1.0. Each
word is pre-rendered once as a sprite: Montserrat Black, a 7 px black stroke
and a blurred drop shadow, in premultiplied RGBA. The sprite is scaled about
its own centre, so the layout never shifts while words pop. Frame n is
evaluated at t = n / 30, so timing is quantised to 33 ms or less.

### 5. Shots and footage (`assemble.plan_shots`, `footage.py`)
* **Cuts:** every sentence start minus 0.12 s is a candidate cut (cut just
  before the line). A cut is skipped if it is within 2.0 s of the previous cut
  or of the end. Shots longer than 4.5 s are split into ⌈L / 4.5⌉ equal parts.
* **Clip choice:** for each query, candidates from both providers are scored

  `3·portrait + 1·(short side ≥ 1080) ± 1·(long enough) + 2.5·(1 − |luma − 0.22| / 0.5)`

  where luma is the mean brightness of the clip's thumbnail. The score
  favours dim but not black, i.e. moody footage. A clip is never used twice.
  Searches are cached for 24 h, as Pixabay's terms require.
* **Normalising** (FFmpeg, much faster than per-frame Python):
  `scale … force_original_aspect_ratio=increase` → centre `crop=1080:1920` →
  `fps=30` → `eq` (contrast 1.12, saturation 0.62, brightness −0.05) →
  vignette → split-tone `colorbalance` (cool shadows, warm highlights) →
  temporal film grain.

### 6. Assembly (`assemble.py`, MoviePy 2)
Segment *i* starts at shot *i* and runs `x = 0.35 s` into the next shot. In
that overlap the frame is `(1 − α)·previous + α·current`, with
`α = (t − sᵢ)/x`. Outside the overlaps, a frame is read straight from one
clip. That takes about 15 ms per frame, against about 106 ms with MoviePy's
mask-based compose concatenation. A clip shorter than its shot is looped, and
a reused clip starts from a different offset. On top come a 0.25 s fade-in,
a 0.6 s fade-out, the caption overlay, the voice (delayed 0.2 s) and an
optional music bed at 12% volume. Export is H.264 CRF 18 (16 Mb/s cap) with
AAC at 192 kb/s.

## Tuning the look
Every knob is in `reelforge/config.py`: `CaptionStyle` for fonts, colours,
stroke, pop timing and words per caption, and `VideoStyle` for shot lengths,
crossfade, grade and grain. The word lists in `reelforge/lexicon.py` (emphasis
words and visual concepts) are the generator's "taste". Edit them freely.

## Tests
```bash
pip install pytest && python -m pytest -q
```
The 29 tests cover parsing, emphasis, alignment (including misheard words),
the fallback fit, chunking, timing invariants, sprite rendering, the
Pexels/Pixabay response parsing, shot planning, crossfade maths, and a tiny
real end-to-end render.

## Notes
* The Pexels and Pixabay code follows their current API docs (Pexels
  `/v1/videos/search`; Pixabay `/api/videos/`). It was exercised with recorded
  response shapes, not with live keys.
* Pexels and Pixabay licences allow commercial use without attribution, but
  crediting creators is appreciated. `credits.txt` lists every clip used.
