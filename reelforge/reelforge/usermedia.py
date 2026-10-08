"""The user's own media: classify uploads, make thumbnails, turn photos into
gently moving clips, and describe pictures well enough to steer the stock search.

* ``media_kind`` / ``media_info`` / ``thumbnail`` - what the upload is, its size
  and length, and a small JPEG for the UI.
* ``image_to_video`` - a Ken Burns clip from a still. Every frame is resampled
  from a float crop box (Pillow's ``resize(box=...)``), so the camera moves by
  fractions of a pixel per frame; the integer crops of ffmpeg's ``zoompan`` are
  what makes the classic slideshow jitter.
* ``signature`` / ``similarity`` / ``reference_score`` - a compact descriptor
  of palette, brightness and layout, so stock clips that look like the user's
  reference pictures rank higher.
* ``palette_terms`` - the same descriptors turned into stock search words
  ("golden hour", "lake", "snow mountains" ...).
"""
from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import math
import os
import re
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .look import rgb_to_lab
from .media import MediaError, probe, run

log = logging.getLogger(__name__)

try:  # iPhone photos: HEIC support when the optional pillow-heif package is installed
    from pillow_heif import register_heif_opener
except ImportError:
    pass
else:
    register_heif_opener()

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
              ".heic", ".heif", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".3gp",
              ".wmv", ".flv", ".ts", ".mts", ".m2ts"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma",
              ".aif", ".aiff"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".text"}
MOTIONS = ("auto", "zoom-in", "zoom-out", "pan-left", "pan-right", "none")


# --------------------------------------------------------------------------- what is this file?

def media_kind(path: str | os.PathLike) -> str:
    """"image" | "video" | "audio" | "text" | "unknown", from the extension
    (cheap), or by sniffing the content when the extension is unfamiliar.
    Animated GIFs count as video: ffmpeg plays them like any clip."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".gif":
        return "video" if _gif_frames(p) > 1 else "image"
    for kind, exts in (("image", IMAGE_EXTS), ("video", VIDEO_EXTS),
                       ("audio", AUDIO_EXTS), ("text", TEXT_EXTS)):
        if ext in exts:
            return kind
    return _sniff(p)


def _gif_frames(path: Path) -> int:
    try:
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1)
    except (UnidentifiedImageError, OSError):
        return 1


def _sniff(path: Path) -> str:
    try:
        with Image.open(path) as im:
            im.verify()
        return "image"
    except (UnidentifiedImageError, OSError, SyntaxError):
        pass
    streams = _streams(path)
    if any(_is_picture(s) for s in streams):
        return "video"
    if any(s.get("codec_type") == "audio" for s in streams):
        return "audio"
    return "unknown"


def _streams(path: Path) -> list[dict]:
    try:
        return probe(path).get("streams", [])
    except (MediaError, ValueError):
        return []


def _is_picture(stream: dict) -> bool:
    """A real video stream - not the cover art an MP3 or M4A carries."""
    return (stream.get("codec_type") == "video"
            and not (stream.get("disposition") or {}).get("attached_pic"))


def media_info(path: str | os.PathLike) -> dict:
    """``{"kind", "duration", "width", "height"}`` - display size (EXIF / video
    rotation applied), duration in seconds for audio/video, None where it
    doesn't apply. Files that can't be read report kind "unknown" instead of
    raising, so an upload screen can say so; a missing file raises."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    kind = media_kind(p)
    info = {"kind": kind, "duration": None, "width": None, "height": None}
    if kind == "image":
        try:
            info["width"], info["height"] = _image_size(p)
        except MediaError:
            info["kind"] = "unknown"
    elif kind in ("video", "audio"):
        try:
            data = probe(p)
        except (MediaError, ValueError):
            return info | {"kind": "unknown"}
        streams = data.get("streams", [])
        video = next((s for s in streams if _is_picture(s)), None)
        if video is not None:
            info["kind"] = "video"
            info["width"], info["height"] = _display_size(video)
        elif any(s.get("codec_type") == "audio" for s in streams):
            info["kind"] = "audio"  # e.g. an .mp4 or .webm holding only sound
        else:
            return info | {"kind": "unknown"}
        info["duration"] = _duration(data, video)
    return info


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as im:
            w, h = im.size
            return (h, w) if _exif_orientation(im) in (5, 6, 7, 8) else (w, h)
    except (UnidentifiedImageError, OSError):
        return _ffmpeg_still(path).size


