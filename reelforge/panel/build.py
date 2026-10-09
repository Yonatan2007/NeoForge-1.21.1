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
PANEL_FILES = ("panel-backend.js", "panel-boot.js", "panel-voices.js", "panel-publish.js", "panel.css")

INDEX = """<title>reelforge</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect x='2' y='2' width='28' height='28' rx='8' fill='%233ec6c1'/%3E%3Cpath d='M6 22.5 12 14l3.6 4.6L19.5 10 26 22.5' fill='none' stroke='%2304201f' stroke-width='2.4' stroke-linejoin='round' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="styles.css">
<link rel="stylesheet" href="panel.css">
<style>
  :root { --panel-bg: #0d0f12; --panel-fg: #e7e9ee; --panel-muted: #9aa1ad; color-scheme: dark; }
  html, body { background: var(--panel-bg); color: var(--panel-fg); }
  .panel-boot { min-height: 60vh; display: grid; place-items: center; padding-inline: 16px; text-align: center; }
  .panel-boot p { max-width: 34rem; color: var(--panel-muted); font: 400 15px/1.6 Inter, system-ui, sans-serif; }
  .panel-boot strong { color: var(--panel-fg); }
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


def build(out: Path, session: str, artifact: str) -> Path:
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
                        "connector": "Claude Code Remote", "tool": "send_message"},
    }
    for name, value in files.items():
        (data / name).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(HERE / "dist"))
    ap.add_argument("--session", default="", help="Claude Code session that plans and renders")
    ap.add_argument("--artifact", default="", help="the panel's claude.ai artifact URL")
    args = ap.parse_args()
    out = build(Path(args.out), args.session, args.artifact)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    print(json.dumps({"out": str(out), "files": files}, indent=1))


if __name__ == "__main__":
    main()
