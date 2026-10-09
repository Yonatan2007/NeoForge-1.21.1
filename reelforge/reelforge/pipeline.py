"""End-to-end orchestration for a ``Project``:

script -> voice -> length fit -> word timings -> hook + captions -> shot plan
-> footage (the user's own media first, stock for the rest) -> colour grade
-> overlays -> audio mix (voice + music) -> MP4, SRT, cover image, credits.

``plan_project`` answers "what will happen" quickly (no downloads, no
rendering) for the UI; ``render_project`` does the work and reports progress.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import alignment, fonts, footage, media, timing, voiceover
from .assemble import assemble, plan_shots
from .captions import Caption, CaptionRenderer, build_captions, write_srt
from .config import FootageItem, Project, Settings
from .script import Script, Word, footage_queries, parse_script

log = logging.getLogger("reelforge")

Progress = Callable[[str, float, str], None]

# Share of the overall progress bar per stage (start, end).
STAGES = {
    "voice": (0.00, 0.08),
    "timing": (0.08, 0.18),
    "footage": (0.18, 0.45),
    "prepare": (0.45, 0.60),
    "render": (0.60, 0.97),
    "finish": (0.97, 1.00),
}
# How much a portrait source matters for each output shape.
PORTRAIT_WEIGHT = {"9:16": 3.0, "4:5": 2.0, "1:1": 0.5, "16:9": -3.0}  # negative: landscape wins
# Extra searches tried for the hook shot; the clearest skyline across all wins.
HOOK_SEARCHES = ("mountains", "hills", "hiking", "snow mountains")
SKYLINE_WEIGHT = 12.0     # the hook clip is chosen almost only by its skyline
REFERENCE_WEIGHT = 5.0    # similarity to the reference look steers every other pick


class Cancelled(RuntimeError):
    """Raised when a render is cancelled from the UI."""


class _Reporter:
    def __init__(self, progress: Progress | None, cancel: threading.Event | None):
        self.progress, self.cancel = progress, cancel

    def __call__(self, stage: str, frac: float = 0.0, message: str = "") -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("render cancelled")
        a, b = STAGES[stage]
        overall = a + (b - a) * min(1.0, max(0.0, frac))
        if message:
            log.info("[%s] %s", stage, message)
        if self.progress:
            self.progress(stage, overall, message)


def slugify(text: str, max_words: int = 6) -> str:
    return "-".join(re.findall(r"[^\W_]+", text.lower())[:max_words])[:60].strip("-") or "reel"


def project_path(p: str, project_dir: Path) -> str:
    """Project files are stored relative to the project folder; URLs pass through."""
    if p.startswith(("http://", "https://")):
        return p
    path = Path(p).expanduser()
    return str(path if path.is_absolute() else Path(project_dir) / path)


def split_hook(script: Script, project: Project) -> tuple[list[Word], list[Word]]:
    """(hook words, caption words). With hook mode "center" every word is a
    normal caption; "terrain" and "off" set the opening sentence(s) apart."""
    hs = project.style.hook
    if hs.mode == "center" or hs.sentences <= 0:
        return [], list(script.words)
    hook = [w for w in script.words if w.sentence < hs.sentences]
    body = [w for w in script.words if w.sentence >= hs.sentences]
    return hook, body


def _hook_cut(hook_words: list[Word]) -> float:
    """Earliest time the hook shot may be cut: once its last word is spoken."""
    return hook_words[-1].end if hook_words else 0.0


def _hook_display_end(hook_words: list[Word], body: list[Word], project: Project,
                      shot_end: float) -> float:
    """The hook text stays ``hold`` seconds after its last word, but never
    past the hook shot or into the first normal caption."""
    end = min(shot_end, hook_words[-1].end + project.style.hook.hold)
    if body:
        end = min(end, body[0].start - project.style.caption.lead)
    return max(end, hook_words[-1].end)


@dataclass
class ShotSource:
    kind: str                     # "user" | "stock"
    item: FootageItem | None = None
    query: str | None = None

    def describe(self) -> str:
        return f"user:{self.item.path}" if self.item else f"stock:{self.query}"


_SEARCH_FILLER = {"only", "just", "some", "all", "use", "with", "of", "the", "a", "an", "and", "please",
                  "footage", "clips", "clip", "videos", "video", "shots", "shot", "stock", "broll", "b-roll"}
_TIME_OF_DAY = {"night", "nighttime", "dark", "evening", "dusk", "day", "daytime", "daylight", "morning",
                "sunrise", "sunset", "dawn", "noon", "sunny", "golden"}


_NEGATION = re.compile(r"\b(?:no|not|without|avoid|except|minus)\b", re.I)
# what a "no ..." in a search rules out, typos included: stock clips are named in words like these
_AVOID_GROUPS = {
    "people": {"people", "person", "man", "men", "woman", "women", "girl", "boy", "couple", "crowd",
               "kid", "child", "children", "family", "friends", "lady", "guy", "human", "silhouette",
               "hand", "face", "portrait", "dancing", "model"},
    "sun": {"sun", "sunrise", "sunset", "sunny", "sunlight", "dawn", "golden"},
    "cars": {"car", "traffic", "road", "highway", "driving"},
    "city": {"city", "street", "building", "urban", "downtown", "skyscraper"},
}
_AVOID_ALIASES = {"pepole": "people", "peple": "people", "poeple": "people", "ppl": "people",
                  "persons": "people", "humans": "people", "person": "people", "rize": "sun",
                  "sunrize": "sun", "sunrise": "sun", "sunset": "sun", "sunny": "sun", "car": "cars",
                  "traffic": "cars", "cities": "city", "urban": "city", "buildings": "city"}


def split_search(query: str) -> tuple[str, set[str]]:
    """(what to search for, words a clip must not show). "moody rain no pepole
    no sun rize" -> ("moody rain", {people, man, woman, ..., sun, sunrise, ...}):
    stock search can't do "no", so the words after it filter the results."""
    parts = _NEGATION.split(query)
    words = [w for w in parts[0].split() if w.lower().strip(",.") not in _SEARCH_FILLER]
    avoid: set[str] = set()
    for part in parts[1:]:
        for w in re.findall(r"[a-z]+", part.lower()):
            if w in _SEARCH_FILLER:
                continue
            group = _AVOID_ALIASES.get(w, w)
            avoid |= _AVOID_GROUPS.get(group, {group})
    return " ".join(words) or (parts[0].strip() if parts[0].strip() else query), avoid


