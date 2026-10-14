# reelforge control panel

The reelforge web UI (`reelforge/ui/static`) packaged as a claude.ai Artifact,
so the whole app can be used from the Claude app on any device:

* `panel-backend.js` replaces the local server: reels are kept in the
  Artifact's database, uploaded files in its asset store (audio and other
  types the store does not take are kept as base64 text).
* `panel-boot.js` connects the Artifact capabilities (`db`, `assets`,
  `downloads`, `mcp` for the viewer's connectors and `sample` for asking
  Claude) and starts the normal UI with that backend.
* `panel-voices.js` is the voice picker (every voice in the user's Higgsfield
  account, read through the Higgsfield connector), playing each voice's sample
  in the page. The samples are kept in the panel's asset store;
  `voice-previews.json` maps voice ids to their asset ids.
* `panel-publish.js` adds the Publish page (your reels and uploaded videos with
  their posting state; one post per video with a phone preview of how it looks
  on TikTok, Reels and Shorts, and a tab per platform: caption, hashtags,
  privacy, when to post with Metricool's best times, sound and volumes; a
  caption writer and checks before it goes out) and the Accounts page (how
  each platform is reached, TikTok accounts connected through Higgsfield,
  Instagram/YouTube accounts, profiles edited one by one or all at once).
  `app.js` shows them through `globalThis.reelforgeExtras`.
* `panel-growth.js` adds Calendar (posts scheduled and published through
  Metricool; a waiting post can be moved or held back as a draft) and Stats
  (views, likes, comments, saves, shares and reach per reel), read with the
  viewer's own Metricool connector, and the caption writer (Claude, through
  `sample`, on the viewer's usage).
* `panel-review.js` adds "Choose the stock clips" to the Footage tab: Claude
  searches the stock sites (a "review" task, `panelrun review`) and writes the
  candidates to `reviews/<reel id>` with one contact sheet of their
  thumbnails; the clips the viewer keeps or rules out are saved as
  `footage.picks` / `footage.banned`, which the render uses first.
* `panel-more.js` adds Ideas (scripts from Google Gemini, an "ideas" task run
  with `GEMINI_API_KEY` in Claude's environment), Render several (one message
  for a batch of renders), the cover picker, "Will it go viral?" (Higgsfield's
  Virality Predictor, a "virality" task) and TikTok's post status.
* `panel-autopilot.js` adds Autopilot: the settings (reels a day, platforms,
  posting hours, the reel to copy, format, music, topics, tone, "ask me
  first"), "Fill the next days" (an "autopilot" task: Claude writes the
  scripts and posts with Gemini, voices them with Higgsfield, renders them,
  looks over their openings and schedules them in Metricool at the
  audience's busiest hours, see `reelforge/autopilot.py`) and the queue,
  `autopilot/<slot id>`, where a waiting reel is approved or held back with
  the viewer's Metricool connector. "Growing the account" holds the growth
  settings: video length, a question as the first comment, posts built
  around real searches, Instagram Trial Reels, collab invites, and a
  reminder to add a trending TikTok sound at volume 0. "What's working"
  shows `insights/latest`, which Claude writes each morning from the
  Metricool numbers (`reelforge/insights.py`); the queue shows each reel's
  first comment, Trial Reel state, collab invite, sound and numbers.
* `panel-replies.js` adds Replies: a comment pasted from a reel gets three
  replies drafted by Claude in the account's voice (`sample`) to copy into
  the app, and a comment worth a whole video becomes a video reply that the
  next autopilot run writes (`replies/<id>`, status "idea").
* `panel-music.js` adds Music, the music library (`music/<id>`): songs added
  once and kept in the panel, each with where it starts and whether it is in
  the shuffle. Autopilot deals them at random to its reels, and every reel's
  Music tab can take one ("Random song", or choose). It also lists TikTok's
  trending sounds this week (the viewer's Higgsfield connector).
* The panel reaches Claude through the Routine in `settings/claude.trigger_id`
  (`fire_trigger`) or a direct `send_message`; when neither is allowed it says
  what to type in the chat.
* "Preview plan", "Render", "Find clips", "Get ideas", "Will it go viral?", "Publish", "Fill the next days" and "Change on my accounts" become tasks
  that Claude carries out (`reelforge/panelrun.py`; the procedures are in
  `.claude/skills/jackk-reel/SKILL.md`, "Control panel requests"). The panel
  messages the session named in its `settings/claude` doc, so a new session
  (one with the Metricool connector for Instagram and YouTube) can take over,
  and profile changes go to a session on the user's computer, which makes them
  in the user's browser.

Build: `python panel/build.py --out <dir> --session <session id> --artifact <url>`,
then publish `<dir>/index.html` with every other file in `<dir>` and the
capabilities `{db: {}, assets: {}, downloads: true, sample: {}, mcp: {servers:
[{server: "Claude Code Remote", tools: ["send_message", "fire_trigger"]}, {server: "higgsfield",
tools: ["list_voices", "tiktok_accounts", "tiktok_connect", "tiktok_reconnect",
"tiktok_music_trending", "tiktok_publish_status", "balance"]}, {server: "Metricool Social Media Management", tools:
["getBrandSettings", "getBestTimeToPostByNetwork", "getScheduledPosts",
"updateScheduledPost", "getAnalyticsDataByMetrics"]}]}}`.
