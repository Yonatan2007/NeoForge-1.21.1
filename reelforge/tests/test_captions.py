import numpy as np
import pytest
from PIL import ImageFont

from reelforge.captions import CaptionRenderer, build_captions, ease_out_back, write_srt
from reelforge.config import CaptionStyle
from reelforge.script import parse_script

from .test_script import EXAMPLE


def timed(text, rate=0.3):
    s = parse_script(text)
    for i, w in enumerate(s.words):
        w.start, w.end = i * rate, i * rate + rate * 0.9
    return s


def test_chunks_read_naturally():
    caps = build_captions(timed(EXAMPLE).words, CaptionStyle())
    assert [c.text for c in caps] == [
        "You've already had", "your last conversation", "with someone.",
        "You just don't", "know which one.", "So stop saving it.",
        "The thank you.", "The I'm proud of you.", "The I'm sorry.",
        "Nobody gets", "a warning before", "the last time.",
        "Say it today.", "Say it badly,", "if you have to.", "Just say it.",
    ]


def test_one_emphasis_group_per_caption():
    s = parse_script("Never stop. Love now, today.")
    for i, w in enumerate(s.words):
        w.start, w.end = i * 0.3, i * 0.3 + 0.25
    caps = build_captions(s.words, CaptionStyle(max_words=5))
    for c in caps:
        assert len({w.group for w in c.words if w.emphasis}) <= 1


def test_timing_invariants():
    st = CaptionStyle()
    caps = build_captions(timed(EXAMPLE).words, st)
    for a, b in zip(caps, caps[1:]):
        assert a.start < a.end <= b.start
    for c in caps:
        assert all(c.start <= t <= c.end for t in c.appear)
        assert c.appear == sorted(c.appear)


def test_pause_forces_new_caption():
    s = parse_script("one two three")
    times = [(0.0, 0.2), (0.25, 0.4), (1.5, 1.7)]
    for w, (a, b) in zip(s.words, times):
        w.start, w.end = a, b
    caps = build_captions(s.words, CaptionStyle())
    assert [c.text for c in caps] == ["one two", "three"]


def test_word_by_word_and_phrase_reveal():
    s = timed("Say it badly if you have to.")
    assert all(len(c.words) <= 2 for c in build_captions(s.words, CaptionStyle(max_words=1)))
    caps = build_captions(s.words, CaptionStyle(reveal="phrase"))
    assert all(len(set(c.appear)) == 1 for c in caps)


def test_srt(tmp_path):
    caps = build_captions(timed("Just say it.").words, CaptionStyle())
    text = write_srt(caps, tmp_path / "c.srt").read_text()
    assert text.startswith("1\n00:00:00,000 --> 00:00:")
    assert "Just say it." in text


def test_ease_out_back_overshoots_then_settles():
    assert ease_out_back(0.0) == pytest.approx(0.0)
    assert ease_out_back(1.0) == pytest.approx(1.0)
    assert max(ease_out_back(p / 100) for p in range(101)) > 1.05


def _any_font():
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            ImageFont.truetype(name, 10)
            return name
        except OSError:
            continue
    pytest.skip("no TrueType font available")


def test_renderer_draws_only_while_caption_is_live():
    s = timed("Say it today.")
    st = CaptionStyle(font_size=40, max_line_width=300)
    caps = build_captions(s.words, st)
    r = CaptionRenderer(caps, st, _any_font(), width=360, height=640)
    blank = np.zeros((640, 360, 3), np.uint8)
    assert r.overlay(blank, caps[0].start - 0.01) is blank
    live = r.overlay(blank, caps[0].end - 0.01)
    assert live.any() and not blank.any()
    ys = np.nonzero(live.max(axis=(1, 2)))[0]
    assert 200 < ys.mean() < 440  # centred vertically
    # the emphasised word is drawn in the highlight colour
    yellow = (live[..., 0] > 200) & (live[..., 1] > 170) & (live[..., 2] < 80)
    assert yellow.sum() > 50
