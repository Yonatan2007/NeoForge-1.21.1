"""The hook: the opening sentence laid along the skyline of the first shot.

The reference reel opens on a mountain range with the first sentence written
along the ridge: words appear one by one as they are spoken, sit just above
the rock outline, tilt with the slope, single letters fan out around sharp
peaks, the first and stressed words are big, function words small, and the
last word drops large under the end of the line ("are?").

Two halves:

* ``find_contour`` - where is the skyline? A per-column sky/terrain boundary
  found by dynamic programming over a sky-likelihood map (colour, brightness
  and texture compared with a sky model seeded from the top rows, refined
  twice) plus the vertical colour gradient. It reports a confidence; weak
  skylines fall back to the dominant straight edge, then to a fixed pattern,
  so every frame gets a usable path.
* ``HookRenderer`` - lays the words along an offset of that contour (valleys
  rounded, slopes capped, kept inside the safe area, collision-free, wrapped
  onto parallel lines above the ridge when the sentence is long), renders each
  word once as a rotated sprite and alpha-blends the visible ones per frame.

All pixel sizes in ``HookStyle`` are at a 1080 px wide frame and scale with
the frame width.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import fonts
from .captions import ease_out_back
from .config import BASE_WIDTH, HookStyle
from .lexicon import STOPWORDS
from .script import Word, normalize

log = logging.getLogger(__name__)

RGBColor = tuple[int, int, int]


# =========================================================================== contour

@dataclass
class Contour:
    """A path across the frame that the hook text sits on (above)."""
    xs: np.ndarray        # x pixel coords, increasing, covering the usable width
    ys: np.ndarray        # y of the skyline (top edge of terrain/objects) at each x
    confidence: float     # 0..1 how much this looks like a real skyline/ridge
    kind: str             # "skyline" | "line" | "diagonal" | "arc" | "center"


WORK_WIDTH = 240          # skyline detection runs on a frame this wide (fast, robust)
FALLBACKS = ("diagonal", "arc", "center")

# Detection tuning (all in work-resolution rows unless noted).
_SEED_FRACTION = 0.07     # top rows that seed the sky model
_SIGMA_FLOOR = np.array([3.0, 2.5, 2.5], dtype=np.float32)   # L*, a*, b* noise floors
_TEX_FLOOR = 1.5
_SKY_TAU = 3.0            # distance (in sigmas) where a pixel is 50 % sky
_SKY_SLOPE = 1.6
_EDGE_WEIGHT = 2.5        # reward for a boundary on a strong sky->terrain edge, in rows
_JUMP_COST = 0.45         # per row of boundary change between neighbouring columns


def _as_rgb(image) -> np.ndarray:
    """Any frame-like input (PIL image, grey, RGBA, float 0..1) -> HxWx3 uint8."""
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGB"))
    arr = np.asarray(image)
    if arr.dtype != np.uint8:
        arr = np.nan_to_num(arr.astype(np.float32))
        if arr.size and arr.max() <= 1.0:
            arr = arr * 255.0
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        arr = arr[..., None]
    if arr.ndim != 3:
        raise ValueError(f"expected an image array, got shape {arr.shape}")
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    return np.ascontiguousarray(arr[..., :3])


_SRGB_LINEAR = np.where(np.arange(256) / 255.0 <= 0.04045, np.arange(256) / 255.0 / 12.92,
                        ((np.arange(256) / 255.0 + 0.055) / 1.055) ** 2.4).astype(np.float32)
_RGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)


def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 -> CIE L*a*b* (D65), float32. Perceptual distances make one
    threshold work for blue skies, white overcast and golden-hour haze."""
    xyz = (_SRGB_LINEAR[rgb] @ _RGB_TO_XYZ.T) / _D65
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.stack([116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]),
                     200.0 * (f[..., 1] - f[..., 2])], axis=-1).astype(np.float32)


