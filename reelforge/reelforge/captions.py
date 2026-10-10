"""Captions: chunking, timing, SRT export and the animated pop-in renderer.

Rendering is done with Pillow into small per-word sprites that are alpha
blended onto each video frame, so every word can pop independently and the
layout never shifts while words appear.
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import fonts
from .config import BASE_WIDTH, CaptionStyle
from .script import Word

log = logging.getLogger(__name__)

# A chunk should not end on one of these when it can be avoided.
WEAK = set("a an the of to in on at by for with from and or but so your my his her "
           "their our its as than that this before after into".split())


@dataclass
class Caption:
    words: list[Word]
    start: float
    end: float
    appear: list[float]  # when each word pops in

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


# --------------------------------------------------------------------------- chunking

def _chunk(words: list[Word], st: CaptionStyle) -> list[list[Word]]:
    if st.max_words == 1:  # strict word-by-word: no phrase grouping or absorbing
        return [[w] for w in words]
    chunks: list[list[Word]] = []
    cur: list[Word] = []

    def group_len(k: int) -> int:
        g, n = words[k].group, 0
        while k + n < len(words) and words[k + n].group == g:
            n += 1
        return n

    def left_in_sentence(k: int) -> int:
        n = 0
        while k + n < len(words) and words[k + n].sentence == words[k].sentence:
            n += 1
        return n

    hard_cap = st.max_words + 2  # an emphasis phrase may stretch a caption this far
    for k, w in enumerate(words):
        if cur:
            prev = cur[-1]
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            if w.group is not None and w.group == prev.group:
                full = len(cur) >= hard_cap  # otherwise never split a phrase
            else:
                full = len(cur) >= st.max_words or chars > st.max_chars
                if w.group is not None and group_len(k) > 1:
                    # A phrase starts here; if it cannot fit, give it a fresh caption.
                    full = full or len(cur) + group_len(k) > hard_cap
                elif full and left_in_sentence(k) == 1 and len(cur) <= st.max_words \
                        and chars <= st.max_chars:
                    full = False  # absorb a short final word: "So stop saving it."
            if w.start - prev.end > st.gap_break:
                chunks.append(cur)
                cur = []
            elif full:
                # Do not end on "a"/"the"/..., and do not orphan a sentence's
                # last word: move the previous word to the next caption instead.
                carry = None
                if len(cur) > 1 and prev.group is None and (
                        (prev.norm in WEAK and w.norm not in WEAK) or left_in_sentence(k) == 1):
                    carry = cur.pop()
                chunks.append(cur)
                cur = [carry] if carry else []
        cur.append(w)
        if w.ends_sentence or w.ends_clause:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _demote(chunks: list[list[Word]]) -> None:
    """At most one emphasis group per caption (plus anything forced by markup)."""
    for chunk in chunks:
        groups = {w.group: w.priority for w in chunk if w.emphasis and not w.forced}
        if len(groups) <= 1:
            continue
        keep = max(groups, key=lambda g: groups[g])
        for w in chunk:
            if w.emphasis and not w.forced and w.group != keep:
                w.emphasis = 0


def build_captions(words: list[Word], st: CaptionStyle) -> list[Caption]:
    if st.max_words < 1:
        raise ValueError("max_words must be >= 1")
    chunks = _chunk(words, st)
    _demote(chunks)
    starts = [max(0.0, c[0].start - st.lead) for c in chunks]
    captions = []
    for i, chunk in enumerate(chunks):
        start = starts[i]
        nxt = starts[i + 1] if i + 1 < len(chunks) else float("inf")
        end = min(chunk[-1].end + st.hold, nxt)
        if nxt - end < st.flicker_gap:
            end = nxt  # close tiny gaps so captions do not blink
        end = max(end, min(start + st.min_duration, nxt))
        if st.reveal == "build":
            appear = [min(max(start, w.start - st.lead), end) for w in chunk]
        else:
            appear = [start] * len(chunk)
        captions.append(Caption(chunk, start, end, appear))
    return captions


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(captions: list[Caption], path: Path) -> Path:
    lines = []
    for n, c in enumerate(captions, 1):
        lines += [str(n), f"{_ts(c.start)} --> {_ts(c.end)}", c.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- rendering

def ease_out_back(p: float, c1: float = 1.70158) -> float:
    """0 -> 1 with a ~10% overshoot near the end: the "pop"."""
    c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


@dataclass
class _Sprite:
    rgb: np.ndarray    # premultiplied, float32, HxWx3, 0..255
    alpha: np.ndarray  # float32, HxWx1, 0..1
    image: Image.Image # premultiplied RGBa, used for scaled variants
    cx: float          # where the sprite centre goes in the frame
    cy: float


class CaptionRenderer:
    """Pixel sizes in ``style`` are at a 1080 px wide frame; they are scaled
    by ``width / 1080`` so 9:16, 4:5, 1:1 and 16:9 all look the same."""

    def __init__(self, captions: list[Caption], style: CaptionStyle, font: fonts.FontSpec | str,
                 width: int = 1080, height: int = 1920):
        self.captions = captions
        self.st = style
        self.font_spec = font if isinstance(font, fonts.FontSpec) else fonts.FontSpec(str(font))
        self.width, self.height = width, height
        self.k = width / BASE_WIDTH
        self._starts = [c.start for c in captions]
        self._layouts: dict[int, list[_Sprite]] = {}

    # -- layout ------------------------------------------------------------
    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        return fonts.load(self.font_spec, size)

    def _px(self, value: float) -> int:
        return int(round(value * self.k))

    def _colour(self, w: Word):
        if self.st.emphasis != "color":
            return self.st.text_color
        return {1: self.st.highlight_color, 2: self.st.alert_color}.get(w.emphasis, self.st.text_color)

    def _wrap(self, widths: list[float], space: float) -> list[list[int]]:
        lines: list[list[int]] = [[]]
        line_w = 0.0
        for i, w in enumerate(widths):
            add = w if not lines[-1] else space + w
            if lines[-1] and line_w + add > self._px(self.st.max_line_width):
                lines.append([i])
                line_w = w
            else:
                lines[-1].append(i)
                line_w += add
        return lines

    def _layout(self, idx: int) -> list[_Sprite]:
        if idx in self._layouts:
            return self._layouts[idx]
        st = self.st
        cap = self.captions[idx]
        texts = [fonts.apply_case(w.text, st.case) for w in cap.words]
        size = self._px(st.font_size)
        stroke = self._px(st.stroke_width)
        while True:
            font = self._font(size)
            widths = [font.getlength(t) + 2 * stroke for t in texts]
            space = font.getlength(" ")
            lines = self._wrap(widths, space)
            if (len(lines) <= st.max_lines and max(widths) <= self._px(st.max_line_width)) \
                    or size <= self._px(36):
                break
            size = int(size * 0.92)  # shrink until it fits the safe area

        ascent, descent = font.getmetrics()
        line_h = (ascent + descent) * st.line_spacing
        top = st.y_center * self.height - line_h * len(lines) / 2
        sprites: list[_Sprite | None] = [None] * len(texts)
        for li, line in enumerate(lines):
            line_w = sum(widths[i] for i in line) + space * (len(line) - 1)
            x = (self.width - line_w) / 2
            cy = top + li * line_h + line_h / 2
            for i in line:
                sprites[i] = self._sprite(texts[i], font, self._colour(cap.words[i]),
                                          x + widths[i] / 2, cy)
                x += widths[i] + space
        self._layouts = {idx: sprites}  # only the current caption stays cached
        return sprites

    def _sprite(self, text: str, font: ImageFont.FreeTypeFont, colour, cx: float, cy: float) -> _Sprite:
        st = self.st
        ascent, descent = font.getmetrics()
        ox, oy = self._px(st.shadow_offset[0]), self._px(st.shadow_offset[1])
        stroke, blur = self._px(st.stroke_width), st.shadow_blur * self.k
        pad = stroke + int(blur * 2) + max(abs(ox), abs(oy)) + 2
        w = int(font.getlength(text)) + 2 * pad
        h = ascent + descent + 2 * pad
        origin = (w / 2, pad + ascent)  # middle of the baseline

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if st.shadow_opacity > 0:
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).text((origin[0] + ox, origin[1] + oy), text, font=font, fill=255,
                                      anchor="ms", stroke_width=stroke, stroke_fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(blur))
            img.putalpha(mask.point(lambda v: int(v * st.shadow_opacity)))
        ImageDraw.Draw(img).text(origin, text, font=font, fill=tuple(colour), anchor="ms",
                                 stroke_width=stroke, stroke_fill=tuple(st.stroke_color))
        pm = img.convert("RGBa")
        arr = np.asarray(pm, dtype=np.float32)
        # The ascent+descent box is centred in the sprite, so the sprite centre
        # lands on the line centre and every word of a line shares a baseline.
        return _Sprite(arr[..., :3], arr[..., 3:] / 255.0, pm, cx, cy)

    # -- per-frame ----------------------------------------------------------
    def caption_at(self, t: float) -> int | None:
        i = bisect.bisect_right(self._starts, t) - 1
        if i < 0 or t >= self.captions[i].end:
            return None
        return i

    def overlay(self, frame: np.ndarray, t: float) -> np.ndarray:
        idx = self.caption_at(t)
        if idx is None:
            return frame
        out = np.array(frame, copy=True)
        cap = self.captions[idx]
        for sprite, appear in zip(self._layout(idx), cap.appear):
            if t < appear:
                continue
            p = (t - appear) / self.st.pop_duration if self.st.pop_duration > 0 else 1.0
            if p >= 1.0:
                self._blend(out, sprite.rgb, sprite.alpha, sprite.cx, sprite.cy)
                continue
            scale = self.st.pop_start_scale + (1 - self.st.pop_start_scale) * ease_out_back(p)
            fade = min(1.0, p / 0.35)
            w = max(1, int(sprite.image.width * scale))
            h = max(1, int(sprite.image.height * scale))
            arr = np.asarray(sprite.image.resize((w, h), Image.BILINEAR), dtype=np.float32)
            self._blend(out, arr[..., :3] * fade, arr[..., 3:] / 255.0 * fade, sprite.cx, sprite.cy)
        return out

    @staticmethod
    def _blend(out: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, cx: float, cy: float) -> None:
        h, w = alpha.shape[:2]
        x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
        fx0, fy0 = max(0, x0), max(0, y0)
        fx1, fy1 = min(out.shape[1], x0 + w), min(out.shape[0], y0 + h)
        if fx1 <= fx0 or fy1 <= fy0:
            return
        sx, sy = fx0 - x0, fy0 - y0
        a = alpha[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0]
        c = rgb[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0]
        region = out[fy0:fy1, fx0:fx1].astype(np.float32)
        out[fy0:fy1, fx0:fx1] = np.clip(region * (1 - a) + c, 0, 255).astype(np.uint8)