def search_terms(query: str) -> str:
    """A search the user typed, without words that only describe the request
    or what it rules out: "only night footage" -> "night"."""
    return split_search(query)[0]


def avoided(queries: list[str] | None) -> set[str]:
    """Every word the user's searches rule out with "no ..."."""
    return set().union(*(split_search(q)[1] for q in queries)) if queries else set()


def themed(queries: list[str], custom: list[str]) -> list[str]:
    """The script's own searches with the user's search words added, so a
    fallback search for "couple silhouette sunset" under "only night footage"
    becomes "couple silhouette night" (a time of day in the user's words
    replaces the one in the search)."""
    theme = list(dict.fromkeys(w for q in custom for w in search_terms(q).lower().split()))
    avoid = avoided(custom)
    queries = [q for q in queries if not avoid & set(q.lower().split())]  # "no people": drop "lonely man walking"
    if not theme:
        return list(queries)
    timed = any(w in _TIME_OF_DAY for w in theme)
    out = []
    for q in queries:
        words = [w for w in q.split() if not (timed and w.lower() in _TIME_OF_DAY)]
        out.append(" ".join(dict.fromkeys(words + [w for w in theme if w not in words])))
    return out


def assign_sources(project: Project, script: Script, n_shots: int, hook_terrain: bool,
                   ref_terms: list[str] | None = None) -> tuple[list[ShotSource], list[str]]:
    """One source per shot: pinned user media, then user media in upload order,
    then stock searches (the hook shot gets ``hook_query`` so it has a
    skyline). Returns (sources, warnings)."""
    fs = project.footage
    warnings: list[str] = []
    user = [i for i in fs.items if i.role == "footage"]
    sources: list[ShotSource | None] = [None] * n_shots
    for item in user:
        if item.shot is not None and 0 <= item.shot < n_shots and sources[item.shot] is None:
            sources[item.shot] = ShotSource("user", item)
        elif item.shot is not None:
            warnings.append(f"{Path(item.path).name}: shot {item.shot + 1} is not available, "
                            "using it in upload order")
    queue = [i for i in user if not any(s is not None and s.item is i for s in sources)]
    for k in range(n_shots):
        if sources[k] is None and queue:
            sources[k] = ShotSource("user", queue.pop(0))
    if queue:
        warnings.append(f"{len(queue)} uploaded clip(s) not used: the video has only {n_shots} shots")

    empty = [k for k in range(n_shots) if sources[k] is None]
    if empty and fs.stock:
        hook_slot = hook_terrain and bool(fs.hook_query) and 0 in empty and not fs.queries
        if hook_slot:
            sources[0] = ShotSource("stock", query=fs.hook_query)
            empty.remove(0)
        if fs.queries:
            custom = [search_terms(q) for q in fs.queries]
            queries = [custom[j % len(custom)] for j in range(len(empty))]
        elif empty:
            pool = [q for q in dict.fromkeys((ref_terms or []) +
                                             footage_queries(script, len(empty) + 5, palette=fs.palette))
                    if not (hook_slot and q == fs.hook_query)]
            queries = [pool[j % len(pool)] for j in range(len(empty))]
        else:
            queries = []
        for k, q in zip(empty, queries):
            sources[k] = ShotSource("stock", query=q)
    elif empty and user:
        for j, k in enumerate(empty):  # no stock: repeat the user's media
            sources[k] = ShotSource("user", user[j % len(user)])
    elif empty:
        raise ValueError("No footage: upload pictures or videos, or turn on stock footage.")
    return [s for s in sources if s is not None], warnings


