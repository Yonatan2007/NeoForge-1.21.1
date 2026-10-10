"""Project model: every setting the app (CLI and UI) can change.

* ``Settings``  - secrets and machine paths from the environment.
* ``Project``   - one reel: script, voice, music, footage, duration and the
                  visual ``Style`` (captions, hook, video, look).
* ``PRESETS``   - named partial overrides layered on the dataclass defaults.
                  The dataclass defaults ARE the "reference" look (bright film
                  footage, single white lowercase words, terrain hook).
* Saved defaults - the user's own overrides, stored as JSON and layered on top
                   of a preset, so "change the defaults" survives restarts.

Everything round-trips through plain JSON (``to_dict`` / ``from_dict``), which
is what the UI edits and what ``project.json`` stores.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import types
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path

log = logging.getLogger(__name__)

RGB = tuple[int, int, int]

ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),   # Reels / TikTok / Shorts
    "4:5": (1080, 1350),    # feed portrait
    "1:1": (1080, 1080),    # square (the reference reel's format)
    "16:9": (1920, 1080),   # YouTube landscape
}
BASE_WIDTH = 1080  # caption/hook pixel sizes are specified at this frame width


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    # Higgsfield REST credentials ("key_id:key_secret"). Only needed for the
    # headless `--tts higgsfield-api` route; the Claude connector route
    # authenticates through the connected account instead.
    higgsfield_key: str | None = None
    higgsfield_tts_endpoint: str | None = None
    higgsfield_voice_id: str | None = None
    whisper_model: str = "base.en"
    whisper_device: str = "cpu"
    cache_dir: Path = Path.home() / ".cache" / "reelforge"
    home_dir: Path = Path.home() / ".config" / "reelforge"   # saved defaults live here

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        hf_key = os.getenv("HF_KEY")
        if not hf_key and os.getenv("HF_API_KEY") and os.getenv("HF_API_SECRET"):
            hf_key = f"{os.environ['HF_API_KEY']}:{os.environ['HF_API_SECRET']}"
        return cls(
            pexels_api_key=os.getenv("PEXELS_API_KEY") or None,
            pixabay_api_key=os.getenv("PIXABAY_API_KEY") or None,
            higgsfield_key=hf_key or None,
            higgsfield_tts_endpoint=os.getenv("HIGGSFIELD_TTS_ENDPOINT") or None,
            higgsfield_voice_id=os.getenv("HIGGSFIELD_VOICE_ID") or None,
            whisper_model=os.getenv("WHISPER_MODEL", "base.en"),
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
            cache_dir=Path(os.getenv("REELFORGE_CACHE", str(Path.home() / ".cache" / "reelforge"))),
            home_dir=Path(os.getenv("REELFORGE_HOME", str(Path.home() / ".config" / "reelforge"))),
        )


# --------------------------------------------------------------------------- style

@dataclass
class CaptionStyle:
    """Captions after the hook. Pixel sizes are at a 1080 px wide frame and
    scale with the output width."""
    font: str = "inter"                    # "inter" | "montserrat" | path to a .ttf/.otf
    font_weight: str = "Bold"              # Inter: Regular..Black; Montserrat: Bold, ExtraBold, Black
    font_size: int = 70
    case: str = "lower"                    # lower | upper | as-is
    text_color: RGB = (255, 255, 255)
    emphasis: str = "none"                 # none | color (yellow / red key words)
    highlight_color: RGB = (255, 212, 0)   # emphasis level 1
    alert_color: RGB = (255, 59, 48)       # emphasis level 2
    stroke_width: int = 0
    stroke_color: RGB = (0, 0, 0)
    shadow_offset: tuple[int, int] = (0, 3)
    shadow_blur: float = 9.0
    shadow_opacity: float = 0.55
    max_words: int = 1                     # 1 = one word at a time (the reference look)
    max_chars: int = 18
    max_line_width: int = 920
    max_lines: int = 2
    line_spacing: float = 1.05
    y_center: float = 0.5                  # 0 = top, 1 = bottom
    reveal: str = "build"                  # build: words pop as spoken | phrase: whole caption at once
    pop_duration: float = 0.10
    pop_start_scale: float = 0.85
    lead: float = 0.05                     # show text slightly before it is heard
    hold: float = 0.30                     # linger after the last word of a caption
    min_duration: float = 0.20
    gap_break: float = 0.35                # a pause this long forces a new caption
    flicker_gap: float = 0.20              # gaps shorter than this are closed


@dataclass
class HookStyle:
    """The opening sentence(s). In "terrain" mode the words are laid along the
    skyline / ridge of the first shot (like text following a mountain range),
    stay on screen as they accumulate, and are rotated to the local slope."""
    mode: str = "terrain"                  # terrain | center (normal captions) | off (no hook text)
    sentences: int = 1                     # how many opening sentences form the hook
    font: str = "inter"
    font_weight: str = "Bold"
    size: int = 58                         # base word size, px at 1080 wide
    big_scale: float = 2.0                 # first word, last word and stressed words
    small_scale: float = 0.72              # short function words (a, the, to, of ...)
    color: str = "auto"                    # auto: dark on bright sky, light on dark | "#rrggbb"
    dark_color: RGB = (17, 17, 17)
    light_color: RGB = (255, 255, 255)
    shadow: bool = False                   # the reference hook has no shadow
    offset: int = 8                        # gap between the contour and the text baseline, px
    margin: int = 36                       # left/right safe margin, px
    per_glyph: bool = True                 # bend single letters around sharp peaks
    last_word_below: bool = True           # drop the final word large under the line end ("are?")
    hold: float = 0.6                      # keep the hook text this long after its last word
    min_confidence: float = 0.35           # weaker skylines use the fallback pattern
    fallback: str = "diagonal"             # diagonal | arc | center


@dataclass
class LookStyle:
    """Colour grade applied to every clip while it is normalised."""
    match: str = "reference"               # reference (built-in look of the reference reel)
                                           # | uploads (match the user's reference media) | off
    match_strength: float = 0.75           # 0 = untouched clip, 1 = full statistical match
    contrast: float = 1.0
    brightness: float = 0.0
    saturation: float = 1.05
    gamma: float = 1.0
    lift: float = 0.04                     # raise blacks (film fade), 0..0.15
    tint: str = ""                         # ffmpeg colorbalance args, e.g. "rs=-0.04:bh=-0.03"
    vignette: str = ""                     # ffmpeg vignette angle, e.g. "PI/5" ("" = none)
    grain: int = 3


@dataclass
class VideoStyle:
    aspect: str = "9:16"                   # 9:16 | 4:5 | 1:1 | 16:9
    letterbox: bool = False                # keep the picture's shape, pad the file to 9:16 with black
    draft: bool = False                    # half resolution for quick previews
    fps: int = 30
    min_shot: float = 3.0
    max_shot: float = 7.0
    cut_preroll: float = 0.12              # cut slightly before a sentence starts
    crossfade: float = 0.0                 # 0 = hard cuts (the reference look)
    fade_in: float = 0.1
    fade_out: float = 0.5
    voice_delay: float = 0.1               # silence before the first word
    tail: float = 0.8                      # picture after the last word
    image_seconds: float = 4.0             # default length of a clip made from a photo
    max_clip_seconds: float = 20.0
    crf: int = 18
    maxrate: str = "10M"                   # ~25 MB per 20 s; Instagram re-encodes anyway
    bufsize: str = "20M"
    preset: str = "medium"
    audio_bitrate: str = "192k"

    @property
    def width(self) -> int:
        return self._size()[0]

    @property
    def height(self) -> int:
        return self._size()[1]

    def _size(self) -> tuple[int, int]:
        w, h = ASPECTS.get(self.aspect, ASPECTS["9:16"])
        if self.draft:  # half size, kept even for H.264
            w, h = w // 4 * 2, h // 4 * 2
        return w, h

    @property
    def frame_size(self) -> tuple[int, int]:
        """Size of the written file: the picture, or with ``letterbox`` the
        picture centred in a 9:16 frame with black above and below."""
        w, h = self._size()
        if self.letterbox and h * 9 < w * 16:  # wider than 9:16
            h = int(round(w * 16 / 9 / 2)) * 2
        return w, h

    @property
    def scale(self) -> float:
        """Multiplier for pixel sizes specified at a 1080 px wide frame."""
        return self.width / BASE_WIDTH


@dataclass
class Style:
    caption: CaptionStyle = field(default_factory=CaptionStyle)
    hook: HookStyle = field(default_factory=HookStyle)
    video: VideoStyle = field(default_factory=VideoStyle)
    look: LookStyle = field(default_factory=LookStyle)


# --------------------------------------------------------------------------- project inputs

@dataclass
class VoiceSettings:
    source: str = "file"                   # file (path or URL) | higgsfield (via Claude) |
                                           # higgsfield-api | piper | none (music only, captions timed to duration)
    file: str | None = None                # path or URL of a finished voiceover
    higgsfield_preset: str = "elevenlabs"
    voice_id: str | None = None
    voice_type: str = "preset"             # Higgsfield: preset (built in) | element (your own voice)
    piper_model: str | None = None
    words_per_second: float = 2.6          # reading pace for source=none
    align: str = "auto"                    # word timing: auto (Whisper if installed) | whisper | estimate


@dataclass
class MusicSettings:
    file: str | None = None
    source_in: float = 0.0                 # use the music from here ...
    source_out: float | None = None        # ... to here (None = end of file)
    start_at: float = 0.0                  # where the music starts in the video, seconds
    end_at: float | None = None            # where it stops (None = end of the video)
    loop: bool = True                      # repeat the selected part to fill the span
    volume_db: float = -16.0               # music level relative to full scale
    fade_in: float = 1.0
    fade_out: float = 1.5
    duck: bool = True                      # lower the music while the voice speaks
    duck_db: float = -8.0                  # how much lower


@dataclass
class FootageItem:
    path: str                              # uploaded file (image or video)
    role: str = "footage"                  # footage (use it in the video) | reference (only steer the stock search)
    kind: str = "auto"                     # auto | image | video
    trim_in: float = 0.0                   # videos: use from here
    trim_out: float | None = None          # videos: ... to here
    seconds: float | None = None           # images: clip length (None = VideoStyle.image_seconds)
    motion: str = "auto"                   # images: auto | zoom-in | zoom-out | pan-left | pan-right | none
    shot: int | None = None                # pin to a shot number (0 = the hook shot)
    note: str = ""                         # free text, e.g. what to look for in stock footage


@dataclass
class FootageSettings:
    items: list[FootageItem] = field(default_factory=list)
    stock: bool = True                     # fill shots without user footage from stock sites
    sources: list[str] = field(default_factory=lambda: ["mixkit", "pexels", "pixabay"])
    palette: str = "bright"                # bright (sunny nature, the reference) | moody (night, rain)
    queries: list[str] | None = None       # override searches, one per shot in order
    hook_query: str | None = "mountains"   # search for the hook shot (needs a skyline)
    reference_matching: bool = True        # rank stock clips by similarity to reference uploads
    allow_landscape: bool = True           # crop landscape clips when vertical ones run out
    picks: list[dict] = field(default_factory=list)  # stock clips chosen in the review, in shot order
    #   (footage.pick_of: provider, id, page_url, download_url, ...; slot "opening" = the hook shot)
    banned: list[str] = field(default_factory=list)  # stock clips never to use ("provider_id" keys)


@dataclass
class DurationSettings:
    target: float | None = None            # seconds; None = as long as the voiceover needs
    min_tempo: float = 0.92                # slowest allowed voice speed change
    max_tempo: float = 1.12                # fastest allowed voice speed change
    max_intro: float = 2.0                 # extra opening (music + picture) when the voice is short
    # Anything still missing after tempo and intro becomes extra ending.


@dataclass
class Project:
    name: str = "untitled"
    preset: str = "reference"
    script: str = ""
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    music: MusicSettings = field(default_factory=MusicSettings)
    footage: FootageSettings = field(default_factory=FootageSettings)
    duration: DurationSettings = field(default_factory=DurationSettings)
    style: Style = field(default_factory=Style)
    auto_emphasis: bool = True


# --------------------------------------------------------------------------- presets

PRESETS: dict[str, dict] = {
    # The dataclass defaults: bright film footage, one white lowercase word
    # at a time, the opening sentence following the skyline.
    "reference": {},
    # The first version of the app: dark rain/night B-roll, bold uppercase
    # Montserrat captions with yellow/red key words, crossfades.
    "moody": {
        "footage": {"palette": "moody", "hook_query": None},
        "style": {
            "caption": {"font": "montserrat", "font_weight": "Black", "font_size": 96, "case": "upper",
                        "emphasis": "color", "stroke_width": 7, "shadow_offset": [0, 8],
                        "shadow_blur": 12.0, "shadow_opacity": 0.6, "max_words": 3,
                        "pop_duration": 0.18, "pop_start_scale": 0.55, "hold": 0.45, "min_duration": 0.3},
            "hook": {"mode": "center"},
            "video": {"min_shot": 2.0, "max_shot": 4.5, "crossfade": 0.35, "fade_in": 0.25,
                      "fade_out": 0.6, "voice_delay": 0.2, "tail": 0.9},
            "look": {"match": "off", "contrast": 1.12, "brightness": -0.05, "saturation": 0.62,
                     "gamma": 0.95, "lift": 0.0, "tint": "rs=-0.04:gs=-0.01:bs=0.05:rh=0.03:bh=-0.03",
                     "vignette": "PI/5", "grain": 7},
        },
    },
    # Reference footage and hook, but punchy 3-word captions with coloured key words.
    "bold": {
        "style": {
            "caption": {"font": "montserrat", "font_weight": "Black", "font_size": 92, "case": "upper",
                        "emphasis": "color", "stroke_width": 7, "max_words": 3,
                        "pop_duration": 0.18, "pop_start_scale": 0.55},
        },
    },
}

PRESET_LABELS = {
    "reference": "Reference (bright film, terrain hook)",
    "moody": "Moody night (dark B-roll, bold captions)",
    "bold": "Bold captions (bright footage, 3-word captions)",
}


# --------------------------------------------------------------------------- (de)serialisation

def to_dict(obj) -> dict:
    """Dataclass -> JSON-safe dict (tuples become lists)."""
    def conv(v):
        if is_dataclass(v):
            return {f.name: conv(getattr(v, f.name)) for f in fields(v)}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, Path):
            return str(v)
        return v
    return conv(obj)


def _parse_color(value) -> RGB:
    if isinstance(value, str):
        h = value.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if len(value) < 3:
        raise ValueError(f"a colour needs red, green and blue values, got {list(value)!r}")
    return tuple(int(x) for x in value)[:3]  # type: ignore[return-value]


def _coerce(tp, value):
    """Convert JSON ``value`` to the annotated type ``tp`` (best effort)."""
    if value is None:
        return None
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        return _coerce(non_none[0], value) if len(non_none) == 1 else value
    if is_dataclass(tp):
        return from_dict(tp, value)
    if origin is list:
        return [_coerce(args[0], v) for v in value] if args else list(value)
    if origin is tuple:
        if tp == RGB or (len(args) == 3 and all(a is int for a in args)):
            return _parse_color(value)
        if args and Ellipsis not in args and len(value) != len(args):
            raise ValueError(f"expected {len(args)} values, got {list(value)!r}")
        return tuple(_coerce(a, v) for a, v in zip(args, value)) if args else tuple(value)
    if tp is float:
        return float(value)
    if tp is int:
        return int(round(float(value)))
    if tp is bool:
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    if tp is str:
        return str(value)
    return value


def from_dict(cls, data: dict):
    """JSON dict -> dataclass ``cls``. Missing keys keep their defaults and
    unknown keys are ignored (logged), so old project files keep loading."""
    hints = typing.get_type_hints(cls)
    kwargs = {}
    names = {f.name for f in fields(cls)}
    for key in data:
        if key not in names:
            log.debug("ignoring unknown setting %s.%s", cls.__name__, key)
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = _coerce(hints[f.name], data[f.name])
        elif f.default is MISSING and f.default_factory is MISSING:  # required field
            raise ValueError(f"{cls.__name__}.{f.name} is required")
    return cls(**kwargs)


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into a copy of ``base`` (lists replace)."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def preset_dict(name: str) -> dict:
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
    return deep_merge(to_dict(Project()), PRESETS[name]) | {"preset": name}


# --------------------------------------------------------------------------- saved defaults

DEFAULT_KEYS = ("preset", "voice", "music", "duration", "footage", "style", "auto_emphasis")


def defaults_path(settings: Settings) -> Path:
    return Path(settings.home_dir) / "defaults.json"


def load_defaults(settings: Settings) -> Project:
    """The user's default project: preset + their saved overrides."""
    saved: dict = {}
    path = defaults_path(settings)
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("ignoring unreadable defaults file %s", path)
    base = preset_dict(saved.get("preset", "reference"))
    return from_dict(Project, deep_merge(base, saved))


def save_defaults(project: Project, settings: Settings) -> Path:
    """Store everything except per-reel content (script, uploaded files)."""
    data = {k: v for k, v in to_dict(project).items() if k in DEFAULT_KEYS}
    data["footage"]["items"] = []
    data["voice"]["file"] = None
    data["music"]["file"] = None
    path = defaults_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def reset_defaults(settings: Settings) -> None:
    defaults_path(settings).unlink(missing_ok=True)


def load_project(path: str | os.PathLike) -> Project:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    base = preset_dict(data.get("preset", "reference"))
    return from_dict(Project, deep_merge(base, data))


def save_project(project: Project, path: str | os.PathLike) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(project), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