def _exif_orientation(im: Image.Image) -> int:
    try:
        return int(im.getexif().get(0x0112, 1))
    except (OSError, ValueError, TypeError):
        return 1


def _display_size(stream: dict) -> tuple[int, int]:
    """Width/height as the clip is shown: phones store portrait video as
    landscape plus a rotation, and some cameras use non-square pixels."""
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    sar = re.fullmatch(r"(\d+):(\d+)", stream.get("sample_aspect_ratio") or "")
    if sar and int(sar[1]) > 0 and int(sar[2]) > 0 and sar[1] != sar[2]:
        w = round(w * int(sar[1]) / int(sar[2]))
    rotation = stream.get("tags", {}).get("rotate")
    for side in stream.get("side_data_list") or []:
        rotation = side.get("rotation", rotation)
    try:
        if round(float(rotation or 0)) % 180 == 90:
            w, h = h, w
    except ValueError:
        pass
    return w, h


def _duration(data: dict, video: dict | None) -> float | None:
    for value in (data.get("format", {}).get("duration"), (video or {}).get("duration")):
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return None


# --------------------------------------------------------------------------- decoding pictures

def load_image(path: str | os.PathLike, cover: tuple[int, int] | None = None) -> Image.Image:
    """A still upright (EXIF orientation applied) and flattened to RGB.

    ``cover`` (width, height): the picture will only be shown cover-fitted to
    that size, so the JPEG decoder may skip resolution that would be thrown
    away - a 48 MP phone photo decodes several times faster. Formats Pillow
    can't read (e.g. HEIC without pillow-heif) are tried through ffmpeg before
    giving up with ``MediaError``."""
    p = Path(path)
    try:
        with Image.open(p) as im:
            if cover:
                upright = im.size[::-1] if _exif_orientation(im) in (5, 6, 7, 8) else im.size
                k = max(cover[0] / upright[0], cover[1] / upright[1])
                if k < 1.0:
                    im.draft(im.mode, (math.ceil(im.width * k), math.ceil(im.height * k)))
            return _flatten(ImageOps.exif_transpose(im))
    except (UnidentifiedImageError, OSError) as exc:
        log.debug("Pillow can't read %s (%s); trying ffmpeg", p, exc)
    return _flatten(_ffmpeg_still(p))


def _ffmpeg_still(path: Path, t: float = 0.0, max_side: int | None = None) -> Image.Image:
    """First frame at ``t`` decoded by ffmpeg (auto-rotated), as a PIL image."""
    vf = (["-vf", f"scale='min({max_side},iw)':'min({max_side},ih)'"
                  ":force_original_aspect_ratio=decrease"] if max_side else [])
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
           "-frames:v", "1", *vf, "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        data = run(cmd).stdout
        if not data:
            raise MediaError(f"no frame at {t:.2f} s")
        return Image.open(io.BytesIO(data))
    except (MediaError, UnidentifiedImageError, OSError) as exc:
        raise MediaError(f"can't decode a picture from {path.name}: {exc}") from exc


