"""Colour look: make every clip - stock, uploaded, phone video - share one film look.

Stock clips arrive with wildly different grades (flat log-like, punchy, cool,
warm). A fixed ffmpeg grade shifts all of them by the same amount, so they stay
inconsistent next to each other. Instead each clip is first moved towards a
*target colour distribution* - the mean and spread of every CIE L*a*b* channel,
the classic Reinhard et al. (2001) colour transfer - baked into a small 3D LUT
that ffmpeg applies with ``lut3d``. The shared creative finish (eq, film lift,
split tint, vignette, grain) then goes on top of the matched clip.

The default target is ``REFERENCE_STATS``, measured from the reference reel
(warm vintage film: teal sky, yellow-greens, lifted blacks, soft contrast).
With ``LookStyle.match == "uploads"`` the target is measured from the user's
own reference pictures/videos instead.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import LookStyle, VideoStyle
from .media import MediaError, probe, run

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- sRGB <-> CIE L*a*b* (D65)

# Linear sRGB -> XYZ for the D65 white point (IEC 61966-2-1).
_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                     [0.2126729, 0.7151522, 0.0721750],
                     [0.0193339, 0.1191920, 0.9503041]])
_XYZ2RGB = np.linalg.inv(_RGB2XYZ)
_WHITE = np.array([0.95047, 1.0, 1.08883])  # D65 reference white (Xn, Yn, Zn)
_EPS = (6 / 29) ** 3


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in 0..1 (any shape ``(..., 3)``) -> L* (0..100), a*, b*."""
    xyz = _srgb_to_linear(np.asarray(rgb, dtype=np.float64)) @ _RGB2XYZ.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """L*a*b* (``(..., 3)``) -> sRGB in 0..1; out-of-gamut colours are clipped."""
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], axis=-1)
    xyz = np.where(f > 6 / 29, f ** 3, 3 * (6 / 29) ** 2 * (f - 4 / 29)) * _WHITE
    return _linear_to_srgb(xyz @ _XYZ2RGB.T)


# --------------------------------------------------------------------------- statistics

@dataclass(frozen=True)
class LabStats:
    """CIE L*a*b* (D65) statistics of an image or clip: per-channel mean and
    *within-frame* standard deviation (the spread a single shot has)."""
    mean: tuple[float, float, float]
    std: tuple[float, float, float]


# The reference reel's footage (`ref/reel.mp4`, 720x720, 56.6 s, 11 shots).
# Measured by decoding 4 frames/s at 360x360 (226 frames, fade-to-black frames
# skipped), converting to L*a*b* with `rgb_to_lab`, and excluding caption glyphs:
# during the hook shot (t < 4.3 s) thin near-black low-chroma strokes, afterwards
# thin near-white low-chroma strokes in the centre band (y 0.36..0.64) - "thin"
# meaning removed by a 9-11 px morphological opening - each dilated by 9 px to
# cover anti-aliasing and the soft drop shadow (2.8% of pixels masked on
# average). Per-frame stats were pooled per shot, then the 11 shots pooled with
# equal weight (`combine`), so long shots don't dominate the look.
# Character: mid-bright (L 58) with soft contrast, green-cyan a* and yellow b*:
# blue skies land on teal, greens on warm yellow-green.
REFERENCE_STATS = LabStats(mean=(57.95, -12.66, 20.43), std=(18.96, 9.92, 18.62))

_STAT_SIDE = 192            # frames are measured at this size; statistics barely change with scale


def image_stats(rgb: np.ndarray) -> LabStats:
    """L*a*b* mean and spread of an ``HxWx3`` uint8 RGB image (grey or RGBA is
    accepted too). Large images are subsampled: the statistics don't need
    every pixel."""
    arr = np.asarray(rgb)
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=2)
    arr = arr[..., :3]
    step = max(1, int(math.sqrt(arr.shape[0] * arr.shape[1] / 250_000)))
    lab = rgb_to_lab(arr[::step, ::step].reshape(-1, 3) / 255.0)
    return LabStats(tuple(float(x) for x in lab.mean(0)), tuple(float(x) for x in lab.std(0)))


