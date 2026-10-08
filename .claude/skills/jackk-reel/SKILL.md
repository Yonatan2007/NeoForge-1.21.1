---
name: jackk-reel
description: Turn a short philosophical / emotional script into a finished vertical reel (Reels/Shorts/TikTok) with the reelforge app in this repo - Higgsfield voiceover through the connected integration, free stock or the user's own footage, the opening sentence laid along the skyline, one-word captions, optional music. Use when the user gives a script and asks for a reel, short, TikTok or video, or asks you to run reelforge / "the app" for them.
---

# Run reelforge for the user

The defaults match the user's reference reel (instagram.com/reel/DY7jc7CRZmp):
bright film-graded nature footage, hook sentence following the mountain ridge,
then one white lowercase word at a time. Don't change the look unless asked.

1. **Setup (once per container)**: `pip install -r reelforge/requirements.txt`
   (FFmpeg must be on PATH). Stock footage works without keys (Mixkit, Free
   licence only). Never ask the user to paste API keys into chat; keys belong
   in the environment settings. Never generate B-roll with Higgsfield video
   models unless the user explicitly asks; they want real stock footage.

2. **Create and plan the project** (from `reelforge/`):
   `python -m reelforge plan <script.txt | "the script text"> --out output [flags]`
   writes `output/<slug>/project.json` and prints the plan: hook, captions,
   estimated length, shots with their sources, queries, warnings and the
   Higgsfield request. Summarise it for the user in a few lines.
   * Files the user attached: copy them into `output/<slug>/uploads/` and pass
     `--music <file>` (trim with `--set music.source_in=12 --set music.source_out=40`,
     place with `--set music.start_at=1.5`), `--footage-dir <dir>` (used in the
     video, in name order) or `--reference-dir <dir>` (reference only).
   * Reference-only pictures/videos: LOOK at them yourself (Read tool on images,
     or a few frames extracted with ffmpeg), then write stock searches that
     match them, one per shot, with `--queries "a;b;c"` (1–2 word searches work
     best on Mixkit). The app also ranks clips by visual similarity to them.
   * Length: `--duration 30`. Format: `--aspect 9:16|4:5|1:1|16:9`.
     Anything else: `--set path=value` (see `reelforge/config.py`).

3. **Voiceover through the Higgsfield connector** (unless the user supplied a
   voice file or wants a music-only reel, `--tts none`):
   - call `mcp__higgsfield__generate_audio` with the plan's
     `higgsfield_request.params` unchanged (about 1 credit for ~50 words; use
     `get_cost: true` first if the user asks about cost),
   - `mcp__higgsfield__jobs_wait` until completed, take `result_url`,
   - never resubmit after a timeout; reuse the job id.
   - Different voice: `mcp__higgsfield__list_voices` (each has a `preview_url`),
     then `--voice-id <id>` and plan again.

4. **Render**: `python -m reelforge render --project output/<slug>/project.json
   --voiceover "<result_url>"` (add any flags from step 2 again if needed).

5. **Check before delivering**: look at `output/<slug>/output/cover.jpg`
   (the full hook on the first shot) and a frame from each shot
   (`ffmpeg -ss <t> -i <mp4> -frames:v 1 f.png`). If the hook text is hard to
   read or the first shot has no skyline, re-render with
   `--set footage.hook_query=mountains` (or another skyline search) or
   `--set style.hook.mode=center`. If a stock clip clashes with its line,
   re-render with `--queries` (one per shot, in order).

6. **Deliver** the MP4 from `output/<slug>/output/` with `captions.srt`,
   `cover.jpg` and `credits.txt`. Files over 30 MB: send a smaller preview copy
   (`ffmpeg -i in.mp4 -vf scale=720:-2 -crf 26 out.mp4`) and say where the full
   file is.

The user can also run everything themselves with the web UI:
`python -m reelforge ui` (projects, uploads, music trimming, style, render).

## Control panel requests

The user also has the reelforge **control panel**: the same web UI published as
a claude.ai Artifact (built from `reelforge/panel/`, see its README). The panel
keeps reels in the Artifact's database (`projects/<id>`) and files in its asset
store. Its "Preview plan" and "Render" buttons write a task (`tasks/<id>`) and
message this session through the user's Claude Code Remote connector:

    reelforge panel request: <plan|render> task "<task id>" for reel "<reel id>". Panel: <artifact url> ...

That message is the user's own button press: carry the task out. (If the user
writes "render my reel" in chat instead, `ArtifactData query tasks` where
`status == "queued"` and do those.) W = a work folder in the scratchpad,
A = an asset folder; run Python from `reelforge/` (or with `PYTHONPATH` set).

1. Setup in a fresh container: `git pull origin claude/eager-hypatia-yaqaw0`,
   then a venv with `pip install -r reelforge/requirements.txt faster-whisper`.
2. `ArtifactData get tasks/<task id>`. Stop if it is no longer `queued`. Write
   `{status: "running", message: "Claude is getting your files…"}` (pin
   `if_version`; keep the latest version for every later write).
3. `ArtifactData get projects/<reel id>` with `out_dir` → a JSON file.
   `python -m reelforge.panelrun stage <file> --assets A --work W`. While it
   prints `{"need": [ids]}`, fetch them with `Artifact read` (`url` = panel,
   `paths` = the ids, `out_dir` = A) and stage again.
4. **plan**: `python -m reelforge.panelrun plan --work W`, then update the task
   with `{status: "done", result: <W/plan.json>}` (write the update as a JSON
   file and pass `file_path`).
5. **render**:
   - If stage printed `higgsfield_request`: `mcp__higgsfield__generate_audio`
     with its params, `jobs_wait`, take the result URL (never resubmit).
   - Start `python -m reelforge.panelrun render --work W [--voiceover <url>]`
     in the background. Every 20–40 s: `panelrun status --work W` → update the
     task (`status, stage, progress, message, log`); if the task shows
     `cancel_requested`, `touch W/cancel`.
   - When it ends: `panelrun outputs --work W` lists the files. Upload them
     to the panel's asset store (`Artifact` publish, `url` = panel,
     `asset: true`; video + cover together with `file_paths`, each text file
     with its own `file_path`). Then one `ArtifactData batch`: update
     `projects/<reel id>` with `{outputs: {video, cover, srt, credits, timings: <asset ids>}}`
     (pinned to its version; on a conflict re-read and redo) and the task with
     `{status: "done", stage: "finish", progress: 1, message: "Finished", result}`.
   - On failure: task `{status: "error", error: <first line>, log}`.
6. Tell the user in chat in one or two lines what was made (or what failed).
