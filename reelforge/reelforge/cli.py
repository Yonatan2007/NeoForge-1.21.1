"""Command line.

    python -m reelforge ui                       open the web UI (recommended)
    python -m reelforge plan   SCRIPT [options]  preview captions, shots, length
    python -m reelforge render SCRIPT [options]  build the video
    python -m reelforge defaults [--show|--reset|--set path=value ...]
    python -m reelforge presets

SCRIPT is a .txt file, '-' for stdin, the text itself, or a project.json
(``--project``). Any setting can be changed with ``--set path=value``, e.g.
``--set style.caption.font_size=80 --set music.start_at=2``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config
from .config import FootageItem, Project, Settings
from .pipeline import plan_project, render_project, slugify
from .voiceover import VOICE_PRESETS

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}


def _read_script(arg: str) -> tuple[str, str]:
    if arg == "-":
        text = sys.stdin.read()
        return text, slugify(text)
    path = Path(arg)
    if path.is_file():
        return path.read_text(encoding="utf-8"), path.stem
    if path.suffix in (".txt", ".md") and " " not in arg:
        raise SystemExit(f"script file not found: {arg}")
    return arg, slugify(arg)  # the script itself was passed inline


def _set(data: dict, dotted: str, raw: str) -> None:
    """Apply ``path=value`` to the project dict; the value is parsed as JSON
    when possible (numbers, true/false, null, lists), else kept as text."""
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    node = data
    keys = dotted.split(".")
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            raise SystemExit(f"unknown setting: {dotted}")
        node = node[k]
    if keys[-1] not in node:
        raise SystemExit(f"unknown setting: {dotted}")
    node[keys[-1]] = value


def _media_items(directory: Path, role: str) -> list[dict]:
    files = sorted(p for p in directory.iterdir() if p.suffix.lower() in VIDEO_EXTS | IMAGE_EXTS)
    if not files:
        raise SystemExit(f"no pictures or videos in {directory}")
    return [config.to_dict(FootageItem(path=str(p.resolve()), role=role)) for p in files]


def _build_project(args: argparse.Namespace, settings: Settings) -> tuple[Project, Path]:
    if args.project:
        project = config.load_project(args.project)
        project_dir = Path(args.project).resolve().parent
        data = config.to_dict(project)
    else:
        text, slug = _read_script(args.script)
        base = (config.to_dict(config.load_defaults(settings)) if not args.preset
                else config.preset_dict(args.preset))
        data = base | {"script": text, "name": args.slug or slug}
        project_dir = Path(args.out).resolve() / (args.slug or slug)
    if args.preset and args.project:
        data = config.deep_merge(config.preset_dict(args.preset),
                                 {k: data[k] for k in ("name", "script", "voice", "music", "footage",
                                                       "duration")})
    if args.voiceover:
        data["voice"].update(source="file", file=args.voiceover)
    if args.tts:
        data["voice"]["source"] = args.tts
    if args.piper_model:
        data["voice"]["piper_model"] = args.piper_model
    if args.voice_id:
        data["voice"]["voice_id"] = args.voice_id
    if args.voice_preset:
        data["voice"]["higgsfield_preset"] = args.voice_preset
    if args.align:
        data["voice"]["align"] = args.align
    if args.music:
        data["music"]["file"] = str(Path(args.music).resolve()) if Path(args.music).exists() else args.music
    if args.footage_dir:
        data["footage"]["items"] += _media_items(Path(args.footage_dir), "footage")
    if args.reference_dir:
        data["footage"]["items"] += _media_items(Path(args.reference_dir), "reference")
    if args.queries:
        data["footage"]["queries"] = [q.strip() for q in args.queries.split(";") if q.strip()]
    if args.no_stock:
        data["footage"]["stock"] = False
    if args.duration is not None:
        data["duration"]["target"] = args.duration or None
    if args.aspect:
        data["style"]["video"]["aspect"] = args.aspect
    for item in args.set or []:
        path, _, raw = item.partition("=")
        _set(data, path.strip(), raw.strip())
    return config.from_dict(Project, data), project_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reelforge", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    ui = sub.add_parser("ui", help="open the web UI")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")

    def project_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("script", nargs="?", help="script .txt, '-' for stdin, or the text itself")
        sp.add_argument("--project", help="an existing project.json instead of a script")
        sp.add_argument("--out", default="output", help="projects root (default: output)")
        sp.add_argument("--slug", help="project / video name")
        sp.add_argument("--preset", choices=sorted(config.PRESETS))
        sp.add_argument("--voiceover", help="voiceover file or URL (e.g. the Higgsfield result)")
        sp.add_argument("--tts", choices=["piper", "higgsfield-api", "none"],
                        help="generate the voice here (none = music-only reel)")
        sp.add_argument("--piper-model")
        sp.add_argument("--voice-preset", choices=sorted(VOICE_PRESETS))
        sp.add_argument("--voice-id")
        sp.add_argument("--align", choices=["auto", "whisper", "estimate"])
        sp.add_argument("--music", help="music file (use --set music.source_in=... to trim)")
        sp.add_argument("--footage-dir", help="your pictures/videos, used in order")
        sp.add_argument("--reference-dir", help="pictures/videos that only steer the stock search and look")
        sp.add_argument("--queries", help="stock searches, one per shot, separated by ';'")
        sp.add_argument("--no-stock", action="store_true")
        sp.add_argument("--duration", type=float, help="target length in seconds (0 = automatic)")
        sp.add_argument("--aspect", choices=sorted(config.ASPECTS))
        sp.add_argument("--set", action="append", metavar="PATH=VALUE",
                        help="change any setting, e.g. style.caption.font_size=80")

    project_args(sub.add_parser("plan", help="preview captions, shots and length"))
    project_args(sub.add_parser("render", help="build the video"))

    d = sub.add_parser("defaults", help="show or change the saved defaults")
    d.add_argument("--show", action="store_true")
    d.add_argument("--reset", action="store_true")
    d.add_argument("--preset", choices=sorted(config.PRESETS), help="start the defaults from a preset")
    d.add_argument("--set", action="append", metavar="PATH=VALUE")

    sub.add_parser("presets", help="list the built-in looks")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()

    if args.cmd == "ui":
        from .server import serve
        serve(args.host, args.port, open_browser=not args.no_browser)
        return 0
    if args.cmd == "presets":
        for name, label in config.PRESET_LABELS.items():
            print(f"{name:10s} {label}")
        return 0
    if args.cmd == "defaults":
        if args.reset:
            config.reset_defaults(settings)
        data = (config.preset_dict(args.preset) if args.preset
                else config.to_dict(config.load_defaults(settings)))
        for item in args.set or []:
            path, _, raw = item.partition("=")
            _set(data, path.strip(), raw.strip())
        if args.preset or args.set:
            print("saved", config.save_defaults(config.from_dict(Project, data), settings))
        if args.show or not (args.reset or args.preset or args.set):
            print(json.dumps(config.to_dict(config.load_defaults(settings)), indent=2))
        return 0

    if not args.script and not args.project:
        raise SystemExit("give a script (file, '-' or text) or --project project.json")
    project, project_dir = _build_project(args, settings)
    config.save_project(project, project_dir / "project.json")
    if args.cmd == "plan":
        print(json.dumps(plan_project(project, project_dir, settings), indent=2, ensure_ascii=False))
        return 0
    result = render_project(project, project_dir, settings)
    for w in result["warnings"]:
        logging.warning(w)
    print(project_dir / result["video"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
