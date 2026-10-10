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

    reelforge panel request: <kind> task "<task id>" for reel "<reel id>". Panel: <artifact url> ...
    reelforge panel request: render tasks "<id>", "<id>" ...      (a batch from "Render several")

It arrives either as a direct message or through the Routine "reelforge panel
button" (its id is `settings/claude.trigger_id`; the panel calls `fire_trigger`
with the message as text). That message is the user's own button press: carry
the task out. (If the user writes "render my reel", "review my clips", "get my
script ideas" or "check my video" in chat instead, `ArtifactData query tasks`
where `status == "queued"` and do those.) Batches: do the renders one after
another, each with its own work folder. W = a work folder in the scratchpad,
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
   - Clips chosen on the Footage tab's review are in the project
     (`footage.picks`, `footage.banned`): the render uses them by itself.
6. **review** (the Footage tab's "Find clips": stock clips to choose from):
   `python -m reelforge.panelrun review --work W` prints `sheet` (one JPEG of
   every thumbnail) and `json`. Upload the sheet (`Artifact` publish, `url` =
   panel, `asset: true`, `file_path` = sheet). Then one `ArtifactData batch`:
   `set reviews/<reel id>` to the JSON's fields plus `{last_task: <task id>,
   created: <now ISO>, sheet: <asset id>}` (from `file_path`, a JSON file you
   write), and the task `{status: "done", message: "Found <n> clips"}`. On
   `{"ok": false}`: task `{status: "error", error}`. No voiceover is needed.
7. **ideas** (the Ideas page; scripts from Google Gemini): save the task with
   `out_dir`, run `python -m reelforge.panelrun ideas <task file>` (it needs
   `GEMINI_API_KEY` in the environment; `GEMINI_MODEL` picks a model). On
   `{"ok": true, "result": {model, ideas}}`: `ArtifactData batch` with
   `set ideas/<task id>` = `{created: <now ISO>, topic, model, ideas}` and the
   task `{status: "done", message: "Gemini wrote <n> scripts", result}`. On
   `{"ok": false}`: task `{status: "error", error}` (the error says how to add
   the key). Never write ideas yourself in place of Gemini's.
8. **virality** (the Publish page's "Will it go viral?"; `post_id`, `video`):
   get the video like a publish does, upload it to Higgsfield
   (`media_upload`, PUT, `media_confirm`), `mcp__higgsfield__virality_predictor`
   `{action: "create", params: {model: "virality_predictor", medias: [{role:
   "video", id: <media_id>}]}}`, `jobs_wait` for it, and read the analysis.
   Finish the task with `result: {score: 0-100 or null, verdict: one sentence,
   strengths: [..], risks: [..], tips: [..], job_id, media_id}` in plain words
   (keep `media_id`: a publish of the same video can reuse it).
9. Tell the user in chat in one or two lines what was made (or what failed).

### Publish requests (`kind: "publish"`)

The panel's Publish page stores per-video settings in `posts/<post_id>`
(`common` caption/hashtags, then `tiktok`, `instagram`, `youtube`, each with
`enabled`, `accounts`, caption/hashtags overrides (null = use `common`),
privacy and switches, `sound` and `sound_volume` / `original_volume` in %;
Instagram and YouTube also `when` = "now" | "best" | "at", with `at` a local
time in time zone `tz`; YouTube `title`) and accounts in `accounts/<id>`
(`platform`, `handle`, TikTok `connector_id`). The task names `post_id`,
`platforms` and `video` (`{kind: "reel" | "video", id}`).

1. Read the task, the post and the accounts. Get the video file: a reel's
   `projects/<id>.outputs.video` asset, or `videos/<id>.store` (raw or base64
   parts, decode like `panelrun stage` does). `Artifact read` it into a folder.
2. **TikTok** (Higgsfield connector): upload the MP4 to Higgsfield
   (`media_upload` / `media_confirm`; TikTok needs a Higgsfield-hosted URL),
   then per account `tiktok_prepare_publish` with `connector_id`, `mode`,
   `media_type: "VIDEO"`, `video_url`, `title` (first 150 characters of the
   caption), `description` (caption + hashtags), `privacy_level`,
   `allow_comment/duet/stitch`, `is_aigc`. That opens TikTok's publish form in
   the chat: tell the user to pick the saved sound (title, artist) at the saved
   volumes there (music only works for "Post now"). Write
   `result.tiktok = {status: "waiting", message: "Confirm the post in your Claude chat",
   connector_id, publish_id}` (the publish id or session id the prepare call or
   the form returns, when there is one): the panel then reads
   `tiktok_publish_status` itself and marks it `done`; the user can also press
   "I posted it". TikTok's cover is chosen in its own form.
3. **Instagram / YouTube** go through the Metricool connector. Read its tool
   schemas first, never guess: `getBrandSettings` shows which networks the
   user linked, `createScheduledPost` posts. The video must be a public URL:
   reuse the Higgsfield-hosted URL from step 2 (or `media_upload` it there).
   Time: `when` "now" = a few minutes from now; "best" =
   `getBestTimeToPostByNetwork`; "at" = `at` in time zone `tz`.
   Instagram: a Reel (`share_to_feed`), caption + hashtags. YouTube: a Short
   with `title`, description (caption + tags), `privacy`, `made_for_kids`.
   Cover: when the post has `cover: {ms}`, pass `videoCoverMilliseconds: ms`.
   Instagram sound: when `instagram.sound.name` is set, add
   `instagramData.audioConfiguration: {audioId: <the name>, audioVolume:
   sound_volume, videoVolume: original_volume}`. If Metricool answers with
   several candidates, retry once with the numeric id of the one whose title
   and artist match; if it refuses because the account isn't a Business
   account linked to a Facebook Page, post without it and say so in the
   result message (the user adds the sound in the app).
   Result per platform: `{status: "scheduled", message: "Posting at <time>"}`.
   Without Metricool in this session: `{status: "error", message: "Add the
   Metricool connector in Claude, then say “take over my reelforge panel” in
   a new session"}`. In-app sounds can't be added through any API: tell the
   user to add the saved sound in the app at the saved volumes.
4. Finish the task: `{status: "done", message, result: {<platform>: {status, message, url?}}}`.

The panel's Calendar and Stats pages and the best-time picker call Metricool
themselves (the viewer's own connector), its caption writer asks Claude
through the Artifact's `sample` capability, and TikTok's status is read with
the viewer's Higgsfield connector: none of them sends you a task.

### Profile requests (`kind: "profile"`)

These go only to a session on the user's computer (see below): no
TikTok, Instagram or YouTube API changes profiles, so it's done in the
user's own browser, where they're signed in (Claude in Chrome). The task
has `accounts` (`[{id, platform, handle}]`) and `changes` (any of `name`,
`bio`, `link`, `picture` = a panel asset id; missing = keep).

1. Set the task `running`. If `picture` is set, `Artifact read` it (`path` =
   the asset id) to a local file for the upload dialogs.
2. Per account, open its profile editor and change only the given fields:
   TikTok: tiktok.com/@<handle> → Edit profile (bio up to 80 characters);
   Instagram: instagram.com/accounts/edit/ (bio up to 150; the name is in
   Accounts Center); YouTube: studio.youtube.com → Customization (picture,
   name, description, links). Save each page and check that it took.
3. If a site asks to sign in, for a code, or for any confirmation, stop that
   account and say so in its result; never type passwords for the user.
4. Write `result.<account id> = {status: "done" | "error", account: "@handle",
   message}` as you go, then `{status: "done", message}`.

### Who handles panel requests (`settings/claude`)

The panel messages the session in its database doc `settings/claude`:
`session_id` for plans, renders and publishing (the session the panel was
built with when the doc is missing), `computer_session_id` for profile
changes. `connectors` lists what the handling session can reach; the
Accounts page shows Instagram and YouTube as ready when it includes
Metricool. Connectors only load in a new session, hence:

- **"Take over my reelforge panel"** (a new session, e.g. after adding
  Metricool): find the panel (`Artifact list`, the artifact titled
  "reelforge", or ask for its link) and read it once. Get this session's id
  (`get_session` from claude-code-remote with no id; without that tool, ask
  for this session's link and take the `session_…` part). `ArtifactData get
  settings/claude`, then `update` it (pinned; `set` if missing) with
  `{session_id, connectors: [the connector names you have], updated}`,
  keeping `computer_session_id`. Get the code (repo and branch are in the
  panel's messages), set up as in step 1 above and do any `queued` tasks.
- **"Connect my reelforge panel for profile changes"** (Claude on the user's
  computer: the desktop app, or `claude remote-control`, with Claude in
  Chrome): the same, but write only `{computer_session_id, updated}`.

If the user asks in chat instead ("publish my video", "apply my reelforge
profile changes"), `ArtifactData query tasks` for `status == "queued"` of
that kind and do those.
