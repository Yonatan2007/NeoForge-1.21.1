"""End-to-end orchestration: script -> voice -> word timings -> captions ->
shot plan -> stock footage -> assembled MP4."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import alignment, footage, media, voiceover
from .assemble import assemble, plan_shots
from .captions import Caption, CaptionRenderer, build_captions, resolve_font, write_srt
from .config import Settings, Style
from .script import Script, footage_queries, parse_script

log = logging.getLogger("reelforge")


def slugify(text: str, max_words: int = 6) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())[:max_words]) or "reel"


@dataclass
class RenderOptions:
    voiceover: str | None = None         # file path or URL, e.g. the Higgsfield result
    tts: str | None = None               # "piper" | "higgsfield-api"
    piper_model: str | None = None
    voice_preset: str = "elevenlabs"
    voice_id: str | None = None
    footage_dir: Path | None = None
    queries: list[str] | None = None
    mixkit: bool = True                  # keyless free stock source
    music: Path | None = None
    align: str = "auto"                  # "auto" | "whisper" | "estimate"
    auto_emphasis: bool = True
    allow_landscape: bool = True


def _preview(captions: list[Caption]) -> list[str]:
    """Caption chunks with emphasis marked: [yellow] and {red}."""
    lines = []
    for c in captions:
        parts = []
        for w in c.words:
            parts.append({1: f"[{w.text}]", 2: f"{{{w.text}}}"}.get(w.emphasis, w.text))
        lines.append(" ".join(parts))
    return lines


def plan(text: str, out_dir: Path, settings: Settings, style: Style,
         voice_preset: str = "elevenlabs", voice_id: str | None = None,
         auto_emphasis: bool = True) -> dict:
    """Everything that can be decided before the voiceover exists, plus the
    exact Higgsfield connector request that produces the voiceover."""
    script = parse_script(text, auto_emphasis)
    out_dir.mkdir(parents=True, exist_ok=True)
    request = voiceover.higgsfield_request(script, voice_preset, voice_id or settings.higgsfield_voice_id)
    req_path = voiceover.write_request(request, out_dir / "higgsfield_request.json")
    seconds = len(script.words) / 2.4 + style.video.voice_delay + style.video.tail  # ~145 wpm read
    shots = max(3, round(seconds / ((style.video.min_shot + style.video.max_shot) / 2)))
    summary = {
        "words": len(script.words),
        "sentences": script.sentence_count,
        "estimated_seconds": round(seconds, 1),
        "captions": _preview(build_captions(script.words, style.caption)),
        "footage_queries": footage_queries(script, shots),
        "higgsfield_request": str(req_path),
    }
    (out_dir / "plan.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


def _voice(script: Script, work: Path, settings: Settings, opts: RenderOptions) -> Path:
    if opts.voiceover:
        return media.fetch(opts.voiceover, work, "voice_source")
    if opts.tts == "piper":
        if not opts.piper_model:
            raise ValueError("--tts piper needs --piper-model /path/to/voice.onnx")
        return voiceover.synthesize_piper(script.text, opts.piper_model, work / "voice_piper.wav")
    if opts.tts == "higgsfield-api":
        request = voiceover.higgsfield_request(script, opts.voice_preset,
                                               opts.voice_id or settings.higgsfield_voice_id)
        api = voiceover.HiggsfieldAPI(settings.higgsfield_key, settings.higgsfield_tts_endpoint)
        return api.synthesize(request["params"], work / "voice_higgsfield")
    raise ValueError("no voiceover: pass --voiceover <file|url> (generate it with the Higgsfield "
                     "connector; see `reelforge plan`) or --tts piper|higgsfield-api")


def render(text: str, out_dir: Path, settings: Settings, style: Style,
           opts: RenderOptions, logger: str | None = "bar") -> Path:
    media.require_ffmpeg()
    vs, cs = style.video, style.caption
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    script = parse_script(text, opts.auto_emphasis)
    if not script.words:
        raise ValueError("the script is empty")

    # 1. Voice: fetch/generate, then clean up and loudness-normalise.
    voice = media.prepare_voiceover(_voice(script, work, settings, opts), work / "voice.wav")

    # 2. Word timings from the actual audio.
    samples = media.decode_audio(voice, alignment.SAMPLE_RATE)
    method = alignment.time_words(script, samples, opts.align, settings.whisper_model,
                                  settings.whisper_device)
    for w in script.words:
        w.start += vs.voice_delay
        w.end += vs.voice_delay
    total = script.words[-1].end + vs.tail
    log.info("voice %.1fs, timings via %s, video %.1fs", len(samples) / alignment.SAMPLE_RATE, method, total)

    # 3. Captions and shot plan.
    captions = build_captions(script.words, cs)
    write_srt(captions, out_dir / "captions.srt")
    shots = plan_shots(script.words, total, vs)

    # 4. Footage: user clips or stock search driven by the script.
    if opts.footage_dir:
        sources = footage.local_footage(opts.footage_dir)
    else:
        providers: list = []
        if settings.pexels_api_key:
            providers.append(footage.Pexels(settings.pexels_api_key, settings.cache_dir))
        if settings.pixabay_api_key:
            providers.append(footage.Pixabay(settings.pixabay_api_key, settings.cache_dir))
        if opts.mixkit:
            providers.append(footage.Mixkit(settings.cache_dir))
        # Spare queries cover searches that come back empty.
        queries = opts.queries or footage_queries(script, len(shots) + 6)
        picks = footage.fetch_footage(queries, providers, settings.cache_dir,
                                      min_duration=vs.max_shot + vs.crossfade,
                                      allow_landscape=opts.allow_landscape, count=len(shots))
        footage.write_credits(picks, out_dir / "credits.txt")
        sources = [path for _, path in picks]
    prepared = [footage.prepare_clip(p, settings.cache_dir / "prepared", vs) for p in sources]

    # 5. Assemble and export.
    renderer = CaptionRenderer(captions, cs, resolve_font(cs.font_path, settings.cache_dir),
                               vs.width, vs.height)
    out = assemble(prepared, shots, voice, renderer, out_dir / f"{out_dir.name}.mp4", vs,
                   opts.music, logger=logger)

    (out_dir / "timings.json").write_text(json.dumps({
        "method": method,
        "duration": total,
        "words": [{"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3),
                   "emphasis": w.emphasis} for w in script.words],
        "captions": [{"text": c.text, "start": round(c.start, 3), "end": round(c.end, 3)}
                     for c in captions],
        "shots": [[round(a, 3), round(b, 3)] for a, b in shots],
        "clips": [str(p) for p in sources],
    }, indent=2, ensure_ascii=False) + "\n")
    return out