def _flatten(im: Image.Image) -> Image.Image:
    """RGB, whatever came in: palette, greyscale, CMYK, 16-bit, or transparency.
    Transparent areas get a backdrop that contrasts with the visible content
    (white behind a dark logo, black behind a light cut-out)."""
    if im.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
        arr = np.asarray(im, dtype=np.float32)
        top = 65535.0 if arr.max() > 255 else 255.0
        im = Image.fromarray(np.clip(arr / top * 255.0 + 0.5, 0, 255).astype(np.uint8))
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if not has_alpha:
        return im.convert("RGB")
    rgba = im.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255.0
    if alpha.min() >= 1.0:
        return rgba.convert("RGB")
    luma = np.asarray(rgba.convert("L"), dtype=np.float32) / 255.0
    visible = float((luma * alpha).sum() / max(alpha.sum(), 1e-6))
    backdrop = Image.new("RGB", rgba.size, (255, 255, 255) if visible < 0.35 else (0, 0, 0))
    backdrop.paste(rgba, mask=rgba.getchannel("A"))
    return backdrop


def _video_frame(path: Path, t: float, max_side: int) -> Image.Image | None:
    """A frame at ``t`` seconds no larger than ``max_side``; None past the end."""
    try:
        return _ffmpeg_still(path, t, max_side).convert("RGB")
    except MediaError:
        return None


def thumbnail(path: str | os.PathLike, dst: Path, max_side: int = 480,
              t: float | None = None) -> Path:
    """Write a JPEG preview (longest side <= ``max_side``) of an image or a
    video frame at ``t`` seconds (default: a third in, at most 2 s, which skips
    fade-ins). Audio and text have no picture: ``MediaError``."""
    p, dst = Path(path), Path(dst)
    kind = media_kind(p)
    if kind == "image":
        im = load_image(p, (max_side, max_side))
    elif kind == "video":
        if t is None:
            length = _duration(_probe_or_empty(p), None) or 0.0
            t = min(2.0, length / 3)
        im = _video_frame(p, t, max_side) or _video_frame(p, 0.0, max_side)
        if im is None:
            raise MediaError(f"can't decode a frame of {p.name}")
    else:
        raise MediaError(f"{p.name} is {kind}, not a picture or video")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "JPEG", quality=85, optimize=True)
    return dst


def _probe_or_empty(path: Path) -> dict:
    try:
        return probe(path)
    except (MediaError, ValueError):
        return {}


# --------------------------------------------------------------------------- Ken Burns

# Gentle, constant-speed moves (the reference reel's camera barely drifts):
# zoom grows ~2 %/s (1.00 -> 1.08 over a 4 s clip), pans travel ~1.5 % of the
# visible width per second; both clamped so very short or long clips stay sane.
ZOOM_PER_SECOND, ZOOM_RANGE = 0.02, (0.03, 0.12)
PAN_PER_SECOND, PAN_RANGE = 0.015, (0.03, 0.10)
OVERSAMPLE = 1.25    # working image resolution vs output: every frame is a real downscale
WIDER = 1.25         # "auto" pans when the photo is this much wider than the frame
_KB_VERSION = 1      # bump when the rendering changes, to invalidate cached clips


def image_to_video(path: Path, dst_dir: Path, seconds: float, motion: str,
                   width: int, height: int, fps: int) -> Path:
    """An H.264 clip (no audio) of ``seconds`` showing the photo cover-fitted to
    ``width`` x ``height`` (cropped, never letterboxed) with a slow Ken Burns
    move: ``zoom-in``/``zoom-out`` about the centre, ``pan-left``/``pan-right``
    (the view travels towards that side of the picture), ``none``, or ``auto``
    (pan along the extra width of a wide photo, otherwise zoom). Ungraded -
    ``look.prepare_clip`` grades it like any other clip. Cached by a hash of the
    file version and every parameter."""
    path, dst_dir = Path(path), Path(dst_dir)
    if motion not in MOTIONS:
        raise ValueError(f"unknown motion {motion!r}; choose from {', '.join(MOTIONS)}")
    if seconds <= 0 or fps <= 0:
        raise ValueError("seconds and fps must be positive")
    if width < 2 or height < 2 or width % 2 or height % 2:
        raise ValueError(f"output size must be even (H.264 4:2:0), got {width}x{height}")
    st = path.stat()
    key = json.dumps([str(path.resolve()), st.st_size, st.st_mtime_ns, round(seconds, 3),
                      motion, width, height, fps, _KB_VERSION])
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem)[:32].strip("-") or "image"
    dst = dst_dir / f"{stem}_{digest}.mp4"
    if dst.is_file() and dst.stat().st_size > 0:
        return dst

    picture = _working_image(path, width, height)
    if motion == "auto":
        motion = _auto_motion(path, picture.size, (width, height))
    frames = max(1, round(seconds * fps))
    boxes = [_crop_box(picture.size, (width, height), motion, seconds, i / max(1, frames - 1))
             for i in range(frames)]
    dst_dir.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    _encode(picture, boxes, (width, height), fps, part)
    part.replace(dst)
    return dst