def combine(stats: list[LabStats]) -> LabStats:
    """Pool several images/clips into one look: the average mean and the pooled
    within-image spread (root mean variance). Differences *between* the inputs
    are deliberately left out - every clip is matched on its own, so the target
    spread must be that of a single shot."""
    if not stats:
        raise ValueError("combine() needs at least one LabStats")
    means = np.array([s.mean for s in stats], dtype=np.float64)
    variances = np.array([s.std for s in stats], dtype=np.float64) ** 2
    return LabStats(tuple(float(x) for x in means.mean(0)),
                    tuple(float(x) for x in np.sqrt(variances.mean(0))))


def _read_image(path: Path) -> np.ndarray | None:
    """RGB pixels of a still image (EXIF rotation applied), or None for anything
    Pillow can't open - videos, but also e.g. HEIC that ffmpeg may still read."""
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((_STAT_SIDE * 2, _STAT_SIDE * 2))
            return np.asarray(im.convert("RGB"))
    except (UnidentifiedImageError, OSError):
        return None


def _video_frame(path: Path, t: float) -> np.ndarray | None:
    """One decoded frame at ``t`` seconds, resized to a small square (the
    aspect ratio is irrelevant for colour statistics); None past the end."""
    s = _STAT_SIDE
    try:
        raw = run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
                   "-frames:v", "1", "-vf", f"scale={s}:{s}:flags=area",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    except MediaError:
        return None
    if len(raw) < s * s * 3:
        return None
    return np.frombuffer(raw[: s * s * 3], np.uint8).reshape(s, s, 3)


def _video_duration(path: Path) -> float | None:
    try:
        value = probe(path).get("format", {}).get("duration")
        return float(value) if value not in (None, "N/A") else None
    except (MediaError, ValueError):
        return None


@functools.lru_cache(maxsize=256)
def _media_stats(path: str, size: int, mtime_ns: int, samples: int,
                 start: float, end: float | None) -> LabStats:
    del size, mtime_ns  # part of the cache key only: a replaced file is re-measured
    pixels = _read_image(Path(path))
    if pixels is not None:
        return image_stats(pixels)
    total = _video_duration(Path(path))
    lo = max(0.0, start)
    hi = min(end, total) if end is not None and total is not None else (end if end is not None else total)
    if hi is None or hi <= lo:  # unknown length (a single-frame file) or a span past the end
        times = [lo if total is None or lo < total else 0.0]
    else:  # centres of `samples` equal slices: skips black first/last frames of fades
        times = [lo + (hi - lo) * (i + 0.5) / samples for i in range(max(1, samples))]
    frames = [f for f in (_video_frame(Path(path), t) for t in times) if f is not None]
    if not frames:
        raise MediaError(f"could not decode any frame of {path}")
    return combine([image_stats(f) for f in frames])


def media_stats(path: Path, samples: int = 8, *, start: float = 0.0,
                end: float | None = None) -> LabStats:
    """Colour statistics of an image, or of a video sampled at ``samples``
    evenly spaced frames (only within ``start``..``end`` seconds when given, so
    a trimmed clip is matched on the part that is actually used). Results are
    memoised per file version."""
    p = Path(path).resolve()
    st = p.stat()
    return _media_stats(str(p), st.st_size, st.st_mtime_ns, int(samples),
                        round(float(start), 3), None if end is None else round(float(end), 3))


# --------------------------------------------------------------------------- statistical match -> 3D LUT

STD_RATIO = (0.6, 1.6)          # contrast/saturation change allowed per channel
MAX_SHIFT = (15.0, 20.0, 20.0)  # largest L*, a*, b* mean shift: a night clip is brightened, not flattened grey
_KNEE = 88.0                    # L* above this rolls off smoothly instead of clipping to white


