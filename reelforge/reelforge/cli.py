"""Command line: `python -m reelforge plan|render ...`."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings, Style
from .pipeline import RenderOptions, plan, render, slugify
from .voiceover import VOICE_PRESETS


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


def _style(args: argparse.Namespace) -> Style:
    style = Style()
    cs = style.caption
    cs.max_words = args.words_per_caption
    cs.reveal = args.reveal
    cs.uppercase = not args.no_uppercase
    cs.y_center = args.y
    cs.font_path = args.font
    if args.font_size:
        cs.font_size = args.font_size
    return style


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reelforge", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("script", help="script .txt file, '-' for stdin, or the text itself")
        sp.add_argument("--out", default="output", help="output root directory (default: output)")
        sp.add_argument("--slug", help="name of the output folder/video (default: from the script)")
        sp.add_argument("--voice-preset", default="elevenlabs", choices=sorted(VOICE_PRESETS))
        sp.add_argument("--voice-id", help="Higgsfield voice id (see the connector's list_voices)")
        sp.add_argument("--no-auto-emphasis", action="store_true",
                        help="only colour words marked *like this* or **like this**")
        sp.add_argument("--words-per-caption", type=int, default=3, help="1 = word-by-word")
        sp.add_argument("--reveal", choices=["build", "phrase"], default="build",
                        help="build: each word pops as spoken; phrase: whole caption pops")
        sp.add_argument("--no-uppercase", action="store_true")
        sp.add_argument("--font", help="path to a .ttf/.otf (default: Montserrat Black)")
        sp.add_argument("--font-size", type=int)
        sp.add_argument("--y", type=float, default=0.5, help="caption centre, 0=top 1=bottom")
        sp.add_argument("-v", "--verbose", action="store_true")

    sp_plan = sub.add_parser("plan", help="analyse a script and write the Higgsfield voiceover request")
    common(sp_plan)

    sp_render = sub.add_parser("render", help="build the finished MP4")
    common(sp_render)
    sp_render.add_argument("--voiceover", help="voiceover file or URL (e.g. the Higgsfield result)")
    sp_render.add_argument("--tts", choices=["piper", "higgsfield-api"],
                           help="generate the voice here instead of passing --voiceover")
    sp_render.add_argument("--piper-model", help="Piper .onnx voice for --tts piper")
    sp_render.add_argument("--footage-dir", type=Path, help="use your own clips instead of stock search")
    sp_render.add_argument("--queries", help="override stock searches, separated by ';'")
    sp_render.add_argument("--music", type=Path, help="optional music bed (mixed low, looped)")
    sp_render.add_argument("--align", choices=["auto", "whisper", "estimate"], default="auto")
    sp_render.add_argument("--portrait-only", action="store_true", help="reject landscape stock clips")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    style = _style(args)
    text, slug = _read_script(args.script)
    out_dir = Path(args.out) / (args.slug or slug)

    if args.cmd == "plan":
        summary = plan(text, out_dir, settings, style, args.voice_preset, args.voice_id,
                       auto_emphasis=not args.no_auto_emphasis)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    opts = RenderOptions(
        voiceover=args.voiceover, tts=args.tts, piper_model=args.piper_model,
        voice_preset=args.voice_preset, voice_id=args.voice_id, footage_dir=args.footage_dir,
        queries=[q.strip() for q in args.queries.split(";") if q.strip()] if args.queries else None,
        music=args.music, align=args.align, auto_emphasis=not args.no_auto_emphasis,
        allow_landscape=not args.portrait_only)
    out = render(text, out_dir, settings, style, opts)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
