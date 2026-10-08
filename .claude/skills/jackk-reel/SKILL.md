---
name: jackk-reel
description: Turn a short philosophical / emotional script into a finished 9:16 Reel/Short (moody stock B-roll, Higgsfield AI voiceover, animated pop-in captions with highlighted words) using the reelforge app in this repo and the connected Higgsfield integration. Use when the user gives a script and asks for a reel, short, TikTok or video in the @jackkgriffith style.
---

# Make a reel with reelforge + the Higgsfield connector

The Python app cannot call Claude's connectors itself, so you run the voiceover
step through the Higgsfield connector and hand the result to the app.

1. **Setup (once per container)**
   `pip install -r reelforge/requirements.txt` (needs `ffmpeg` on PATH).
   Stock search needs `PEXELS_API_KEY` and/or `PIXABAY_API_KEY` in the
   environment or `reelforge/.env`. If neither is set and the user supplied
   no clips, ask for a key or a folder of clips before rendering.

2. **Save the script** to `reelforge/scripts/<slug>.txt`, keeping the user's line
   breaks and punctuation. The user may mark words with `*word*` (yellow) or
   `**word**` (red). Leave their markup alone and do not add any.

3. **Plan**: run `cd reelforge && python -m reelforge plan scripts/<slug>.txt`.
   Show the user the caption preview: `[word]` is yellow, `{word}` is red.
   The plan also writes `output/<slug>/higgsfield_request.json`.

4. **Voiceover via the connector**:
   - Read `params` from `higgsfield_request.json`.
   - Call `mcp__higgsfield__generate_audio` with exactly those params. If the
     user asked about cost, call it first with `get_cost: true`. An ElevenLabs
     read of about 50 words costs roughly 1 credit.
   - Call `mcp__higgsfield__jobs_wait` with the returned job id until it
     reports `completed`, then take `result_url`, an `.mp3` on the Higgsfield CDN.
   - Do not resubmit after a timeout. Reuse the job id instead.
   - To change the voice, use `mcp__higgsfield__list_voices` (each voice has a
     `preview_url`), then re-run the plan with `--voice-id <id>`.

5. **Render**: run
   `python -m reelforge render scripts/<slug>.txt --voiceover "<result_url>"`.
   Add `--footage-dir <dir>` to use the user's own clips, `--music <file>`
   for a music bed, or `--queries "a;b;c"` to override the stock searches.

6. **Deliver** `reelforge/output/<slug>/<slug>.mp4` with `captions.srt` and,
   for stock footage, `credits.txt`. Before calling the video done, check a
   few frames: `ffmpeg -ss <t> -i <mp4> -frames:v 1 frame.png`.
