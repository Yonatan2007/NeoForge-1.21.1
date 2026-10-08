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
