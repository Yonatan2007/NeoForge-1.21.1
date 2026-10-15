"""Font resolution shared by captions and the hook renderer.

A font is named by a key ("inter", "montserrat") or a path, plus a weight.
Inter is a variable font: one file, weight chosen with Pillow's variation
API. Montserrat ships one static file per weight. Files are downloaded once
into the cache; system copies and generic bold fonts are fallbacks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .media import download

log = logging.getLogger(__name__)

INTER_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf"
MONTSERRAT_URL = ("https://raw.githubusercontent.com/JulietaUla/Montserrat/master/"
                  "fonts/ttf/Montserrat-{weight}.ttf")
SYSTEM_FILES = {
    "inter": ["/usr/share/fonts/opentype/inter/Inter-{weight}.otf",
              "/Library/Fonts/Inter-{weight}.otf", "C:/Windows/Fonts/Inter-{weight}.ttf"],
}
FALLBACKS = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf")
WEIGHTS = ("Thin", "ExtraLight", "Light", "Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Black")


@dataclass(frozen=True)
class FontSpec:
    path: str
    variation: str | None = None  # named instance of a variable font


def resolve(font: str, weight: str, cache_dir: Path) -> FontSpec:
    """Find a usable file for ``font`` at ``weight`` (downloading if needed)."""
    weight = weight if weight in WEIGHTS else "Bold"
    key = font.lower()
    if key == "inter":
        for pattern in SYSTEM_FILES["inter"]:
            p = Path(pattern.format(weight=weight))
            if p.is_file():
                return FontSpec(str(p))
        spec = _download(INTER_URL, cache_dir / "fonts" / "Inter-Variable.ttf", variation=weight)
        if spec:
            return spec
    elif key == "montserrat":
        spec = _download(MONTSERRAT_URL.format(weight=weight),
                         cache_dir / "fonts" / f"Montserrat-{weight}.ttf")
        if spec:
            return spec
    elif Path(font).expanduser().is_file():
        return FontSpec(str(Path(font).expanduser()))
    else:
        log.warning("unknown font %r", font)
    for name in FALLBACKS:
        try:
            ImageFont.truetype(name, 10)
            log.warning("using fallback font %s", name)
            return FontSpec(name)
        except OSError:
            continue
    raise RuntimeError("no usable font found; set the font to a .ttf/.otf path")


def _download(url: str, target: Path, variation: str | None = None) -> FontSpec | None:
    try:
        download(url, target, timeout=30)
        spec = FontSpec(str(target), variation)
        load(spec, 10)
        return spec
    except Exception as exc:  # offline, blocked, corrupt download
        log.warning("could not fetch %s (%s)", url, exc)
        target.unlink(missing_ok=True)
        return None


@lru_cache(maxsize=256)
def load(spec: FontSpec, size: int) -> ImageFont.FreeTypeFont:
    """A Pillow font at ``size`` px (cached per spec and size)."""
    font = ImageFont.truetype(spec.path, max(1, int(size)))
    if spec.variation:
        try:
            font.set_variation_by_name(spec.variation)
        except (OSError, ValueError) as exc:
            log.warning("font %s has no %s instance (%s)", spec.path, spec.variation, exc)
    return font


def apply_case(text: str, case: str) -> str:
    return text.lower() if case == "lower" else text.upper() if case == "upper" else text
