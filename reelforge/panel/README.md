# reelforge control panel

The reelforge web UI (`reelforge/ui/static`) packaged as a claude.ai Artifact,
so the whole app can be used from the Claude app on any device:

* `panel-backend.js` replaces the local server: reels are kept in the
  Artifact's database, uploaded files in its asset store (audio and other
  types the store does not take are kept as base64 text).
* `panel-boot.js` connects the Artifact capabilities (`db`, `assets`,
  `downloads`, and `mcp` for the Claude Code Remote connector) and starts the
  normal UI with that backend.
* "Preview plan" and "Render" become tasks that Claude carries out in the
  session the panel messages (`reelforge/panelrun.py`; the procedure is in
  `.claude/skills/jackk-reel/SKILL.md`, "Control panel requests").

Build: `python panel/build.py --out <dir> --session <session id> --artifact <url>`,
then publish `<dir>/index.html` with every other file in `<dir>` and the
capabilities `{db: {}, assets: {}, downloads: true, mcp: {servers: [{server:
"Claude Code Remote", tools: ["send_message"]}]}}`.
