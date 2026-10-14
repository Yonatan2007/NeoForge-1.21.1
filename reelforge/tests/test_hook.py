import time

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from reelforge import hook
from reelforge.config import HookStyle
from reelforge.script import parse_script

SENTENCE = "If you lost your memory, who would you trust to tell you who you are?"


def _font():
    for name in ("/usr/share/fonts/opentype/inter/Inter-Bold.otf", "DejaVuSans-Bold.ttf"):
        try:
            ImageFont.truetype(name, 10)
            return name
        except OSError:
            continue
    pytest.skip("no TrueType font available")


def mountains(w=540, h=960, ridge=((0, 420), (120, 300), (200, 360), (300, 220), (420, 340), (540, 300))):
    """Blue sky gradient over a jagged brown/green mountain polygon."""
    y = np.linspace(0, 1, h)[:, None, None]
    sky = (np.array([70, 150, 215]) * (1 - y) + np.array([170, 210, 235]) * y).repeat(w, axis=1)
    im = Image.fromarray(sky.astype(np.uint8))
    ImageDraw.Draw(im).polygon(list(ridge) + [(w, h), (0, h)], fill=(105, 120, 70))
    return np.asarray(im), ridge


def timed(n_offset=0.0):
    s = parse_script(SENTENCE)
    for i, w in enumerate(s.words):
        w.start, w.end = n_offset + 0.2 * i, n_offset + 0.2 * i + 0.18
    return s.words


def test_finds_the_ridge_of_a_mountain():
    frame, ridge = mountains()
    c = hook.find_contour(frame, HookStyle())
    assert c.kind == "skyline" and c.confidence > 0.6
    xs = np.array([p[0] for p in ridge])
    ys = np.array([p[1] for p in ridge])
    expected = np.interp(c.xs, xs, ys)
    inner = (c.xs > 20) & (c.xs < 520)
    assert np.median(np.abs(c.ys[inner] - expected[inner])) < 20


@pytest.mark.parametrize("frame", [np.zeros((640, 360, 3), np.uint8),
                                   np.full((640, 360, 3), 255, np.uint8),
                                   np.random.default_rng(1).integers(0, 255, (640, 360, 3), dtype=np.uint8)])
def test_never_crashes_and_falls_back_on_frames_without_sky(frame):
    c = hook.find_contour(frame, HookStyle())
    assert len(c.xs) == len(c.ys) > 10 and 0.0 <= c.confidence <= 1.0
    r = hook.HookRenderer(timed(), HookStyle(), _font(), frame, end=5.0)
    out = r.overlay(frame, 4.0)
    assert out.shape == frame.shape and not np.array_equal(out, frame)


def test_words_accumulate_above_the_ridge_and_vanish_at_end():
    frame, _ = mountains()
    words = timed()
    r = hook.HookRenderer(words, HookStyle(), _font(), frame, end=4.0)
    assert r.overlay(frame, words[0].start - 0.05) is frame or np.array_equal(
        r.overlay(frame, words[0].start - 0.05), frame)
    early = np.abs(r.overlay(frame, words[2].start + 0.15).astype(int) - frame).sum()
    late = np.abs(r.overlay(frame, words[-1].start + 0.3).astype(int) - frame).sum()
    assert 0 < early < late                        # more words, more ink
    assert np.array_equal(r.overlay(frame, 4.01), frame)
    p = r.placements
    assert [x["text"] for x in p][:3] == ["if", "you", "lost"]   # lower case by default
    assert p[0]["size"] > p[1]["size"] and p[-1]["size"] > p[1]["size"]  # big first/last words
    above = [x for x in p if not x["below"]]
    assert {x["color"] for x in above} == {"#111111"}  # dark text on the bright sky
    assert p[-1]["below"] and p[-1]["color"] == "#ffffff"  # "are?" dropped onto the mountain: light


def test_skyline_score_prefers_a_real_sky():
    frame, _ = mountains()
    wall = np.full((960, 540, 3), (150, 130, 110), np.uint8)
    wall[500:] = (60, 50, 40)
    assert hook.skyline_score(frame) > 0.4 > hook.skyline_score(wall)
    assert hook.skyline_score(None) == 0.0


def test_overlay_is_fast_enough_for_video():
    frame, _ = mountains(1080, 1920, ((0, 840), (240, 600), (400, 720), (600, 440), (840, 680), (1080, 600)))
    words = timed()
    r = hook.HookRenderer(words, HookStyle(), _font(), frame, end=6.0)
    r.overlay(frame, 3.0)  # warm the sprite cache
    t0 = time.perf_counter()
    for k in range(20):
        r.overlay(frame, 3.0 + k / 30)
    assert (time.perf_counter() - t0) / 20 < 0.05
    assert r.preview().size == (1080, 1920)


