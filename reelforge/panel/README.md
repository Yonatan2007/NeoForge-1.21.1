# reelforge control panel

The reelforge web UI (`reelforge/ui/static`) packaged as a claude.ai Artifact,
so the whole app can be used from the Claude app on any device:

* `panel-backend.js` replaces the local server: reels are kept in the
  Artifact's database, uploaded files in its asset store (audio and other
  types the store does not take are kept as base64 text).
* `panel-boot.js` connects the Artifact capabilities (`db`, `assets`,
  `downloads`, and `mcp` for the Claude Code Remote connector) and starts the
  normal UI with that backend.
* `panel-voices.js` is the voice picker (every voice in the user's Higgsfield
  account, read through the Higgsfield connector), playing each voice's sample
  in the page. The samples are kept in the panel's asset store;
  `voice-previews.json` maps voice ids to their asset ids.
* `panel-publish.js` adds the Publish page (your reels and uploaded videos with
  their posting state; one post per video with a phone preview of how it looks
  on TikTok, Reels and Shorts, and a tab per platform: caption, hashtags,
  privacy, when to post, sound and volumes) and the Accounts page (how each
  platform is reached, TikTok accounts connected through Higgsfield,
  Instagram/YouTube accounts, profiles edited one by one or all at once).
  `app.js` shows them through `globalThis.reelforgeExtras`.
* "Preview plan", "Render", "Publish" and "Change on my accounts" become tasks
  that Claude carries out (`reelforge/panelrun.py`; the procedures are in
  `.claude/skills/jackk-reel/SKILL.md`, "Control panel requests"). The panel
  messages the session named in its `settings/claude` doc, so a new session
  (one with the Metricool connector for Instagram and YouTube) can take over,
  and profile changes go to a session on the user's computer, which makes them
  in the user's browser.

Build: `python panel/build.py --out <dir> --session <session id> --artifact <url>`,
then publish `<dir>/index.html` with every other file in `<dir>` and the
capabilities `{db: {}, assets: {}, downloads: true, mcp: {servers: [{server:
"Claude Code Remote", tools: ["send_message"]}, {server: "higgsfield", tools:
["list_voices", "tiktok_accounts", "tiktok_connect", "tiktok_reconnect",
"tiktok_music_trending"]}]}}`.