# --------------------------------------------------------------------------- plan

def _estimate_seconds(project: Project, project_dir: Path, script: Script) -> float:
    v = project.voice
    if v.source == "none":
        return timing.reading_seconds(script.words, v.words_per_second)
    if v.file and not v.file.startswith(("http://", "https://")):
        path = Path(project_path(v.file, project_dir))
        if path.is_file():
            try:
                return media.duration(path)
            except media.MediaError:
                pass
    return len(script.words) / 2.6 + 0.4 * script.sentence_count  # typical narration pace


def plan_project(project: Project, project_dir: Path, settings: Settings) -> dict:
    """Fast preview of the reel: captions, hook, estimated length, shots and
    their sources, stock queries, the Higgsfield request and warnings."""
    script = parse_script(project.script, project.auto_emphasis)
    if not script.words:
        return {"captions": [], "hook": [], "estimated_seconds": 0.0,
                "target_seconds": project.duration.target, "shots": [], "queries": [],
                "higgsfield_request": None, "warnings": ["Write or upload a script first."],
                "hook_preview": None}
    vs, cs = project.style.video, project.style.caption
    warnings: list[str] = []
    speech = _estimate_seconds(project, Path(project_dir), script)
    plan = timing.fit_duration(speech, project.duration, vs.voice_delay, vs.tail)
    warnings += plan.warnings
    timing.synthetic_timings(script.words, plan.intro, plan.intro + speech / plan.tempo)

    hook_words, body = split_hook(script, project)
    hook_terrain = project.style.hook.mode == "terrain" and bool(hook_words)
    shots = plan_shots(script.words, plan.total, vs,
                       keep_until=_hook_cut(hook_words) if hook_terrain else 0.0)
    try:
        sources, w = assign_sources(project, script, len(shots), hook_terrain)
        warnings += w
    except ValueError as exc:
        sources = []
        warnings.append(str(exc))
    rows = []
    for k, (a, b) in enumerate(shots):
        text = " ".join(w.text for w in script.words if a <= w.start < b)
        rows.append({"start": round(a, 2), "end": round(b, 2), "text": text,
                     "source": sources[k].describe() if k < len(sources) else None})
    if project.voice.source == "higgsfield" and not project.voice.file:
        warnings.append("Voice: ask Claude to generate the Higgsfield voiceover, then upload it "
                        "or paste its link.")
    if project.voice.source in ("file",) and not project.voice.file:
        warnings.append("Voice: upload a voiceover file, or pick another voice source.")
    captions = build_captions(body, cs)
    request = voiceover.higgsfield_request(script, project.voice.higgsfield_preset,
                                           project.voice.voice_id or settings.higgsfield_voice_id,
                                           voice_type=project.voice.voice_type if project.voice.voice_id else "preset")
    return {
        "captions": [fonts.apply_case(c.text, cs.case) for c in captions],
        "hook": [fonts.apply_case(w.text, cs.case) for w in hook_words] if hook_terrain else [],
        "estimated_seconds": round(plan.total, 1),
        "target_seconds": project.duration.target,
        "shots": rows,
        "queries": [s.query for s in sources if s.kind == "stock"],
        "higgsfield_request": request,
        "warnings": warnings,
        "hook_preview": None,
    }