def _working_image(path: Path, width: int, height: int) -> Image.Image:
    """The photo at ``OVERSAMPLE`` x the resolution its cover-fit needs, so each
    frame is a real (anti-aliased) downscale. Never upscaled: a small photo is
    resampled from its own pixels every frame."""
    cover = (OVERSAMPLE * width, OVERSAMPLE * height)
    im = load_image(path, cover)
    k = max(cover[0] / im.width, cover[1] / im.height)
    if k < 1.0:
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))),
                       Image.LANCZOS, reducing_gap=3.0)
    return im


def _auto_motion(path: Path, src: tuple[int, int], out: tuple[int, int]) -> str:
    """Pan along the spare width of a wide photo (showing more of it),
    otherwise zoom. The direction is a stable function of the file name, so a
    run of photos doesn't all move the same way."""
    flip = int(hashlib.sha1(path.name.encode()).hexdigest(), 16) % 2
    if (src[0] / src[1]) / (out[0] / out[1]) >= WIDER:
        return ("pan-right", "pan-left")[flip]
    return ("zoom-in", "zoom-out")[flip]


def _crop_box(src: tuple[int, int], out: tuple[int, int], motion: str, seconds: float,
              progress: float) -> tuple[float, float, float, float]:
    """The float source rectangle shown at ``progress`` (0..1) of the clip.

    Zoom is geometric (equal ratio per frame), which the eye reads as constant
    speed; a pan uses just enough zoom to have room to travel."""
    iw, ih = src
    w, h = out
    cover = max(w / iw, h / ih)          # output px per source px when the photo just fills the frame
    zoom, shift = 1.0, 0.0               # shift: horizontal offset in visible widths
    if motion in ("zoom-in", "zoom-out"):
        amount = min(max(ZOOM_PER_SECOND * seconds, ZOOM_RANGE[0]), ZOOM_RANGE[1])
        zoom = (1.0 + amount) ** (progress if motion == "zoom-in" else 1.0 - progress)
    elif motion in ("pan-left", "pan-right"):
        travel = min(max(PAN_PER_SECOND * seconds, PAN_RANGE[0]), PAN_RANGE[1])
        zoom = max(1.0, (1.0 + travel) * w / (cover * iw))
        shift = (progress - 0.5) * travel * (1.0 if motion == "pan-right" else -1.0)
    bw, bh = w / (cover * zoom), h / (cover * zoom)
    x0 = min(max(iw / 2 + (shift - 0.5) * bw, 0.0), iw - bw)
    y0 = min(max((ih - bh) / 2, 0.0), ih - bh)
    return (x0, y0, min(float(iw), x0 + bw), min(float(ih), y0 + bh))