def _luminance(rgb: np.ndarray) -> np.ndarray:
    """Relative (linear) luminance 0..1, as used for WCAG contrast."""
    lin = _SRGB_LINEAR[rgb]
    return lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window (2-D, edge padded) via summed-area tables."""
    if r <= 0:
        return a
    p = np.pad(a, r, mode="edge").astype(np.float64)
    s = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    k = 2 * r + 1
    h, w = a.shape
    out = s[k:k + h, k:k + w] - s[:h, k:k + w] - s[k:k + h, :w] + s[:h, :w]
    return (out / (k * k)).astype(np.float32)


def _median1d(v: np.ndarray, size: int) -> np.ndarray:
    size = max(1, int(size) | 1)
    if size == 1 or len(v) < 2:
        return v.astype(np.float64)
    p = np.pad(v.astype(np.float64), size // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(p, size), axis=1)


def _gauss1d(v: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(v) < 2:
        return v.astype(np.float64)
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    p = np.pad(v.astype(np.float64), r, mode="edge")
    return np.convolve(p, k / k.sum(), mode="valid")


def _ramp(v: float, lo: float, hi: float) -> float:
    """0 at ``lo``, 1 at ``hi``, linear in between (either direction)."""
    if hi == lo:
        return float(v >= hi)
    return float(np.clip((v - lo) / (hi - lo), 0.0, 1.0))


@dataclass
class _Features:
    lab: np.ndarray       # h x w x 3
    tex: np.ndarray       # h x w, local mean gradient magnitude of L*

    @classmethod
    def of(cls, rgb: np.ndarray) -> "_Features":
        h, w = rgb.shape[:2]
        wh = max(8, round(h * WORK_WIDTH / w)) if w > WORK_WIDTH else h
        ww = WORK_WIDTH if w > WORK_WIDTH else w
        small = np.asarray(Image.fromarray(rgb).resize((ww, wh), Image.BOX)) if (ww, wh) != (w, h) else rgb
        lab = _lab(small)
        L = lab[..., 0]
        grad = np.zeros_like(L)
        grad[:-1] += np.abs(np.diff(L, axis=0))
        grad[:, :-1] += np.abs(np.diff(L, axis=1))
        return cls(lab, _box_blur(grad, 1))

    @property
    def shape(self) -> tuple[int, int]:
        return self.tex.shape


def _sky_probability(f: _Features, boundary: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel probability of being sky, and the distance map behind it.

    Without a boundary the sky model is the (horizontally median-filtered)
    colour of the top rows; with one it is a robust linear fit over
    everything above it, which captures the usual brightening toward the
    horizon and side light."""
    h, w = f.shape
    lab = f.lab
    if boundary is None:
        n = max(2, round(h * _SEED_FRACTION))
        seed = lab[:n]
        mu_col = np.median(seed, axis=0)                                  # w x 3
        mu_col = np.stack([_median1d(mu_col[:, c], max(3, w // 5)) for c in range(3)], axis=1)
        mu = np.broadcast_to(mu_col[None], lab.shape).astype(np.float32)
        resid = (seed - mu_col[None]).reshape(-1, 3)
        tex_sky = f.tex[:n]
    else:
        rows = np.arange(h)[:, None]
        mask = rows < (boundary[None, :] - 1)
        if mask.sum() < 30:
            return _sky_probability(f, None)
        ys, xs = np.nonzero(mask)
        step = max(1, len(ys) // 20000)
        ys, xs = ys[::step], xs[::step]
        design = np.stack([np.ones(len(ys)), xs / w, ys / h], axis=1)
        coef, *_ = np.linalg.lstsq(design, lab[ys, xs].astype(np.float64), rcond=None)
        gy, gx = np.mgrid[0:h, 0:w]
        full = np.stack([np.ones(gy.size), gx.ravel() / w, gy.ravel() / h], axis=1)
        mu = (full @ coef).reshape(h, w, 3).astype(np.float32)
        resid = lab[ys, xs] - mu[ys, xs]
        tex_sky = f.tex[mask]
    sigma = np.maximum(1.4826 * np.median(np.abs(resid - np.median(resid, axis=0)), axis=0), _SIGMA_FLOOR)
    tex_mu = float(np.median(tex_sky))
    tex_sigma = max(1.4826 * float(np.median(np.abs(tex_sky - tex_mu))), _TEX_FLOOR)
    d2 = (((lab - mu) / sigma) ** 2).sum(axis=-1)
    d2 += (np.maximum(f.tex - tex_mu, 0.0) / tex_sigma) ** 2
    dist = np.sqrt(d2)
    p = 1.0 / (1.0 + np.exp(np.clip((dist - _SKY_TAU) * _SKY_SLOPE, -30, 30)))
    # Clouds, birds, the sun or text floating in the sky are not terrain:
    # anything non-sky that is not connected to the bottom or the sides of
    # the frame counts as sky, so the ridge is not pulled up to a cloud.
    solid = p < 0.5
    floating = solid & ~_reachable(solid)
    p = np.where(floating, np.maximum(p, 0.9), p)
    return p.astype(np.float32), dist


def _fill_runs(mask: np.ndarray, reached: np.ndarray) -> np.ndarray:
    """Mark every vertical run of ``mask`` that contains a reached pixel."""
    m = mask.T
    start = m & ~np.pad(m, ((0, 0), (1, 0)))[:, :-1]
    label = np.cumsum(start.ravel()).reshape(m.shape) * m
    hit = np.bincount(label[reached.T & m], minlength=int(label.max()) + 1) > 0
    hit[0] = False
    return hit[label].T


def _reachable(mask: np.ndarray) -> np.ndarray:
    """Pixels of ``mask`` connected (4-neighbourhood) to the bottom row or the
    left/right columns: a flood fill done as alternating column and row run
    passes, which converges in a few passes for terrain-like shapes."""
    reached = np.zeros_like(mask)
    reached[-1] = mask[-1]
    reached[:, 0] |= mask[:, 0]
    reached[:, -1] |= mask[:, -1]
    for _ in range(24):
        grown = _fill_runs(mask, reached)
        grown = _fill_runs(mask.T, grown.T).T
        if np.array_equal(grown, reached):
            break
        reached = grown
    return reached


def _boundary_cost(f: _Features, p: np.ndarray) -> np.ndarray:
    """(h+1) x w cost of putting the sky/terrain boundary above row b: sky
    pixels below it and terrain pixels above it are errors; a strong colour
    edge where the sky ends is a bonus."""
    h, w = p.shape
    cum_p = np.vstack([np.zeros((1, w), np.float32), np.cumsum(p, axis=0)])
    rows = np.arange(h + 1, dtype=np.float32)[:, None]
    cost = (rows - cum_p) + (cum_p[-1:] - cum_p)
    lab = f.lab
    up = np.vstack([lab[:1], lab[:-1]])
    up2 = np.vstack([lab[:2], lab[:-2]])
    down = np.vstack([lab[1:], lab[-1:]])
    delta = np.linalg.norm((lab + down) / 2 - (up + up2) / 2, axis=-1)     # edge just above row b
    p_up = np.vstack([p[:1], p[:-1]])
    p_down = np.vstack([p[1:], p[-1:]])
    direction = np.clip((p_up - p_down) * 2.0, 0.0, 1.0)
    edge = np.clip(delta / 12.0, 0.0, 1.5) * direction
    cost[1:h] -= _EDGE_WEIGHT * edge[1:]
    return cost


def _boundary_dp(cost: np.ndarray, jump: float) -> np.ndarray:
    """Cheapest boundary b(x) through the cost table with an L1 penalty on
    b(x)-b(x-1). The min-plus step uses running minima, so a whole column is
    a couple of numpy calls."""
    nb, w = cost.shape
    ramp = np.arange(nb, dtype=np.float64) * jump
    total = np.empty((nb, w), dtype=np.float64)
    total[:, 0] = cost[:, 0]
    for x in range(1, w):
        prev = total[:, x - 1]
        fwd = np.minimum.accumulate(prev - ramp) + ramp
        bwd = np.minimum.accumulate((prev + ramp)[::-1])[::-1] - ramp
        total[:, x] = cost[:, x] + np.minimum(fwd, bwd)
    b = np.empty(w, dtype=np.int64)
    b[-1] = int(np.argmin(total[:, -1]))
    for x in range(w - 1, 0, -1):
        b[x - 1] = int(np.argmin(total[:, x - 1] + np.abs(ramp - ramp[b[x]])))
    return b


@dataclass
class _Detection:
    rows: np.ndarray      # boundary per work column, in work rows (float)
    confidence: float
    detail: dict = field(default_factory=dict)


def _detect_skyline(f: _Features) -> _Detection:
    h, w = f.shape
    boundary = None
    p = dist = None
    for _ in range(3):  # seed model, then two refits on the sky found so far
        p, dist = _sky_probability(f, boundary)
        boundary = _boundary_dp(_boundary_cost(f, p), _JUMP_COST)
    raw = boundary.astype(np.float64)
    smooth = _gauss1d(_median1d(raw, max(3, w // 30)), 1.0)

    # ---- confidence ---------------------------------------------------------
    rows = np.arange(h)[:, None]
    sky_mask = rows < boundary[None, :]
    covered = (raw >= 0.03 * h) & (raw <= 0.97 * h)
    coverage = float(covered.mean())
    sky_fraction = float(raw.mean() / h)
    errors = float((1.0 - p)[sky_mask].sum() + p[~sky_mask].sum())
    agreement = 1.0 - errors / p.size
    # colour step across the boundary (bands of 3 rows each side)
    if covered.any():
        cols = np.nonzero(covered)[0]
        b = boundary[cols]
        above = np.stack([f.lab[np.clip(b - k, 0, h - 1), cols] for k in (2, 3, 4)]).mean(0)
        below = np.stack([f.lab[np.clip(b + k, 0, h - 1), cols] for k in (1, 2, 3)]).mean(0)
        contrast = float(np.median(np.linalg.norm(above - below, axis=1)))
    else:
        contrast = 0.0
    roughness = float(np.mean(np.abs(raw - smooth)) / h)
    sky_tex = float(np.median(f.tex[sky_mask])) if sky_mask.any() else 99.0
    ground_tex = float(np.median(f.tex[~sky_mask])) if (~sky_mask).any() else 0.0
    smooth_sky = _ramp(sky_tex / (ground_tex + 1.0), 1.0, 0.45)

    frac_score = min(_ramp(sky_fraction, 0.03, 0.12), _ramp(sky_fraction, 0.92, 0.78))
    quality = (0.3 * _ramp(contrast, 4.0, 22.0) + 0.3 * _ramp(agreement, 0.75, 0.95)
               + 0.2 * _ramp(roughness, 0.05, 0.005) + 0.2 * smooth_sky)
    confidence = float(np.clip(math.sqrt(coverage) * frac_score * quality, 0.0, 1.0))
    detail = dict(coverage=coverage, sky_fraction=sky_fraction, agreement=agreement,
                  contrast=contrast, roughness=roughness, smooth_sky=smooth_sky)
    return _Detection(smooth, confidence, detail)


def _detect_line(f: _Features) -> _Detection | None:
    """The dominant near-horizontal straight edge (horizon, shoreline, road
    edge): a small Hough transform over gradient-weighted edge pixels."""
    h, w = f.shape
    L = f.lab[..., 0]
    gy = np.zeros_like(L)
    gy[1:-1] = np.abs(L[2:] - L[:-2]) / 2
    if not np.isfinite(gy).all() or gy.max() < 4.0:
        return None
    thresh = max(4.0, float(np.percentile(gy, 92)))
    ys, xs = np.nonzero(gy >= thresh)
    if len(ys) < w // 4:
        return None
    weights = gy[ys, xs]
    best = (0.0, 0.0, 0.0)
    for deg in np.arange(-30.0, 30.1, 2.5):
        t = math.tan(math.radians(deg))
        icpt = ys - t * (xs - w / 2)
        ok = (icpt >= 0) & (icpt < h)
        hist = np.bincount(icpt[ok].astype(np.int64), weights=weights[ok], minlength=h)
        hist = np.convolve(hist, np.ones(3), mode="same")
        i = int(np.argmax(hist))
        if hist[i] > best[0]:
            best = (float(hist[i]), t, float(i))
    _, t, y0 = best
    line = y0 + t * (np.arange(w) - w / 2)
    near = np.abs(ys[None, :] - line[xs][None, :]) <= 1.5
    support = len(np.unique(xs[near[0]])) / w
    centre = y0 / h
    position = min(_ramp(centre, 0.15, 0.3), _ramp(centre, 0.85, 0.7))
    confidence = float(np.clip(support * position, 0.0, 1.0)) * 0.9
    return _Detection(line, confidence, {"support": support})


def _pattern(kind: str, width: int, height: int) -> np.ndarray:
    """Fallback contours for frames without a usable skyline."""
    x = np.arange(width, dtype=np.float64)
    u = x / max(1, width - 1)
    if kind == "arc":
        return height * 0.47 - 0.11 * width * np.sin(np.pi * u)
    if kind == "center":
        return np.full(width, height * 0.45)
    return height * (0.52 - 0.14 * u)  # diagonal: a steady climb to the right


def find_contour(frame: np.ndarray, style: HookStyle) -> Contour:
    """The skyline of ``frame`` (top edge of terrain/objects against the sky),
    or the dominant straight edge, or ``style.fallback`` - whichever is the
    first to reach ``style.min_confidence``. The returned confidence is the
    detector's (so a fallback pattern reports how weak the skyline was)."""
    rgb = _as_rgb(frame)
    height, width = rgb.shape[:2]
    xs = np.arange(width, dtype=np.float64)
    fallback = style.fallback if style.fallback in FALLBACKS else "diagonal"
    if height < 16 or width < 16:
        return Contour(xs, _pattern(fallback, width, height), 0.0, fallback)
    f = _Features.of(rgb)
    h, w = f.shape
    work_x = (np.arange(w) + 0.5) * width / w
    sky = _detect_skyline(f)
    best = sky.confidence
    if sky.confidence >= style.min_confidence:
        ys = np.interp(xs, work_x, sky.rows * height / h)
        return Contour(xs, ys, sky.confidence, "skyline")
    line = _detect_line(f)
    if line is not None:
        best = max(best, line.confidence)
        if line.confidence >= style.min_confidence:
            ys = np.interp(xs, work_x, line.rows * height / h)
            return Contour(xs, ys, line.confidence, "line")
    log.info("no skyline found (confidence %.2f); hook uses the %s pattern", best, fallback)
    return Contour(xs, _pattern(fallback, width, height), best, fallback)


def skyline_score(image) -> float:
    """0..1: how good ``image`` is as the hook shot - a confident skyline with
    relief (peaks beat a flat horizon), room for text above it and a bright
    sky behind dark text. Never raises; unusable input scores 0."""
    try:
        if image is None:
            return 0.0
        rgb = _as_rgb(image)
        if rgb.shape[0] < 16 or rgb.shape[1] < 16:
            return 0.0
        f = _Features.of(rgb)
        sky = _detect_skyline(f)
        h, w = f.shape
        rows = sky.rows
        relief = _ramp(float(rows.max() - rows.min()) / h, 0.02, 0.25)
        room = min(_ramp(float(rows.mean()) / h, 0.15, 0.35), _ramp(float(rows.mean()) / h, 0.85, 0.65))
        sky_mask = np.arange(h)[:, None] < rows[None, :].astype(int)
        if not sky_mask.any():
            return 0.0
        L, a, b = (float(f.lab[..., i][sky_mask].mean()) for i in range(3))
        brightness = _ramp(L, 35.0, 75.0)
        # Real sky is blue (b* well below 0) or a bright, colourless overcast;
        # a wall or tabletop behind an object is neither.
        blue = _ramp(-b, 3.0, 15.0) * _ramp(L, 40.0, 65.0)
        overcast = _ramp(L, 70.0, 85.0) * _ramp(-float(np.hypot(a, b)), -15.0, -6.0)
        skyness = max(blue, overcast)
        score = sky.confidence * (0.45 + 0.3 * relief + 0.15 * room + 0.1 * brightness)
        score *= 0.2 + 0.8 * skyness
        # A "skyline" that runs up to the top edge is sky framed by overhanging
        # branches or walls, not a horizon text can ride along.
        touching_top = float(np.mean(rows < 0.04 * h))
        score *= max(0.0, 1.0 - 2.5 * touching_top)
        return float(np.clip(score, 0.0, 1.0))
    except Exception as exc:  # a scoring helper must never break footage search
        log.debug("skyline_score failed: %s", exc)
        return 0.0


# =========================================================================== layout geometry

def _dilate_up(y: np.ndarray, r: float) -> np.ndarray:
    """Raise a terrain profile (y grows downward) by a disk of radius ``r``:
    the curve that stays ``r`` away from the terrain. Peaks get rounded."""
    if r <= 0.5:
        return y - max(r, 0.0)
    ri = int(math.ceil(r))
    pad = np.pad(y, ri, mode="edge")
    out = np.full_like(y, np.inf, dtype=np.float64)
    for k in range(-ri, ri + 1):
        out = np.minimum(out, pad[ri + k:ri + k + len(y)] - math.sqrt(max(r * r - k * k, 0.0)))
    return out


def _erode_down(y: np.ndarray, r: float) -> np.ndarray:
    """Inverse of ``_dilate_up`` (lower the profile by a disk)."""
    if r <= 0.5:
        return y + max(r, 0.0)
    ri = int(math.ceil(r))
    pad = np.pad(y, ri, mode="edge")
    out = np.full_like(y, -np.inf, dtype=np.float64)
    for k in range(-ri, ri + 1):
        out = np.maximum(out, pad[ri + k:ri + k + len(y)] + math.sqrt(max(r * r - k * k, 0.0)))
    return out


def _slope_limit(y: np.ndarray, max_slope: float) -> np.ndarray:
    """The closest profile above ``y`` whose slope never exceeds
    ``max_slope``, so words never have to stand on a vertical cliff."""
    ramp = np.arange(len(y), dtype=np.float64) * max_slope
    fwd = np.minimum.accumulate(y - ramp) + ramp
    bwd = np.minimum.accumulate((y + ramp)[::-1])[::-1] - ramp
    return np.minimum(fwd, bwd)


class _Path:
    """A baseline ``y(x)`` between two columns, resampled to ~1 px of arc
    length so words can be placed by distance along the curve."""

    def __init__(self, ys: np.ndarray, x0: float, x1: float):
        cols = np.arange(int(math.ceil(x0)), int(math.floor(x1)) + 1)
        y = ys[cols].astype(np.float64)
        x = cols.astype(np.float64)
        arc = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
        self.length = float(arc[-1])
        self.s = np.linspace(0.0, self.length, max(2, int(self.length) + 1))
        self.x = np.interp(self.s, arc, x)
        self.y = np.interp(self.s, arc, y)

    def at(self, s: float) -> np.ndarray:
        return np.array([np.interp(s, self.s, self.x), np.interp(s, self.s, self.y)])

    def points(self, s0: float, s1: float) -> np.ndarray:
        keep = (self.s > s0) & (self.s < s1)
        return np.vstack([self.at(s0), np.stack([self.x[keep], self.y[keep]], axis=1), self.at(s1)])

    def angle(self, s0: float, s1: float) -> float:
        """Direction of the chord from ``s0`` to ``s1`` (radians, clockwise on screen)."""
        d = self.at(s1) - self.at(s0)
        return math.atan2(d[1], d[0]) if np.hypot(*d) > 1e-6 else 0.0


@dataclass
class _Glyph:
    """One rigid piece of text: a whole word, or one letter of a bent word."""
    text: str
    x: float              # anchor = middle of the baseline, frame pixels
    y: float
    angle: float          # radians, clockwise on screen (the slope it sits on)
    box: tuple[float, float, float, float]   # ink box (l, t, r, b) around the anchor, unrotated

    def corners(self, pad: float = 0.0) -> np.ndarray:
        l, t, r, b = self.box
        local = np.array([[l - pad, t - pad], [r + pad, t - pad], [r + pad, b + pad], [l - pad, b + pad]])
        c, s = math.cos(self.angle), math.sin(self.angle)
        rot = np.array([[c, s], [-s, c]])      # local x along the slope, local y "down" across it
        return local @ rot + (self.x, self.y)


def _overlaps(a: np.ndarray, b: np.ndarray) -> bool:
    """Separating-axis test for two convex quads (4x2 corner arrays)."""
    for poly in (a, b):
        for i in range(4):
            edge = poly[(i + 1) % 4] - poly[i]
            axis = np.array([-edge[1], edge[0]])
            pa, pb = a @ axis, b @ axis
            if pa.max() <= pb.min() or pb.max() <= pa.min():
                return False
    return True


@dataclass
class _Metrics:
    """Font measurements of one word at ``REF_SIZE`` (scaled linearly)."""
    text: str
    advance: float
    ink: tuple[float, float, float, float]          # around the baseline-middle anchor
    chars: list[tuple[str, float, float, tuple[float, float, float, float]]]
    # per character: (char, centre offset from the word start, advance, ink box around its anchor)


@dataclass
class _Placed:
    index: int            # position in the hook sentence
    text: str
    size: int             # font px
    start: float          # appears at (seconds)
    glyphs: list[_Glyph]
    bent: bool            # laid letter by letter
    line: int             # 0 = top line
    below: bool = False   # the dropped last word
    color: RGBColor = (17, 17, 17)


@dataclass
class _Sprite:
    x0: int
    y0: int
    rgb: np.ndarray       # premultiplied colour, float32 h x w x 3
    alpha: np.ndarray     # float32 h x w x 1
    image: Image.Image    # premultiplied RGBa, for the pop-in scaling


REF_SIZE = 200            # metrics are measured once at this size
FILL = 0.94               # use at most this share of the path for text
MAX_GROW = 1.4            # short hooks grow at most this much
ONE_LINE_SCALE = 0.6      # a single line along a craggy ridge (the reference look) down to this scale
FLAT_LINE_SCALE = 0.85    # ... but along a flat/straight contour wrapping reads better sooner
UPPER_SLOPE = math.tan(math.radians(40))   # wrapped lines above the first are gentler arcs
GOOD_SCALE = 0.74         # below this, wrapping onto another line reads better
MIN_PX = 26               # no word smaller than this (px at 1080 wide)
WORD_GAP = 0.85           # word spacing as a share of the font's space (the reference packs tightly)
MAX_ATTEMPTS = 48         # layout tries before falling back to the plain pattern
CLOSE_EM = 1.2            # valleys of the text path are rounded to at least this many em
MAX_TILT = math.radians(75)   # steepest a normal-size word may lean ...
BIG_TILT = math.radians(25)   # ... and a big_scale word (in between: interpolated)
MIN_SCALE = 0.3
MAX_LINES = 8
STRESS_SHARE = 0.45       # stressed words get this share of big_scale's boost
LAST_BOOST = 1.12         # the dropped last word is the biggest
TRACKING = 0.06           # extra letter spacing (em) when a word is bent letter by letter
MAX_SLOPE = math.tan(math.radians(70))
LEAD = 0.04               # words appear this much before they are heard
POP = 0.12                # pop-in duration
POP_START = 0.86
DESCENT = 0.2             # em below the baseline an upper line needs over the line beneath
LINE_GAP = 0.1            # em of air between stacked lines
_FUNCTION = {normalize(w) for w in STOPWORDS}


def _parse_hex(value: str) -> RGBColor | None:
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else None
    except ValueError:
        return None


def _contrast(lum_a: float, lum_b: float) -> float:
    hi, lo = max(lum_a, lum_b), min(lum_a, lum_b)
    return (hi + 0.05) / (lo + 0.05)


def _rel_luminance(color: RGBColor) -> float:
    return float(_luminance(np.array([[color]], dtype=np.uint8))[0, 0])


# =========================================================================== renderer

class HookRenderer:
    """Renders the hook sentence along the skyline of ``background``.

    ``words`` carry absolute start/end times on the video timeline; each word
    appears at its start (with a short pop) and every word stays until
    ``end``. ``background`` is the hook shot's frame at the final output
    size (already cropped and graded): it drives the layout and the automatic
    text colour. ``case`` is applied to the word texts ("lower" is the
    reference look)."""

    def __init__(self, words: list[Word], style: HookStyle, font: fonts.FontSpec | str,
                 background: np.ndarray, end: float, case: str = "lower"):
        self.style = style
        self.font_spec = font if isinstance(font, fonts.FontSpec) else fonts.FontSpec(str(font))
        self.end = float(end)
        self.background = _as_rgb(background)
        self.height, self.width = self.background.shape[:2]
        self.k = self.width / BASE_WIDTH
        self.words = [w for w in words if fonts.apply_case(w.text, case).strip()]
        self.texts = [fonts.apply_case(w.text, case).strip() for w in self.words]
        st = style
        self.margin = max(0.0, st.margin * self.k)
        tall = self.height / self.width > 1.4
        self.top_safe = max(self.margin, 0.075 * self.height if tall else 0.0)
        self.bottom_safe = max(self.margin, 0.13 * self.height if tall else 0.0)
        self.close_r = 0.035 * self.width
        self.contour = find_contour(self.background, style)
        if not self._has_room(self.contour):
            kind = st.fallback if st.fallback in FALLBACKS else "diagonal"
            log.info("skyline leaves no room for text above it; hook uses the %s pattern", kind)
            self.contour = Contour(np.arange(self.width, dtype=np.float64),
                                   _pattern(kind, self.width, self.height), self.contour.confidence, kind)
        self.ridge = np.interp(np.arange(self.width), self.contour.xs, self.contour.ys)

        self._metrics = [self._measure(t) for t in self.texts]
        self._space = fonts.load(self.font_spec, REF_SIZE).getlength(" ") / REF_SIZE
        self._below = st.last_word_below and len(self.words) >= 2
        self._factors = self._size_factors()
        self.scale = 1.0
        self.lines = 0
        self._items: list[_Placed] = self._solve() if self.words else []
        self._choose_colors()
        self._sprites = [self._sprite(p) for p in self._items]
        order = sorted(range(len(self._items)), key=lambda i: self._appear(i))
        self._order = order
        self._settle = [self._appear(i) + POP for i in order]
        self._pixels = [self._sparse(sp) for sp in self._sprites]

    # ------------------------------------------------------------------ sizes
    def _measure(self, text: str) -> _Metrics:
        font = fonts.load(self.font_spec, REF_SIZE)
        advance = font.getlength(text)
        l, t, r, b = font.getbbox(text, anchor="ls")
        # Letter clusters for bent words: punctuation sticks to its letter
        # ("y," / "“h"), so a comma never floats on its own around a peak.
        clusters: list[tuple[int, int]] = []
        for i, ch in enumerate(text):
            if clusters and (not ch.isalnum() or not any(c.isalnum() for c in text[slice(*clusters[-1])])):
                clusters[-1] = (clusters[-1][0], i + 1)
            else:
                clusters.append((i, i + 1))
        chars = []
        for a, b2 in clusters:
            left = font.getlength(text[:a])
            adv = font.getlength(text[:b2]) - left
            piece = text[a:b2]
            cl, ct, cr, cb = font.getbbox(piece, anchor="ms")
            chars.append((piece, left + adv / 2, adv, (cl, ct, cr, cb)))
        return _Metrics(text, advance, (l - advance / 2, t, r - advance / 2, b), chars)

    def _size_factors(self) -> list[float]:
        """Relative size of every word: first word big, stressed words
        bigger, function words small, the dropped last word biggest."""
        st, words = self.style, self.words
        n = len(words)
        stress = 1.0 + (st.big_scale - 1.0) * STRESS_SHARE
        f = []
        for i, w in enumerate(words):
            if i == 0:
                f.append(st.big_scale)
            elif w.emphasis > 0:
                f.append(stress)
            elif w.norm in _FUNCTION:
                f.append(st.small_scale)
            else:
                f.append(1.0)
        # Long content words carry the meaning; stress a few of them, never two big words in a row.
        budget = max(1, n // 8) - sum(1 for x in f[1:] if x == stress)
        longest = sorted((i for i in range(1, n - 1) if f[i] == 1.0 and len(words[i].norm) >= 6),
                         key=lambda i: -len(words[i].norm))
        for i in longest:
            if budget <= 0:
                break
            if f[i - 1] <= 1.0 and f[i + 1] <= 1.0:
                f[i] = stress
                budget -= 1
        if n > 1:
            f[-1] = st.big_scale * (LAST_BOOST if self._below else 1.0)
        return f

    def _tilt(self, i: int) -> float:
        """Steepest angle word ``i`` may lean: big words stay nearly upright."""
        big = max(self.style.big_scale, 1.0 + 1e-6)
        u = float(np.clip((self._factors[i] - 1.0) / (big - 1.0), 0.0, 1.0))
        return MAX_TILT + (BIG_TILT - MAX_TILT) * u

    def _has_room(self, contour: Contour) -> bool:
        """Whether most of the skyline leaves space for a line of text
        between it and the top of the safe area (a peak touching the top
        of the frame is fine; a ridge along the top edge is not)."""
        ridge = np.interp(np.arange(self.width), contour.xs, contour.ys)
        inner = ridge[int(self.margin):max(int(self.margin) + 1, int(self.width - self.margin))]
        room = inner - self.top_safe
        return float((room >= 1.5 * self.style.size * self.k).mean()) >= 0.6

    def _px(self, i: int, scale: float) -> float:
        """Font size of word ``i`` at fit ``scale``. The bookends (first word,
        dropped last word) follow the scale only partly: they stay big, as
        in the reference, while the words along the ridge make room."""
        if i == 0:
            scale = scale ** 0.5
        elif self._below and i == len(self.words) - 1:
            scale = scale ** 0.3
        return max(MIN_PX * self.k, self.style.size * self.k * self._factors[i] * scale)

    # ------------------------------------------------------------------ search
    def _solve(self) -> list[_Placed]:
        """Largest layout that fits: one line along the ridge if the text is
        legible there, otherwise wrap onto parallel lines above it. If the
        skyline cannot take the text at all, the plain fallback pattern can."""
        placed = self._search()
        if placed is None and self.contour.kind not in FALLBACKS:
            kind = "center"
            log.info("hook text does not fit the %s; using the %s pattern", self.contour.kind, kind)
            self.contour = Contour(np.arange(self.width, dtype=np.float64),
                                   _pattern(kind, self.width, self.height), self.contour.confidence, kind)
            self.ridge = self.contour.ys.copy()
            placed = self._search()
        if placed is None:  # pathological input: squeeze it in rather than fail
            on_path = self._on_path()
            self.scale, self.lines = MIN_SCALE, min(MAX_LINES, len(on_path))
            placed = self._attempt(on_path, self.lines, MIN_SCALE, force=True) or []
        return placed

    def _on_path(self) -> list[int]:
        return list(range(len(self.words) - 1 if self._below else len(self.words)))

    def _search(self) -> list[_Placed] | None:
        on_path = self._on_path()
        ys = self._baseline(self.ridge, self._lift(on_path, 1.0), 0.0, self._typical(on_path, 1.0))
        base = _Path(ys, *self._span(ys, 0.0))
        demand = sum(self._metrics[i].advance / REF_SIZE * self._px(i, 1.0) for i in on_path)
        demand += sum(self._gap(self._px(i, 1.0), self._px(i + 1, 1.0)) for i in on_path[:-1])
        best: tuple[float, int, list[_Placed]] | None = None
        attempts = 0
        most = min(MAX_LINES, len(on_path))
        relief = self._relief()
        one_line = FLAT_LINE_SCALE + (ONE_LINE_SCALE - FLAT_LINE_SCALE) * relief
        for n in range(1, most + 1):
            good = one_line if n == 1 else GOOD_SCALE
            scale = min(MAX_GROW, FILL * n * base.length / max(demand, 1.0))
            if scale < good and n < most and best is None:
                continue  # clearly too long for n lines
            failed = False
            while scale >= MIN_SCALE and attempts < MAX_ATTEMPTS:
                attempts += 1
                placed = self._attempt(on_path, n, scale)
                if placed is not None:
                    if failed:  # the last step down was 7 %: try half of it back
                        finer = self._attempt(on_path, n, scale * 1.035)
                        if finer is not None:
                            scale, placed = scale * 1.035, finer
                    if best is None or scale > best[0] + 1e-6:
                        best = (scale, n, placed)
                    break
                failed = True
                scale *= 0.93
            if best is not None and best[0] >= good or attempts >= MAX_ATTEMPTS:
                break
        if best is None:
            return None
        self.scale, self.lines, placed = best
        return placed

    def _relief(self) -> float:
        """0..1: how much the contour departs from a straight line (peaks
        and valleys are what make a single line along it worth its small size)."""
        x0, x1 = int(self.margin), max(int(self.margin) + 2, int(self.width - self.margin))
        x = np.arange(x0, x1, dtype=np.float64)
        y = self.ridge[x0:x1]
        resid = y - np.polyval(np.polyfit(x, y, 1), x)
        spread = float(np.percentile(resid, 95) - np.percentile(resid, 5)) / self.width
        return _ramp(spread, 0.02, 0.15)

    def _span(self, ys: np.ndarray, ascent: float) -> tuple[float, float]:
        """Columns the text path may use: inside the margins, minus a run at
        either edge where the terrain reaches above the safe area (a cliff
        or a person filling the side of the frame) - unless that would leave
        too little width."""
        x0, x1 = int(math.ceil(self.margin)), int(self.width - 1 - self.margin)
        if x1 - x0 < 2:
            return 0.0, float(self.width - 1)
        inner = ys[x0:x1 + 1]
        ceiling = self.top_safe + ascent
        # "Free" = well over half of the way down from the ceiling to the
        # typical ridge height, so text starts beside a tall edge object, not on it.
        free = inner > ceiling + 1.0 + 0.6 * max(0.0, float(np.median(inner)) - ceiling)
        if not free.any():
            return float(x0), float(x1)
        first = x0 + int(np.argmax(free))
        last = x1 - int(np.argmax(free[::-1]))
        if last - first >= 0.6 * (x1 - x0):
            return float(first), float(last)
        return float(x0), float(x1)

    def _typical(self, idxs: list[int], scale: float) -> float:
        """Median font size of some words (px)."""
        return float(np.median([self._px(i, scale) for i in idxs])) if idxs else self.style.size * self.k

    def _lift(self, idxs: list[int], scale: float) -> float:
        """Clearance between the ridge and the baseline: the style offset
        plus room for descenders of the line's typical word."""
        return self.style.offset * self.k + 0.12 * self._typical(idxs, scale)

    def _gap(self, size_a: float, size_b: float) -> float:
        return self._space * min(size_a, size_b) * WORD_GAP

    def _baseline(self, terrain: np.ndarray, lift: float, ascent: float, size: float,
                  stacked: bool = False) -> np.ndarray:
        """Text baseline over ``terrain``: ``lift`` above it, slopes capped,
        then every concave corner (valleys, and the kinks the slope cap
        makes) rounded to at least ~1 em so letters of words laid through it
        do not converge; kept inside the safe area. A ``stacked`` line (one
        wrapped above another line of text) is a gentler, rounder arc."""
        y = _slope_limit(_dilate_up(terrain, lift), UPPER_SLOPE if stacked else MAX_SLOPE)
        r = max(self.close_r, (3.0 if stacked else CLOSE_EM) * size)
        y = _erode_down(_dilate_up(y, r), r)
        y = _gauss1d(y, 2.0 * self.k)
        top = self.top_safe + ascent
        bottom = self.height - self.bottom_safe
        return np.clip(y, min(top, bottom), bottom)

    def _split(self, idxs: list[int], scale: float, n: int) -> list[list[int]]:
        """Break ``idxs`` into ``n`` lines of similar width (reading order)."""
        if n <= 1:
            return [idxs]
        widths = np.array([self._metrics[i].advance / REF_SIZE * self._px(i, scale)
                           + self._gap(self._px(i, scale), self._px(i, scale)) for i in idxs])
        ends = np.cumsum(widths)
        cuts, prev = [], 0
        for k in range(1, n):
            j = int(np.argmin(np.abs(ends - ends[-1] * k / n))) + 1
            j = min(max(j, prev + 1), len(idxs) - (n - k))
            cuts.append(j)
            prev = j
        bounds = [0] + cuts + [len(idxs)]
        return [idxs[a:b] for a, b in zip(bounds[:-1], bounds[1:])]

    def _attempt(self, on_path: list[int], n: int, scale: float, force: bool = False) -> list[_Placed] | None:
        lines = self._split(on_path, scale, n)
        obstacles: list[np.ndarray] = []
        placed: list[_Placed] = []
        terrain = self.ridge
        lift = self._lift(on_path, scale)
        for li in range(n - 1, -1, -1):   # bottom line first: upper lines stack on its ink
            idxs = lines[li]
            ascent = max(-self._metrics[i].ink[1] / REF_SIZE * self._px(i, scale) for i in idxs)
            ys = self._baseline(terrain, lift, ascent * 1.05, self._typical(idxs, scale), li < n - 1)
            path = _Path(ys, *self._span(ys, ascent * 1.05))
            words = self._lay_line(path, idxs, scale, obstacles, 0.0, force)
            if words is None:
                return None
            used = max(self._arc_end(words, path), 1.0)
            slack = path.length - used
            if slack > 2.0 and not force:
                centred = self._lay_line(path, idxs, scale, obstacles, slack / 2, False)
                if centred is not None:
                    words = centred
            for p in words:
                p.line = li
            placed[:0] = words
            obstacles += [g.corners() for p in words for g in p.glyphs]
            terrain = np.minimum(ys, self._ink_top(words))
            lift = (DESCENT + LINE_GAP) * self._typical(lines[li - 1], scale) if li else 0.0
        if self._below:
            placed.append(self._place_below(len(self.words) - 1, scale, placed, obstacles))
        return placed

    @staticmethod
    def _arc_end(words: list[_Placed], path: _Path) -> float:
        """Arc length where the last glyph of a line ends."""
        far = max((c for p in words for g in p.glyphs for c in g.corners()[:, 0]), default=path.x[0])
        return float(np.interp(far, path.x, path.s))

    def _ink_top(self, words: list[_Placed]) -> np.ndarray:
        """Topmost text pixel per column (inf where the line has no text)."""
        top = np.full(self.width, np.inf)
        for p in words:
            for g in p.glyphs:
                poly = g.corners()
                for a, b in zip(poly, np.roll(poly, -1, axis=0)):
                    n = int(np.hypot(*(b - a))) + 2
                    xs = np.linspace(a[0], b[0], n)
                    ys = np.linspace(a[1], b[1], n)
                    cols = np.clip(np.round(xs).astype(int), 0, self.width - 1)
                    np.minimum.at(top, cols, ys)
        return top

    # ------------------------------------------------------------------ placing words
    def _lay_line(self, path: _Path, idxs: list[int], scale: float, obstacles: list[np.ndarray],
                  start: float, force: bool) -> list[_Placed] | None:
        out: list[_Placed] = []
        mine: list[np.ndarray] = []
        s = start
        prev = None
        for i in idxs:
            size = self._px(i, scale)
            if prev is not None:
                s += self._gap(prev, size)
            for _ in range(80):
                glyphs, s_end, bent = self._fit_word(path, i, size, s)
                if s_end > path.length + 1.0 and not force:
                    return None
                polys = [g.corners(0.03 * size) for g in glyphs]
                verdict = self._check(polys, obstacles + mine)
                if verdict == "ok" or force:
                    break
                if verdict == "fail":
                    return None
                s += 0.06 * size
            else:
                return None
            out.append(_Placed(i, self.texts[i], int(round(size)), self.words[i].start, glyphs, bent, 0))
            mine += [g.corners() for g in glyphs]
            s = s_end
            prev = size
        return out

    def _check(self, polys: list[np.ndarray], obstacles: list[np.ndarray]) -> str:
        """"ok", "push" (move further along the path) or "fail" (cannot fit)."""
        pts = np.vstack(polys)
        if pts[:, 0].max() > self.width - self.margin * 0.5:
            return "fail"
        if pts[:, 1].min() < self.top_safe * 0.5:
            return "fail"
        if pts[:, 0].min() < self.margin * 0.5:
            return "push"
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        for ob in obstacles:
            if (ob[:, 0].max() < lo[0] or ob[:, 0].min() > hi[0]
                    or ob[:, 1].max() < lo[1] or ob[:, 1].min() > hi[1]):
                continue
            if any(_overlaps(p, ob) for p in polys):
                return "push"
        return "ok"

    def _fit_word(self, path: _Path, i: int, size: float, s: float) -> tuple[list[_Glyph], float, bool]:
        """Place word ``i`` with its start at arc length ``s``: rigid along the
        chord when the curve is gentle under it, letter by letter when it
        bends sharply (and per_glyph is on)."""
        m = self._metrics[i]
        k = size / REF_SIZE
        width = m.advance * k
        chord = path.angle(s, s + width)
        p0 = path.at(s)
        up = np.array([math.sin(chord), -math.cos(chord)])
        dev = (path.points(s, s + width) - p0) @ up
        # Over a sharp peak a rigid word would float far above the slopes,
        # so its letters fan out along the curve instead ("w o u l d"); in a
        # valley a rigid word simply bridges it unless the dip is very deep.
        bend = (self.style.per_glyph and len(m.chars) > 1 and self._factors[i] <= 1.0
                and (float(dev.max()) > 0.45 * size or float(-dev.min()) > 1.0 * size))
        if not bend:
            # Lean with the chord (big words less), then rise straight up
            # until the baseline clears the path under the whole word, so it
            # never cuts the ridge and reading order stays left to right.
            theta = float(np.clip(chord, -self._tilt(i), self._tilt(i)))
            ex = np.array([math.cos(theta), math.sin(theta)])
            x1 = p0[0] + ex[0] * width
            cols = np.linspace(p0[0], x1, max(2, int(x1 - p0[0]) + 1))
            line = p0[1] + math.tan(theta) * (cols - p0[0])
            lift = max(0.0, float((line - np.interp(cols, path.x, path.y)).max()))
            anchor = p0 + ex * width / 2 - (0.0, lift)
            box = tuple(v * k for v in m.ink)
            end = float(np.interp(x1, path.x, path.s))
            return [_Glyph(m.text, float(anchor[0]), float(anchor[1]), theta, box)], max(s + width * 0.5, end), False
        track = TRACKING * size
        for _ in range(4):
            glyphs = []
            for j, (ch, centre, adv, ink) in enumerate(m.chars):
                c = s + centre * k + j * track
                half = max(adv * k / 2, 0.3 * size)
                q = path.at(c)
                glyphs.append(_Glyph(ch, float(q[0]), float(q[1]), path.angle(c - half, c + half),
                                     tuple(v * k for v in ink)))
            # Letters may touch at the bottom where the curve fans them out;
            # only real overlap (boxes shrunk a little) spreads them.
            polys = [g.corners(-0.08 * size) for g in glyphs if g.text.strip()]
            if not any(_overlaps(a, b) for a, b in zip(polys, polys[1:])):
                break
            track += 0.08 * size  # letters converge in a tight bend: spread them
        return glyphs, s + width + track * (len(m.chars) - 1), True

    def _place_below(self, i: int, scale: float, placed: list[_Placed],
                     obstacles: list[np.ndarray]) -> _Placed:
        """The last word, horizontal and large, right-aligned under the end
        of the bottom line (shrunk if the frame has no room for it)."""
        m = self._metrics[i]
        ink_width = (m.ink[2] - m.ink[0]) / REF_SIZE
        size = min(self._px(i, max(scale, 0.8)), (self.width - 2 * self.margin) / max(ink_width, 1e-3))
        bottom_line = max((p.line for p in placed), default=0)
        ends = [g.corners()[:, 0].max() for p in placed if p.line == bottom_line for g in p.glyphs]
        line_end = max(ends) if ends else self.width - self.margin
        limit = self.height - self.bottom_safe
        floor = 0.8 * self.style.size * self.k
        while True:
            k = size / REF_SIZE
            l, t, r, b = (v * k for v in m.ink)
            right = float(np.clip(line_end, self.margin + (r - l), self.width - self.margin))
            x = right - r
            lo, hi = x + l - 0.05 * size, x + r + 0.05 * size
            bottoms = [ob[:, 1].max() for ob in obstacles if ob[:, 0].max() > lo and ob[:, 0].min() < hi]
            top = max(bottoms) if bottoms else float(self.ridge[int(np.clip(right, 0, self.width - 1))])
            y = top + 0.06 * size - t
            if y + b <= limit or size * 0.9 < floor:
                y = min(y, limit - b)
                break
            size *= 0.9
        glyph = _Glyph(m.text, float(x), float(y), 0.0, (l, t, r, b))
        return _Placed(i, self.texts[i], int(round(size)), self.words[i].start, [glyph], False,
                       bottom_line, below=True)

    # ------------------------------------------------------------------ colour + sprites
    def _mask(self, p: _Placed) -> tuple[int, int, np.ndarray]:
        """Antialiased coverage of a placed word: every glyph is drawn at 2x,
        rotated about its baseline anchor, downsampled and unioned."""
        ss = 2
        font = fonts.load(self.font_spec, max(1, round(p.size * ss)))
        pieces = []
        for g in p.glyphs:
            if not g.text.strip():
                continue
            l, t, r, b = font.getbbox(g.text, anchor="ms")
            pad = 2 * ss + 1
            hw, hh = int(max(-l, r)) + pad, int(max(-t, b)) + pad
            canvas = Image.new("L", (2 * hw, 2 * hh), 0)
            ImageDraw.Draw(canvas).text((hw, hh), g.text, font=font, fill=255, anchor="ms")
            rot = canvas.rotate(-math.degrees(g.angle), resample=Image.BICUBIC, expand=True)
            cx, cy = rot.width / 2, rot.height / 2           # the anchor stays at the centre
            even = Image.new("L", (rot.width + rot.width % 2, rot.height + rot.height % 2), 0)
            even.paste(rot, (0, 0))
            small = even.resize((even.width // ss, even.height // ss), Image.BOX)
            x0 = int(round(g.x - cx / ss))
            y0 = int(round(g.y - cy / ss))
            pieces.append((x0, y0, np.asarray(small, dtype=np.float32) / 255.0))
        if not pieces:
            return 0, 0, np.zeros((1, 1), np.float32)
        x0 = min(x for x, _, _ in pieces)
        y0 = min(y for _, y, _ in pieces)
        x1 = max(x + m.shape[1] for x, _, m in pieces)
        y1 = max(y + m.shape[0] for _, y, m in pieces)
        mask = np.zeros((y1 - y0, x1 - x0), np.float32)
        for x, y, m in pieces:
            region = mask[y - y0:y - y0 + m.shape[0], x - x0:x - x0 + m.shape[1]]
            np.maximum(region, m, out=region)
        return x0, y0, mask

    def _backdrop_luminance(self, items: list[_Placed]) -> float:
        """Mean luminance of the background around the given words' ink."""
        lum = _luminance(self.background)
        total = weight = 0.0
        for p in items:
            x0, y0, mask = self._mask(p)
            grown = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(
                ImageFilter.MaxFilter(7)), dtype=np.float32) / 255.0
            fx0, fy0 = max(0, x0), max(0, y0)
            fx1, fy1 = min(self.width, x0 + mask.shape[1]), min(self.height, y0 + mask.shape[0])
            if fx1 <= fx0 or fy1 <= fy0:
                continue
            w = grown[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
            total += float((lum[fy0:fy1, fx0:fx1] * w).sum())
            weight += float(w.sum())
        return total / weight if weight > 0 else 0.5

    def _choose_colors(self) -> None:
        """Fixed colour, or (auto) whichever of dark/light contrasts more with
        what is behind the text. The line and the dropped last word are
        judged separately: the last word often sits on the terrain."""
        st = self.style
        fixed = _parse_hex(st.color) if st.color and st.color != "auto" else None
        if fixed is None and st.color not in ("", "auto"):
            log.warning("hook colour %r is not 'auto' or #rrggbb; using auto", st.color)
        groups = [[p for p in self._items if not p.below], [p for p in self._items if p.below]]
        dark, light = tuple(st.dark_color), tuple(st.light_color)
        for group in groups:
            if not group:
                continue
            if fixed is not None:
                color = fixed
            else:
                bg = self._backdrop_luminance(group)
                color = dark if _contrast(bg, _rel_luminance(dark)) >= _contrast(bg, _rel_luminance(light)) \
                    else light
            for p in group:
                p.color = tuple(int(c) for c in color)

    def _sprite(self, p: _Placed) -> _Sprite:
        x0, y0, mask = self._mask(p)
        color = np.array(p.color, dtype=np.float32)
        alpha = mask[..., None]
        rgb = alpha * color
        if self.style.shadow:
            # A soft halo in the opposite tone, offset slightly downward.
            blur = max(1.0, 0.07 * p.size)
            pad = int(blur * 3) + 2
            dy = int(round(0.03 * p.size))
            big = np.zeros((mask.shape[0] + 2 * pad, mask.shape[1] + 2 * pad), np.float32)
            big[pad + dy:pad + dy + mask.shape[0], pad:pad + mask.shape[1]] = mask
            shadow = np.asarray(Image.fromarray((big * 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(blur)), dtype=np.float32)[..., None] / 255.0 * 0.55
            text = np.zeros_like(shadow)
            text[pad:pad + mask.shape[0], pad:pad + mask.shape[1]] = alpha
            tone = np.float32(0.0 if _rel_luminance(p.color) > 0.4 else 255.0)
            rgb = text * color + shadow * tone * (1 - text)
            alpha = text + shadow * (1 - text)
            x0, y0 = x0 - pad, y0 - pad
        rgba = np.concatenate([rgb, alpha * 255.0], axis=-1)
        data = np.ascontiguousarray(np.clip(rgba + 0.5, 0, 255).astype(np.uint8))
        image = Image.frombytes("RGBa", (data.shape[1], data.shape[0]), data.tobytes())  # premultiplied
        return _Sprite(x0, y0, rgb.astype(np.float32), alpha.astype(np.float32), image)

    # ------------------------------------------------------------------ per frame
    def _appear(self, i: int) -> float:
        return self._items[i].start - LEAD

    @property
    def start(self) -> float:
        """When the first word appears."""
        return min((self._appear(i) for i in range(len(self._items))), default=self.end)

    def _sparse(self, s: _Sprite) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """A sprite as flat frame indices of its visible pixels, premultiplied
        colour and 1-alpha: blending is then one gather and one scatter over
        the text pixels only, whatever the size of the word's bounding box."""
        h, w = s.alpha.shape[:2]
        ys, xs = np.nonzero(s.alpha[..., 0] > 1.0 / 512)
        fy, fx = ys + s.y0, xs + s.x0
        inside = (fy >= 0) & (fy < self.height) & (fx >= 0) & (fx < self.width)
        ys, xs = ys[inside], xs[inside]
        return (fy[inside] * self.width + fx[inside], s.rgb[ys, xs], 1.0 - s.alpha[ys, xs])

    @staticmethod
    def _blend(out: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, x0: int, y0: int) -> None:
        h, w = alpha.shape[:2]
        fx0, fy0 = max(0, x0), max(0, y0)
        fx1, fy1 = min(out.shape[1], x0 + w), min(out.shape[0], y0 + h)
        if fx1 <= fx0 or fy1 <= fy0:
            return
        sx, sy = fx0 - x0, fy0 - y0
        a = alpha[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0]
        c = rgb[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0]
        region = out[fy0:fy1, fx0:fx1].astype(np.float32)
        out[fy0:fy1, fx0:fx1] = np.clip(region * (1 - a) + c + 0.5, 0, 255).astype(np.uint8)

    def overlay(self, frame: np.ndarray, t: float) -> np.ndarray:
        """``frame`` with the words spoken by ``t`` drawn on it (the frame
        itself, untouched, while the hook is not showing)."""
        if not self._items or t >= self.end or t < self.start:
            return frame
        out = np.array(frame, dtype=np.uint8, copy=True)
        settled = int(np.searchsorted(self._settle, t, side="right"))
        if out.shape == (self.height, self.width, 3):
            flat = out.reshape(-1, 3)
            for i in self._order[:settled]:
                idx, rgb, keep = self._pixels[i]
                flat[idx] = (flat[idx] * keep + rgb + 0.5).astype(np.uint8)
        else:  # a frame of another size: blend by position, clipped
            for i in self._order[:settled]:
                s = self._sprites[i]
                self._blend(out, s.rgb, s.alpha, s.x0, s.y0)
        for i in self._order[settled:]:
            appear = self._appear(i)
            if appear > t:
                break
            s = self._sprites[i]
            p = (t - appear) / POP
            scale = POP_START + (1 - POP_START) * ease_out_back(p)
            fade = min(1.0, p / 0.4)
            h, w = s.alpha.shape[:2]
            sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
            arr = np.asarray(s.image.resize((sw, sh), Image.BILINEAR), dtype=np.float32)
            cx, cy = s.x0 + w / 2, s.y0 + h / 2
            self._blend(out, arr[..., :3] * fade, arr[..., 3:] / 255.0 * fade,
                        int(round(cx - sw / 2)), int(round(cy - sh / 2)))
        return out

    def preview(self) -> Image.Image:
        """The background with every hook word drawn (for the UI and plans)."""
        out = self.background.copy()
        for s in self._sprites:
            self._blend(out, s.rgb, s.alpha, s.x0, s.y0)
        return Image.fromarray(out)

    @property
    def placements(self) -> list[dict]:
        """Where every word went: text, font size, colour, appear time, line,
        anchor (middle of the baseline) and angle in degrees (clockwise on
        screen), plus one entry per letter when the word is bent."""
        out = []
        for p in sorted(self._items, key=lambda q: q.index):
            angles = [math.degrees(g.angle) for g in p.glyphs]
            xs = [g.x for g in p.glyphs]
            ys = [g.y for g in p.glyphs]
            pts = np.vstack([g.corners() for g in p.glyphs])
            out.append({
                "index": p.index, "text": p.text, "size": p.size, "start": round(p.start, 3),
                "color": "#%02x%02x%02x" % p.color, "line": p.line, "below": p.below,
                "per_glyph": p.bent,
                "x": round(float(np.mean(xs)), 1), "y": round(float(np.mean(ys)), 1),
                "angle": round(float(np.mean(angles)), 1),
                "glyphs": [{"text": g.text, "x": round(g.x, 1), "y": round(g.y, 1),
                            "angle": round(math.degrees(g.angle), 1)} for g in p.glyphs],
                "bbox": [round(float(v), 1) for v in (*pts.min(axis=0), *pts.max(axis=0))],
            })
        return out
