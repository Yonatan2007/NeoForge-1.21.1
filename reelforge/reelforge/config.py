"""Settings (secrets from the environment) and the visual style.

Every aesthetic knob lives in ``CaptionStyle`` / ``VideoStyle`` so the
"look" can be tuned without touching pipeline code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

RGB = tuple[int, int, int]


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
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
        )


@dataclass
class CaptionStyle:
    font_path: str | None = None          # None -> auto-download Montserrat Black
    font_size: int = 96
    uppercase: bool = True
    text_color: RGB = (255, 255, 255)
    highlight_color: RGB = (255, 212, 0)   # emphasis level 1 (yellow)
    alert_color: RGB = (255, 59, 48)       # emphasis level 2 (red)
    stroke_width: int = 7
    stroke_color: RGB = (0, 0, 0)
    shadow_offset: tuple[int, int] = (0, 8)
    shadow_blur: float = 12.0
    shadow_opacity: float = 0.6
    max_words: int = 3                     # 1 = strict word-by-word
    max_chars: int = 18
    max_line_width: int = 920              # px, keeps text inside the IG safe area
    max_lines: int = 2
    line_spacing: float = 1.05
    y_center: float = 0.5                  # 0.5 = dead centre of the frame
    reveal: str = "build"                  # "build" (word pops as spoken) | "phrase"
    pop_duration: float = 0.18
    pop_start_scale: float = 0.55
    lead: float = 0.05                     # show text slightly before it is heard
    hold: float = 0.45                     # linger after the last word of a chunk
    min_duration: float = 0.30
    gap_break: float = 0.35                # a pause this long forces a new caption
    flicker_gap: float = 0.20              # gaps shorter than this are closed


@dataclass
class VideoStyle:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    min_shot: float = 2.0
    max_shot: float = 4.5
    cut_preroll: float = 0.12              # cut slightly before a sentence starts
    crossfade: float = 0.35
    fade_in: float = 0.25
    fade_out: float = 0.6
    voice_delay: float = 0.2
    tail: float = 0.9                      # picture after the last word
    # Moody grade applied while normalising each clip (ffmpeg `eq`, `vignette`, `noise`).
    contrast: float = 1.12
    brightness: float = -0.05
    saturation: float = 0.62
    gamma: float = 0.95
    vignette: str = "PI/5"
    # Cool shadows, warm highlights: the classic "cinematic" split tone.
    tint: str = "rs=-0.04:gs=-0.01:bs=0.05:rh=0.03:bh=-0.03"
    grain: int = 7
    max_clip_seconds: float = 20.0
    music_volume: float = 0.12
    crf: int = 18
    maxrate: str = "10M"                   # ~25 MB per 20 s; IG re-encodes to ~4 Mb/s anyway
    bufsize: str = "20M"
    preset: str = "medium"
    audio_bitrate: str = "192k"


@dataclass
class Style:
    caption: CaptionStyle = field(default_factory=CaptionStyle)
    video: VideoStyle = field(default_factory=VideoStyle)