def _encode(picture: Image.Image, boxes: list[tuple[float, float, float, float]],
            size: tuple[int, int], fps: int, dst: Path) -> None:
    """Resample every frame (in parallel threads - Pillow releases the GIL)
    and pipe the raw RGB to x264. Only a few frames are in flight at a time,
    so memory stays flat however long the clip is."""
    w, h = size
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-framerate", str(fps), "-i", "-", "-an",
           "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "10",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-movflags", "+faststart", "-f", "mp4", str(dst)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)

    def render(box: tuple[float, float, float, float]) -> bytes:
        return picture.resize(size, Image.BICUBIC, box=box).tobytes()

    workers = max(1, min(4, os.cpu_count() or 1))
    try:
        with ThreadPoolExecutor(workers) as pool:
            pending: deque = deque()
            last = None
            for box in boxes:
                # a static shot ("none") renders its single frame once
                last = last if last is not None and box == last[0] else (box, pool.submit(render, box))
                pending.append(last[1])
                if len(pending) > 2 * workers:
                    proc.stdin.write(pending.popleft().result())
            while pending:
                proc.stdin.write(pending.popleft().result())
        proc.stdin.close()
    except BrokenPipeError:
        pass  # ffmpeg died; its error message is reported below
    except BaseException:
        proc.kill()
        proc.wait()
        dst.unlink(missing_ok=True)
        raise
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        dst.unlink(missing_ok=True)
        raise MediaError(f"encoding the photo clip failed:\n{err[-2000:]}")


# --------------------------------------------------------------------------- visual signature

# Layout of a signature vector (float32):
#   colour-name histogram (sums to 1): 4 achromatic lightness bins (black, dark
#   grey, light grey, white) + chromatic 2 lightness x 2 chroma (muted, vivid)
#   x 12 Lab hue sectors (30 deg each), every pixel softly split between
#   neighbouring bins so small colour shifts don't flip bins;
#   L*a*b* mean and std (6); a 3x3 grid of mean L*a*b* (27) for the layout
#   (bright sky on top, green below ...); edge density of the top, middle and
#   bottom thirds (3) for texture (foliage vs still water vs studio wall).
SIG_SIDE = 128                    # pictures are described at this size
SIG_FRAMES = 5                    # frames sampled from a video
_HUES = 12
_ACH = slice(0, 4)
_CHROM = slice(4, 4 + 2 * 2 * _HUES)
_HIST = slice(0, _CHROM.stop)
_STATS = slice(_HIST.stop, _HIST.stop + 6)
_GRID = slice(_STATS.stop, _STATS.stop + 27)
_EDGES = slice(_GRID.stop, _GRID.stop + 3)
SIGNATURE_SIZE = _EDGES.stop
EDGE_STEP = 6.0                   # L* difference between neighbouring pixels that counts as an edge


def signature(src) -> np.ndarray:
    """Compact visual descriptor (``SIGNATURE_SIZE`` floats) of an image or
    video file (``SIG_FRAMES`` frames averaged), a PIL image or an RGB array.
    File results are memoised per file version."""
    if isinstance(src, Image.Image):
        return _frame_signature(_flatten(src))
    if isinstance(src, np.ndarray):
        return _frame_signature(_flatten(Image.fromarray(np.asarray(src, dtype=np.uint8))))
    p = Path(src).resolve()
    st = p.stat()
    return _file_signature(str(p), st.st_size, st.st_mtime_ns).copy()


@functools.lru_cache(maxsize=256)
def _file_signature(path: str, size: int, mtime_ns: int) -> np.ndarray:
    del size, mtime_ns  # cache key only: a replaced file is described again
    p = Path(path)
    if media_kind(p) != "video":
        return _frame_signature(load_image(p, (SIG_SIDE, SIG_SIDE)))
    length = _duration(_probe_or_empty(p), None)
    times = [length * (i + 0.5) / SIG_FRAMES for i in range(SIG_FRAMES)] if length else [0.0]
    frames = [f for f in (_video_frame(p, t, SIG_SIDE) for t in times) if f is not None]
    if not frames:
        raise MediaError(f"can't decode a frame of {p.name}")
    return np.mean([_frame_signature(f) for f in frames], axis=0).astype(np.float32)


def _frame_signature(im: Image.Image) -> np.ndarray:
    scale = SIG_SIDE / max(im.size)
    im = im.resize((max(6, round(im.width * scale)), max(6, round(im.height * scale))),
                   Image.BILINEAR, reducing_gap=2.0)
    lab = rgb_to_lab(np.asarray(im, dtype=np.float64) / 255.0)
    flat = lab.reshape(-1, 3)
    grid = [cell.reshape(-1, 3).mean(axis=0)
            for band in np.array_split(lab, 3, axis=0) for cell in np.array_split(band, 3, axis=1)]
    return np.concatenate([_colour_histogram(flat), flat.mean(axis=0), flat.std(axis=0),
                           np.ravel(grid), _edge_density(lab[..., 0])]).astype(np.float32)