# --------------------------------------------------------------------------- render

def _voice_file(project: Project, project_dir: Path, work: Path, settings: Settings,
                script: Script) -> Path | None:
    v = project.voice
    if v.source == "none":
        return None
    if v.source in ("file", "higgsfield"):
        if not v.file:
            if v.source == "higgsfield":
                raise ValueError("No voiceover yet: ask Claude to generate it with Higgsfield "
                                 "(Voice tab, 'copy request'), then upload it or paste its link.")
            raise ValueError("Upload a voiceover (Voice tab) or choose another voice source.")
        return media.fetch(project_path(v.file, project_dir), work, "voice_source")
    if v.source == "piper":
        if not v.piper_model:
            raise ValueError("Piper needs a voice model (.onnx) in the Voice settings.")
        return voiceover.synthesize_piper(script.text, project_path(v.piper_model, project_dir),
                                          work / "voice_piper.wav")
    if v.source == "higgsfield-api":
        request = voiceover.higgsfield_request(script, v.higgsfield_preset,
                                               v.voice_id or settings.higgsfield_voice_id,
                                               voice_type=v.voice_type if v.voice_id else "preset")
        api = voiceover.HiggsfieldAPI(settings.higgsfield_key, settings.higgsfield_tts_endpoint)
        return api.synthesize(request["params"], work / "voice_higgsfield")
    raise ValueError(f"unknown voice source {v.source!r}")


def _providers(project: Project, settings: Settings) -> list:
    wanted = set(project.footage.sources)
    providers: list = []
    if "pexels" in wanted and settings.pexels_api_key:
        providers.append(footage.Pexels(settings.pexels_api_key, settings.cache_dir))
    if "pixabay" in wanted and settings.pixabay_api_key:
        providers.append(footage.Pixabay(settings.pixabay_api_key, settings.cache_dir))
    if "mixkit" in wanted:
        providers.append(footage.Mixkit(settings.cache_dir))
    return providers


def _frame_at(path: Path, t: float):
    from moviepy import VideoFileClip

    clip = VideoFileClip(str(path), audio=False)
    try:
        return clip.get_frame(min(max(0.0, t), max(0.0, clip.duration - 0.05)))
    finally:
        clip.close()