def _shoulder(L: np.ndarray) -> np.ndarray:
    """Soft highlight roll-off: identity below the knee, then an asymptotic
    approach to L*=100 so stretched highlights keep detail (film-like)."""
    room = 100.0 - _KNEE
    return np.where(L > _KNEE, _KNEE + room * np.tanh((L - _KNEE) / room), L)


def transfer(lab: np.ndarray, source: LabStats, target: LabStats, strength: float = 1.0) -> np.ndarray:
    """Reinhard colour transfer of L*a*b* values from ``source`` statistics
    towards ``target``: ``(x - mu_s) * sigma_t / sigma_s + mu_t`` per channel,
    with the spread ratio and the mean shift clamped so extreme clips (night,
    fog, a single colour) are moved towards the look without breaking, then
    blended with the original by ``strength`` (0..1)."""
    mu_s, mu_t = np.array(source.mean), np.array(target.mean)
    ratio = np.clip(np.array(target.std) / np.maximum(np.array(source.std), 1e-3), *STD_RATIO)
    shift = np.clip(mu_t - mu_s, -np.array(MAX_SHIFT), np.array(MAX_SHIFT))
    out = (lab - mu_s) * ratio + mu_s + shift
    out[..., 0] = _shoulder(out[..., 0])
    s = min(1.0, max(0.0, float(strength)))
    return lab + s * (out - lab)


def match_lut(source: LabStats, target: LabStats, strength: float, path: Path, size: int = 33) -> Path:
    """Write the ``transfer`` from ``source`` to ``target`` as an Adobe/Resolve
    ``.cube`` 3D LUT (red index fastest) for ffmpeg's ``lut3d`` filter."""
    path = Path(path)
    grid = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")  # last axis (red) varies fastest
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    graded = lab_to_rgb(transfer(rgb_to_lab(rgb), source, target, strength))
    lines = ['TITLE "reelforge look match"', f"LUT_3D_SIZE {size}",
             "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in graded]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)  # atomic: a parallel render never reads half a LUT
    return path


# --------------------------------------------------------------------------- ffmpeg grade

def _filter_path(path: Path) -> str:
    """Escape a file path for an ffmpeg filter option. ffmpeg unescapes twice:
    once for the filtergraph (``\\ ' [ ] , ;``), then for the option list
    (``\\ ' :``). So the path is single-quoted for the option level and that
    is backslash-escaped for the graph level; spaces, commas, colons
    (Windows drives) and apostrophes in cache paths are then safe."""
    quoted = "'" + str(path).replace("\\", "/").replace("'", "'\\''") + "'"
    return re.sub(r"([\\'\[\],;])", r"\\\1", quoted)


def grade_filter(look: LookStyle, vs: VideoStyle, lut: Path | None = None) -> str:
    """The complete ffmpeg ``-vf`` chain for one clip: cover-scale and
    centre-crop to the output frame, conform the frame rate, then the colour
    work - statistical match (``lut``), eq, film lift, split tint, vignette and
    grain - ending in yuv420p for H.264."""
    w, h = vs.width, vs.height
    chain = [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos",
             f"crop={w}:{h}", "setsar=1", f"fps={vs.fps}"]
    if lut is not None:
        chain.append(f"lut3d=file={_filter_path(lut)}:interp=tetrahedral")
    eq = (look.contrast, look.brightness, look.saturation, look.gamma)
    if eq != (1.0, 0.0, 1.0, 1.0):
        chain.append("eq=contrast={:g}:brightness={:g}:saturation={:g}:gamma={:g}".format(*eq))
    lift = min(0.3, max(0.0, look.lift))
    if lift > 0:
        # Faded film: blacks raised to `lift`, whites eased down a little.
        chain.append(f"curves=all='0/{lift:.3f} 1/{1 - lift / 4:.3f}'")
    if look.tint.strip():
        chain.append(f"colorbalance={look.tint.strip()}")
    if look.vignette.strip():
        chain.append(f"vignette=angle={look.vignette.strip()}")
    if look.grain > 0:
        chain.append(f"noise=c0s={min(40, int(look.grain))}:c0f=t")
    chain.append("format=yuv420p")
    return ",".join(chain)