def _soft_bins(pos: np.ndarray, n: int, circular: bool = False) -> np.ndarray:
    """(N, n) weights splitting each position (in bin units, bin k centred on
    k) linearly between its two nearest bins."""
    pos = pos if circular else np.clip(pos, 0.0, n - 1.0)
    lo = np.floor(pos)
    frac = pos - lo
    lo = lo.astype(int)
    hi = lo + 1
    lo, hi = (lo % n, hi % n) if circular else (lo, np.minimum(hi, n - 1))
    out = np.zeros((pos.size, n))
    rows = np.arange(pos.size)
    np.add.at(out, (rows, lo), 1.0 - frac)
    np.add.at(out, (rows, hi), frac)
    return out


def _colour_histogram(lab: np.ndarray) -> np.ndarray:
    lightness, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    chroma = np.hypot(a, b)
    coloured = np.clip((chroma - 6.0) / 12.0, 0.0, 1.0)      # grey below C* 6, colour above 18
    vivid = np.clip((chroma - 18.0) / 20.0, 0.0, 1.0)        # muted ~C* 20, vivid from ~C* 38
    bright = np.clip((lightness - 35.0) / 30.0, 0.0, 1.0)    # dark below L* 35, bright above 65
    grey = _soft_bins((lightness - 12.5) / 25.0, 4) * (1.0 - coloured)[:, None]
    hue = _soft_bins(np.degrees(np.arctan2(b, a)) % 360.0 / 30.0, _HUES, circular=True)
    parts = [hue * (coloured * lv * cv)[:, None]
             for lv in (1.0 - bright, bright) for cv in (1.0 - vivid, vivid)]
    hist = np.concatenate([grey.sum(axis=0)] + [p.sum(axis=0) for p in parts])
    return hist / max(len(lab), 1)


def _edge_density(lightness: np.ndarray) -> np.ndarray:
    gx = np.abs(np.diff(lightness, axis=1))[:-1, :]
    gy = np.abs(np.diff(lightness, axis=0))[:, :-1]
    edges = np.hypot(gx, gy) > EDGE_STEP
    return np.array([band.mean() for band in np.array_split(edges, 3, axis=0)])


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """0..1: how alike two signatures look - palette (histogram overlap),
    layout (per-cell colour difference), overall tone and texture."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if a.shape != (SIGNATURE_SIZE,) or b.shape != (SIGNATURE_SIZE,):
        raise ValueError(f"signatures must have {SIGNATURE_SIZE} values")
    palette = float(np.sqrt(np.clip(a[_HIST], 0, None) * np.clip(b[_HIST], 0, None)).sum())
    layout = math.exp(-float(np.linalg.norm((a[_GRID] - b[_GRID]).reshape(9, 3), axis=1).mean()) / 25.0)
    sa, sb = a[_STATS], b[_STATS]
    tone = math.exp(-(float(np.linalg.norm(sa[:3] - sb[:3]))
                      + 0.5 * float(np.linalg.norm(sa[3:] - sb[3:]))) / 20.0)
    texture = math.exp(-float(np.abs(a[_EDGES] - b[_EDGES]).mean()) / 0.15)
    return min(1.0, max(0.0, 0.35 * palette + 0.3 * layout + 0.2 * tone + 0.15 * texture))


def reference_score(thumb: Image.Image | None, refs: list[np.ndarray]) -> float:
    """How well a stock thumbnail matches the user's reference pictures: the best
    similarity to any of them (0 without a thumbnail or references)."""
    if thumb is None or not refs:
        return 0.0
    sig = signature(thumb)
    return max(similarity(sig, r) for r in refs)
