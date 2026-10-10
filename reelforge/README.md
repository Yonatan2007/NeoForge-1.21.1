# reelforge

Script in, ready-to-post reel out. reelforge turns a short philosophical
script into a vertical video in the style of
[this reel](https://www.instagram.com/reel/DY7jc7CRZmp/):

* bright, warm, film-graded nature footage (mountains, lakes, meadows, people
  small in the landscape), cut on the voice;
* the **opening sentence written along the skyline** of the first shot —
  each word follows the ridge, rotated to its slope, letters bending around
  the peaks, the last word dropped large below the line;
* after that, **one white lowercase word at a time**, centred;
* a voiceover (Higgsfield through Claude, your own recording, a free offline
  voice, or none at all), optional music you trim and place yourself, and
  your own pictures or videos mixed with free stock footage.

Everything has a default that matches the reference reel, and everything can
be changed — in the web UI, per project, or once as your own saved defaults.

## Quick start

You need Python 3.10+ and FFmpeg (macOS: `brew install ffmpeg`; Windows:
`winget install ffmpeg`; Ubuntu: `sudo apt install ffmpeg`).

```bash
cd reelforge
pip install -r requirements.txt
python -m reelforge ui                 # opens http://127.0.0.1:8765 in your browser
```

1. **New project** → paste or upload your script.
2. **Voice** → upload a voiceover (or pick "Higgsfield via Claude", "Piper" or
   "No voice — music only").
3. **Music** (optional) → upload a song, drag the highlighted part of the
   waveform you want, and drag where it sits in the video.
4. **Footage** (optional) → drop pictures/videos. For each one choose
   **Use in video** or **Reference only** (only guides which stock clips are
   picked and how the colours look).
5. **Length** → automatic, or the exact number of seconds you want.
6. **Render** → watch the progress, then download the MP4, the captions
   (`.srt`) and a cover image.

The **Style** tab holds every look setting (captions, hook, colour grade,
format, pacing). "Save as my defaults" makes them the starting point of every
new project.

## The web UI

| Tab | What you can do |
|---|---|
| Script | type or upload `.txt`; see word count, estimated length, the hook sentence and how the captions will split. Mark words `*like this*` (yellow) or `**like this**` (red) when colour emphasis is on |
| Voice | upload a file, copy the Higgsfield request for Claude, use Piper offline, or make a music-only reel (captions timed to reading pace) |
| Music | waveform with a draggable selection (in/out), placement on the video timeline (start/end), volume, fades, loop, automatic lowering under the voice ("ducking") |
| Footage | drag-and-drop images/videos, role toggle (use in video / reference only), drag to reorder, pin to a shot, trim videos, length and camera motion for photos, stock sources and custom searches |
| Style | presets and every caption / hook / video / colour setting, generated from the settings model so nothing is hidden; save/reset your defaults |
| Length | target duration and how it is reached |
| Render | plan preview (shots, sources, captions, warnings), render with live progress and cancel, video player and downloads |

Projects live in `~/.config/reelforge/projects/` (change with `REELFORGE_HOME`);
each has a `project.json`, `uploads/` and `output/`.

## What the defaults do (the "reference" preset)

| | |
|---|---|
| Format | 9:16, 1080×1920, 30 fps (also 4:5, 1:1 — the reference reel itself is square — and 16:9) |
| Footage | sunny nature/adventure searches; the first shot is chosen for a clear skyline |
| Look | each clip is colour-matched to the reference reel (warm teal film look), slight black lift and grain |
| Hook | opening sentence along the skyline, near-black on bright sky (white on dark), first/stressed/last words big |
| Captions | Inter Bold, lowercase, white, one word at a time, centred, soft shadow |
| Cuts | hard cuts on sentence starts, shots 3–7 s |

Other presets: **moody** (dark rain/night B-roll, bold uppercase Montserrat
captions with yellow/red key words, crossfades — the first version of this
app) and **bold** (reference footage with punchy 3-word captions).

## Voiceover options

* **Higgsfield via Claude (recommended).** The Higgsfield account is connected
  to Claude, not to this program, so Claude generates the voice: in Claude
  Code say *"make a reel from this script"* (the project skill in
  `.claude/skills/jackk-reel/` runs the whole flow), or copy the request from
  the Voice tab and ask Claude to run it. The result (an `.mp3` link or file)
  goes into the project's voice file.
* **Your own recording** — any audio or video file.
* **Piper** — free offline voice for drafts (`pip install piper-tts` + a voice
  model from huggingface.co/rhasspy/piper-voices).
* **No voice** — a music-only reel; captions are timed to a reading pace.

## How the length is fitted

Speech is never cut. With a target length:
1. a voice that is too long is sped up (up to 1.12× by default),
2. a short voice gets a longer opening (up to 2 s of picture and music),
3. the rest becomes a longer ending.
If the voice cannot fit even at the fastest tempo, the video runs longer and
the plan/render shows a warning.

## Footage

* **Your media first.** Pictures become smooth slow-zoom/pan clips; videos can
  be trimmed. They fill shots in upload order (or the shot you pin them to).
* **Stock for the rest.** [Mixkit](https://mixkit.co) works with no key and
  only clips under its Free licence are used (commercial and social use, no
  attribution required). Add `PEXELS_API_KEY` / `PIXABAY_API_KEY` for more
  choice. Every clip used is listed in `credits.txt`.
* **Reference uploads** are not shown in the video: they rank stock clips by
  visual similarity, add colour-based search terms, and (with *Look → match
  uploads*) set the colour grade.

## Settings reference (CLI and `project.json`)

Everything the UI shows is a field in `reelforge/config.py`:
`style.caption.*`, `style.hook.*`, `style.video.*`, `style.look.*`,
`voice.*`, `music.*`, `footage.*`, `duration.*`. From the command line:

```bash
python -m reelforge render my-script.txt --voiceover voice.mp3 \
    --music song.mp3 --set music.source_in=12.5 --set music.source_out=40 \
    --footage-dir ~/my-clips --reference-dir ~/looks-i-like \
    --duration 30 --aspect 9:16 --set style.caption.font_size=80
python -m reelforge plan my-script.txt          # preview, no rendering
python -m reelforge render --project ~/.config/reelforge/projects/<id>/project.json
python -m reelforge defaults --set style.caption.case=upper   # change your defaults
python -m reelforge defaults --reset
python -m reelforge presets
```

### API keys and environment variables (all optional)

| Variable | For | Where to get it |
|---|---|---|
| `PEXELS_API_KEY` | adds Pexels to the stock search | <https://www.pexels.com/api/> |
| `PIXABAY_API_KEY` | adds Pixabay to the stock search | <https://pixabay.com/api/docs/> |
| `HIGGSFIELD_VOICE_ID` | default narrator voice | the Higgsfield connector's `list_voices` |
| `HF_KEY`, `HIGGSFIELD_TTS_ENDPOINT` | experimental headless Higgsfield route | <https://console.higgsfield.ai> |
| `WHISPER_MODEL`, `WHISPER_DEVICE` | word timing model (default `base.en`, `cpu`) | |
| `REELFORGE_HOME` | projects and saved defaults (default `~/.config/reelforge`) | |
| `REELFORGE_MAX_UPLOAD_MB` | largest file the UI accepts (default 500) | |
| `REELFORGE_CACHE` | downloads and graded clips (default `~/.cache/reelforge`) | |

## How it works

```
script ─► script.py      words, sentences, emphasis, the hook sentence
voice  ─► media.py       clean-up, loudness, silence trim, tempo (timing.py fits the length)
       ─► alignment.py   Whisper word times aligned onto the script's own words
shots  ─► assemble.py    cuts on sentence starts; the hook shot is never cut
footage─► pipeline.py    your media first, stock for the rest (footage.py; skyline-scored hook clip)
       ─► usermedia.py   photos → smooth Ken Burns clips; reference signatures
       ─► look.py        cover-crop to the output size, colour match (Lab statistics → 3D LUT), grain
text   ─► hook.py        skyline detection and text-on-path layout for the opening sentence
       ─► captions.py    one-word (or phrase) captions with pop-in
audio  ─► music.py       trim, place, loop, fade, duck under the voice, final mix
render ─► assemble.py    MoviePy timeline + overlays → H.264/AAC MP4, cover, SRT, credits
ui     ─► server.py      FastAPI app (schema.py describes every setting) + ui/static
```

**Word timing.** Whisper gives timestamps but mishears stylised lines ("The
I'm sorry" → "Dan, sorry"), so the captions always show the script's words
and borrow only Whisper's clock: both word lists are normalised and aligned
with `difflib`; equal runs copy times, replaced runs share the heard span by
word length, unheard words are placed by the measured speaking rate next to
the nearest pause. Without Whisper, energy-based voice detection and a
dynamic-programming fit of sentences to speech regions estimate the timings.

**Hook layout.** The skyline is found per column where the sky (modelled
from the top rows) meets terrain; the curve is smoothed and offset above the
ridge, then the words are laid along its arc length — each glyph rotated to
the local tangent where the curve bends sharply. The first, stressed and last
words are larger; the last word drops below the line end. With no usable
skyline the text follows a gentle diagonal instead.

**Colour match.** Each clip's mean and spread in CIE Lab are mapped onto the
reference look (L' = (L − μs)·σt/σs + μt per channel, clamped and blended by
strength), baked into a 3D LUT and applied by FFmpeg with the rest of the
grade in one pass.

## Tests

```bash
pip install pytest httpx && python -m pytest -q      # about 270 tests, ~30 s
```