def test_words_bend_with_the_terrain_letter_by_letter():
    frame, _ = mountains()
    words = timed()
    r = hook.HookRenderer(words, HookStyle(), _font(), frame, end=5.0)
    on_ridge = [p for p in r._items if not p.below]
    first, rest = on_ridge[0], [p for p in on_ridge[1:] if len(p.text) > 1]
    assert not first.bent and len(first.glyphs) == 1  # the big opening word stays one piece
    assert rest and all(p.bent and len(p.glyphs) > 1 for p in rest)
    # the letters of a word over a peak lean differently: the word curves, it doesn't just tilt
    assert max(max(g.angle for g in p.glyphs) - min(g.angle for g in p.glyphs) for p in rest) > 0.3
    straight = hook.HookRenderer(timed(), HookStyle(per_glyph=False), _font(), frame, end=5.0)
    assert not any(p.bent for p in straight._items) and all(len(p.glyphs) == 1 for p in straight._items)


def _line_extents(placed):
    rows = {}
    for p in placed:
        if p.below:
            continue
        xs = [c for g in p.glyphs for c in g.corners()[:, 0]]
        lo, hi = rows.get(p.line, (np.inf, -np.inf))
        rows[p.line] = (min(lo, min(xs)), max(hi, max(xs)))
    return [rows[k] for k in sorted(rows)]


def test_wrapped_lines_read_in_order_over_a_wavy_skyline():
    # A low, wavy skyline (like a band of cloud) under a long hook: the text
    # wraps, and an upper line used to slide off the end of the line beneath
    # and land beside it on the left: "patient; you are | you are not being".
    ridge = ((0, 152), (45, 158), (90, 166), (135, 171), (180, 165), (225, 160), (270, 158),
             (315, 166), (360, 159), (405, 162), (450, 191), (495, 191), (540, 189))
    frame, _ = mountains(540, 675, ridge)
    s = parse_script("You are not being patient; you are secretly keeping score.")
    for i, w in enumerate(s.words):
        w.start, w.end = 0.2 * i, 0.2 * i + 0.18
    r = hook.HookRenderer(s.words, HookStyle(), _font(), frame, end=5.0)
    lines = _line_extents(r._items)
    assert r.lines >= 2 and len(lines) == r.lines
    for (a0, a1), (b0, b1) in zip(lines, lines[1:]):
        short = min(a1 - a0, b1 - b0)
        stacked = min(a1, b1) - max(a0, b0) >= 0.5 * short
        assert stacked or b0 >= a1 - 0.25 * short, "a line reads after the one below it"


def test_reading_order_check_and_held_top():
    frame, _ = mountains()
    r = hook.HookRenderer(timed(), HookStyle(), _font(), frame, end=5.0)

    def word(i, line, x, y):
        return hook._Placed(i, "word", 40, 0.0, [hook._Glyph("word", x, y, 0.0, (-50, -40, 50, 0))], False, line)

    assert not r._reads_in_order([word(0, 0, 400, 100), word(1, 1, 150, 110)])  # next line beside, on the left
    assert r._reads_in_order([word(0, 0, 200, 100), word(1, 1, 220, 150)])      # stacked
    assert r._reads_in_order([word(0, 0, 150, 100), word(1, 1, 400, 105)])      # one row, in order

    held = r._held_top([word(0, 0, 150, 100), word(1, 0, 350, 80)])
    assert np.isfinite(held).all()
    assert held[0] == pytest.approx(60, abs=1) and held[-1] == pytest.approx(40, abs=1)  # ends held level
    assert 40 <= held[250] <= 60                                                 # the gap is bridged


def test_a_gentle_line_bends_only_a_little_over_jagged_peaks():
    frame, _ = mountains()
    steep = hook.HookRenderer(timed(), HookStyle(), _font(), frame, end=4.0)
    soft = hook.HookRenderer(timed(), HookStyle(curve="gentle"), _font(), frame, end=4.0)

    def tilt(r):
        return max(abs(g["angle"]) for p in r.placements if not p["below"] for g in p["glyphs"])
    assert tilt(soft) <= 15 < tilt(steep)
    ridge = soft.ridge
    for p in soft.placements:  # and the words still clear every peak
        if not p["below"]:
            assert all(g["y"] < ridge[int(min(max(g["x"], 0), len(ridge) - 1))] for g in p["glyphs"])


def test_the_whole_hook_can_appear_at_once():
    frame, _ = mountains()
    words = timed()
    r = hook.HookRenderer(words, HookStyle(reveal="all"), _font(), frame, end=4.0)
    assert np.array_equal(r.overlay(frame, words[0].start), r.overlay(frame, words[-1].end))  # every word, in full
    assert np.array_equal(r.overlay(frame, words[0].start - 0.1), frame)


def test_a_gentle_hook_prefers_rolling_hills_to_tree_tops():
    w, h = 540, 960
    hills = [(x, 600 + 30 * np.sin(x / w * np.pi * 1.5)) for x in range(0, w + 1, 10)]
    trees = [(x, 600 - (28 if (x // 14) % 2 else 0)) for x in range(0, w + 1, 14)]
    smooth, _ = mountains(w, h, ridge=hills)
    spiky, _ = mountains(w, h, ridge=trees)
    assert hook.skyline_score(smooth, gentle=True) > hook.skyline_score(spiky, gentle=True)
