"""Build the reelforge control panel: the web UI from ``reelforge/ui/static``
packaged as a claude.ai Artifact, with a backend that keeps projects in the
Artifact's database and files in its asset store instead of a local server.
Plans and renders are done by Claude (see ``reelforge/panelrun.py``).

    python panel/build.py [--out panel/dist] [--session <id>] [--artifact <url>]

Writes ``index.html`` plus every UI module and the data files the backend
reads (``panel-data/*.json``)."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from reelforge import __version__, config, schema, voiceover  # noqa: E402
from reelforge.config import FootageItem, Project  # noqa: E402

STATIC = ROOT / "reelforge" / "ui" / "static"
PANEL_FILES = ("panel-backend.js", "panel-boot.js", "panel-voices.js", "panel-publish.js", "panel-growth.js",
               "panel-review.js", "panel-more.js", "panel.css")

INDEX = """<title>reelforge</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect x='1' y='1' width='30' height='30' rx='9' fill='%232b3352'/%3E%3Ccircle cx='20' cy='16.5' r='6' fill='%23f7b544'/%3E%3Cpath d='M1 22.5l6.5-7.2 5 5.4 6.6-8.4L31 25v5a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1Z' fill='%23151a28'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500..800&family=Instrument+Sans:wght@400..700&family=JetBrains+Mono:wght@400;600;700&display=swap">
<link rel="stylesheet" href="styles.css">
<link rel="stylesheet" href="panel.css">
<style>
  .panel-boot { min-height: 60vh; display: grid; place-items: center; padding-inline: 16px; text-align: center; }
  .panel-boot p { max-width: 34rem; color: var(--muted); font-size: 15px; }
  .panel-boot strong { color: var(--text); }
</style>
<a class="skip-link" href="#main">Skip to content</a>
<div id="app" class="app">
  <div class="panel-boot" id="panel-boot"><p>Loading reelforge…</p></div>
</div>
<script type="module" src="panel-boot.js"></script>
"""


def meta() -> dict:
    """What the local server's /api/meta answers, for the panel: Claude
    renders, so Whisper and FFmpeg are there; Piper and the Higgsfield REST
    route are not; Higgsfield voices are made by Claude at render time."""
    return {
        "version": __version__,
        "presets": schema.options(schema.PRESET_OPTIONS),
        "aspects": [{"id": a, "label": schema.ASPECT_LABELS.get(a, a), "width": w, "height": h}
                    for a, (w, h) in config.ASPECTS.items()],
        "fonts": [{"id": v, "label": label, "weights": list(_weights())} for v, label in schema.FONTS],
        "font_weights": list(_weights()),
        "voice_sources": schema.options(schema.VOICE_SOURCES),
        "voice_presets": schema.options(schema.VOICE_PRESETS),
        "palettes": schema.options(schema.PALETTES),
        "hook_modes": schema.options(schema.HOOK_MODES),
        "caption_cases": schema.options(schema.CASES),
        "stock_sources": schema.options(schema.STOCK_SOURCES),
        "motions": schema.options(schema.MOTIONS),
        "roles": ["script", "voice", "music", "footage", "reference"],
        "max_upload_mb": 200,
        "capabilities": {"whisper": True, "piper": False, "pexels": False, "pixabay": False,
                         "higgsfield_api": False, "ffmpeg": True,
                         "claude_voice": True, "claude_render": True},
    }


def _weights():
    from reelforge import fonts
    return fonts.WEIGHTS


def build(out: Path, session: str, artifact: str, repo: str = "", branch: str = "") -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out, ignore=shutil.ignore_patterns("index.html", "__pycache__"))
    for name in PANEL_FILES:
        shutil.copy2(HERE / name, out / name)
    (out / "index.html").write_text(INDEX, encoding="utf-8")
    data = out / "panel-data"
    data.mkdir()
    factory = config.to_dict(config.from_dict(Project, config.preset_dict("reference")))
    files = {
        "meta.json": meta(),
        "schema.json": schema.sections(),
        "presets.json": {name: config.to_dict(config.from_dict(Project, config.preset_dict(name)))
                         for name in config.PRESETS},
        "footage-item.json": config.to_dict(FootageItem(path="")),
        "factory.json": factory,
        "voice.json": {"presets": voiceover.VOICE_PRESETS, "default_voice_id": voiceover.DEFAULT_VOICE_ID},
        "config.json": {"session_id": session, "artifact": artifact,
                        "connector": "Claude Code Remote", "tool": "send_message",
                        "repo": repo, "branch": branch},
        "voice-previews.json": json.loads((HERE / "voice-previews.json").read_text(encoding="utf-8"))
                               if (HERE / "voice-previews.json").is_file() else {},
    }
    for name, value in files.items():
        (data / name).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(HERE / "dist"))
    ap.add_argument("--session", default="", help="Claude Code session that plans and renders")
    ap.add_argument("--artifact", default="", help="the panel's claude.ai artifact URL")
    ap.add_argument("--repo", default="Yonatan2007/NeoForge-1.21.1", help="GitHub owner/repo with this code")
    ap.add_argument("--branch", default="claude/eager-hypatia-yaqaw0", help="branch with this code")
    args = ap.parse_args()
    out = build(Path(args.out), args.session, args.artifact, args.repo, args.branch)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    print(json.dumps({"out": str(out), "files": files}, indent=1))


if __name__ == "__main__":
    main()