def render_project(project: Project, project_dir: Path, settings: Settings,
                   progress: Progress | None = None, cancel: threading.Event | None = None) -> dict:
    """Build the reel. Returns output paths relative to ``project_dir``."""
    from . import hook as hookmod, look, music, usermedia  # heavier modules, loaded on demand

    report = _Reporter(progress, cancel)
    media.require_ffmpeg()
    project_dir = Path(project_dir)
    work, out_dir = project_dir / "work", project_dir / "output"
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("credits.txt", "credits.json"):  # a render without stock has no credits
        (out_dir / stale).unlink(missing_ok=True)
    style = project.style
    vs, cs, hs = style.video, style.caption, style.hook
    warnings: list[str] = []
    script = parse_script(project.script, project.auto_emphasis)
    if not script.words:
        raise ValueError("The script is empty.")

    # 1. Voice ------------------------------------------------------------------
    report("voice", 0.0, "preparing the voiceover")
    raw = _voice_file(project, project_dir, work, settings, script)
    voice_wav: Path | None = None
    speech_start = 0.0
    if raw is not None:
        clean = media.prepare_voiceover(raw, work / "voice_clean.wav")
        samples = media.decode_audio(clean, alignment.SAMPLE_RATE)
        regions = alignment.speech_regions(samples)
        if regions:  # trim leading/trailing silence so the length plan is exact
            speech_start, speech_end = max(0.0, regions[0][0] - 0.05), regions[-1][1] + 0.15
        else:
            speech_start, speech_end = 0.0, len(samples) / alignment.SAMPLE_RATE
        speech = speech_end - speech_start
    else:
        speech = timing.reading_seconds(script.words, project.voice.words_per_second)
    report("voice", 1.0, "voice ready")

    # 2. Length and word timings --------------------------------------------------
    plan = timing.fit_duration(speech, project.duration, vs.voice_delay, vs.tail)
    warnings += plan.warnings
    report("timing", 0.1, f"video length {plan.total:.1f} s (voice tempo {plan.tempo:.2f}x)")
    if raw is not None:
        voice_wav = media.trim_and_tempo(work / "voice_clean.wav", work / "voice.wav",
                                         speech_start, speech_start + speech, plan.tempo)
        samples = media.decode_audio(voice_wav, alignment.SAMPLE_RATE)
        method = alignment.time_words(script, samples, project.voice.align,
                                      settings.whisper_model, settings.whisper_device)
        for w in script.words:
            w.start += plan.intro
            w.end += plan.intro
    else:
        method = "reading pace"
        timing.synthetic_timings(script.words, plan.intro, plan.total - plan.outro)
    total = plan.total
    report("timing", 1.0, f"word timings via {method}")

    hook_words, body = split_hook(script, project)
    hook_terrain = hs.mode == "terrain" and bool(hook_words)
    shots = plan_shots(script.words, total, vs,
                       keep_until=_hook_cut(hook_words) if hook_terrain else 0.0)

    # 3. Footage ----------------------------------------------------------------
    report("footage", 0.0, f"{len(shots)} shots; finding footage")
    references = [Path(project_path(i.path, project_dir)) for i in project.footage.items
                  if i.role == "reference"]
    references = [p for p in references if p.is_file()]
    ref_sigs = ([usermedia.signature(p) for p in references]
                if references and project.footage.reference_matching else [])
    ref_terms = usermedia.palette_terms(ref_sigs) if ref_sigs else []
    if not ref_sigs and project.footage.reference_matching and project.footage.palette == "bright":
        ref_sigs = list(usermedia.builtin_reference_signatures())  # rank toward the reference look
    sources, w = assign_sources(project, script, len(shots), hook_terrain, ref_terms)
    warnings += w

    stock_slots = [k for k, s in enumerate(sources) if s.kind == "stock"]
    stock_paths: dict[int, Path] = {}
    if stock_slots:
        providers = _providers(project, settings)
        wide = vs.aspect == "16:9"
        common = dict(min_duration=vs.max_shot + vs.crossfade,
                      allow_landscape=project.footage.allow_landscape or wide,
                      portrait_weight=PORTRAIT_WEIGHT.get(vs.aspect, 3.0),
                      palette=project.footage.palette, wide=wide,
                      avoid=avoided(project.footage.queries) | avoided([project.footage.hook_query or ""]))

        def likeness(c, img) -> float:
            return REFERENCE_WEIGHT * usermedia.reference_score(img, ref_sigs) if ref_sigs else 0.0

        picks: list = []
        rest = list(stock_slots)
        if hook_terrain and 0 in stock_slots and project.footage.hook_query and not project.footage.queries:
            report("footage", 0.05, "choosing a first shot with a clear skyline")
            hook_pick = footage.best_of(
                [search_terms(project.footage.hook_query), *HOOK_SEARCHES], providers, settings.cache_dir,
                rank=lambda c, img: (SKYLINE_WEIGHT * hookmod.skyline_score(img) if img is not None
                                     else 0.0) + likeness(c, img), **common)
            if hook_pick is not None:
                picks.append(hook_pick)
                stock_paths[0] = hook_pick[1]
                rest.remove(0)
        if rest:
            queries = [sources[k].query for k in rest]
            spare = [q for q in footage_queries(script, len(queries) + 6, palette=project.footage.palette)
                     if q not in queries]
            if project.footage.queries:  # the user's searches set the theme of the fallbacks too
                spare = [q for q in dict.fromkeys(themed(spare, project.footage.queries)) if q not in queries]
            found = footage.fetch_footage(
                queries + spare, providers, settings.cache_dir, count=len(rest),
                extra_score=likeness, exclude={c.key for c, _ in picks}, **common)
            if len(found) < len(rest):
                warnings.append(f"Found {len(found)} stock clips for {len(rest)} shots; "
                                "some clips repeat.")
            for j, k in enumerate(rest):
                stock_paths[k] = found[j % len(found)][1]
            picks += found
        footage.write_credits(picks, out_dir / "credits.txt")
    report("footage", 1.0, "footage ready")

    # 4. Prepare (cover-crop to the output size, grade) -------------------------------
    target = look.target_stats(style.look, references)
    prepared: list[Path] = []
    done: dict[tuple, Path] = {}
    prep_dir = settings.cache_dir / "prepared"
    for k, src in enumerate(sources):
        a, b = shots[k]
        report("prepare", k / len(sources), f"grading shot {k + 1}/{len(sources)}")
        if src.kind == "stock":
            key: tuple = ("stock", str(stock_paths[k]))
            if key not in done:
                done[key] = look.prepare_clip(stock_paths[k], prep_dir, vs, style.look, target)
        else:
            item = src.item
            path = Path(project_path(item.path, project_dir))
            kind = item.kind if item.kind != "auto" else usermedia.media_kind(path)
            if kind == "image":
                seconds = item.seconds or max(vs.image_seconds, (b - a) + vs.crossfade + 0.3)
                key = ("image", str(path), seconds, item.motion, vs.aspect)
                if key not in done:
                    clip = usermedia.image_to_video(path, work / "images", seconds, item.motion,
                                                    vs.width, vs.height, vs.fps)
                    done[key] = look.prepare_clip(clip, prep_dir, vs, style.look, target)
            else:
                key = ("video", str(path), item.trim_in, item.trim_out)
                if key not in done:
                    length = look.video_duration(path) if item.trim_in else None
                    if length is not None and item.trim_in >= length - 1.0 / vs.fps:
                        warnings.append(f"{path.name}: 'Start at' {item.trim_in:g} s is past the end "
                                        f"of the clip ({length:.1f} s), so its last part is used.")
                    done[key] = look.prepare_clip(path, prep_dir, vs, style.look, target,
                                                  trim_in=item.trim_in, trim_out=item.trim_out)
        prepared.append(done[key])
    report("prepare", 1.0, "clips graded")

    # 5. Overlays and audio -------------------------------------------------------------
    overlays: list = []
    hook_end = 0.0
    if hook_terrain:
        hook_end = _hook_display_end(hook_words, body, project, shots[0][1])
        mid = (hook_words[0].start + hook_words[-1].end) / 2
        overlays.append(hookmod.HookRenderer(
            hook_words, hs, fonts.resolve(hs.font, hs.font_weight, settings.cache_dir),
            _frame_at(prepared[0], mid), end=hook_end, case=cs.case))
    captions = build_captions(body, cs)
    if captions:
        overlays.append(CaptionRenderer(
            captions, cs, fonts.resolve(cs.font, cs.font_weight, settings.cache_dir),
            vs.width, vs.height))
    hook_cue = ([Caption(hook_words, max(0.0, hook_words[0].start - cs.lead),
                         max(hook_end, hook_words[-1].end), [])] if hook_words else [])
    write_srt(hook_cue + captions, out_dir / "captions.srt")

    ms = project.music
    if ms.file:
        ms = dataclasses.replace(ms, file=project_path(ms.file, project_dir))
        if ms.file.startswith(("http://", "https://")):
            ms = dataclasses.replace(ms, file=str(media.fetch(ms.file, work, "music_source")))
    music_wav = music.prepare_music(ms, total, work / "music.wav", voice_wav, plan.intro)
    audio = (music.mix(voice_wav, music_wav, plan.intro, total, work / "mix.wav")
             if (voice_wav or music_wav) else None)

    # 6. Render -------------------------------------------------------------------------
    name = slugify(project.name if project.name not in ("", "untitled") else project.script)
    video = out_dir / f"{name}.mp4"
    report("render", 0.0, "rendering video")
    assemble(prepared, shots, audio, overlays, video, vs, logger=None,
             on_progress=lambda f: report("render", f))

    report("finish", 0.2, "writing cover and timings")
    cover = media.extract_frame(video, (hook_end - 0.1) if hook_terrain else min(1.5, total / 2),
                                out_dir / "cover.jpg")
    (out_dir / "timings.json").write_text(json.dumps({
        "duration": total, "tempo": plan.tempo, "intro": plan.intro, "outro": plan.outro,
        "method": method,
        "words": [{"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3),
                   "emphasis": w.emphasis} for w in script.words],
        "hook": [w.text for w in hook_words] if hook_terrain else [],
        "captions": [{"text": c.text, "start": round(c.start, 3), "end": round(c.end, 3)}
                     for c in captions],
        "shots": [{"start": round(a, 3), "end": round(b, 3), "source": s.describe()}
                  for (a, b), s in zip(shots, sources)],
        "warnings": warnings,
    }, indent=2, ensure_ascii=False) + "\n")
    report("finish", 1.0, "done")

    def rel(p: Path) -> str:
        return str(Path(p).relative_to(project_dir))

    credits = out_dir / "credits.txt"
    return {"video": rel(video), "srt": rel(out_dir / "captions.srt"), "cover": rel(cover),
            "credits": rel(credits) if credits.exists() else None,
            "timings": rel(out_dir / "timings.json"), "duration": round(total, 2),
            "warnings": warnings}
