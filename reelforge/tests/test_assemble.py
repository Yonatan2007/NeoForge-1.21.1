import shutil
import subprocess

import numpy as np
import pytest

from reelforge.assemble import Timeline, plan_shots
from reelforge.config import CaptionStyle, Settings, Style, VideoStyle
from reelforge.pipeline import RenderOptions, render
from reelforge.script import parse_script

from .test_script import EXAMPLE


def test_shots_cut_on_sentences_and_respect_limits():
    vs = VideoStyle()
    s = parse_script(EXAMPLE)
    for i, w in enumerate(s.words):
        w.start, w.end = 0.2 + i * 0.4, 0.2 + i * 0.4 + 0.35
    total = s.words[-1].end + vs.tail
    shots = plan_shots(s.words, total, vs)
    assert shots[0][0] == 0 and shots[-1][1] == pytest.approx(total)
    for (a, b), (c, _) in zip(shots, shots[1:]):
        assert b == c
    assert all(b - a <= vs.max_shot + 1e-9 for a, b in shots)
    sentence_cuts = {round(w.start - vs.cut_preroll, 6) for i, w in enumerate(s.words)
                     if i and s.words[i - 1].ends_sentence}
    assert any(round(a, 6) in sentence_cuts for a, _ in shots[1:])


class Solid:
    def __init__(self, value, duration):
        self.value, self.duration = value, duration

    def get_frame(self, t):
        return np.full((4, 4, 3), self.value, np.uint8)


def test_timeline_crossfades_linearly():
    vs = VideoStyle(crossfade=0.4, fade_in=0, fade_out=0)
    tl = Timeline([Solid(0, 2.4), Solid(200, 2.0)], [0.0, 2.0], vs, None)
    assert tl.frame(1.0)[0, 0, 0] == 0
    assert tl.frame(2.1)[0, 0, 0] == pytest.approx(50, abs=1)   # a = 0.25
    assert tl.frame(2.3)[0, 0, 0] == pytest.approx(150, abs=1)  # a = 0.75
    assert tl.frame(3.0)[0, 0, 0] == 200


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_render_end_to_end(tmp_path):
    """Tiny full render: synthetic voice, one synthetic clip, estimated timings."""
    pytest.importorskip("moviepy")
    font = None
    for name in ("DejaVuSans-Bold.ttf", "Arial.ttf"):
        try:
            from PIL import ImageFont
            ImageFont.truetype(name, 10)
            font = name
            break
        except OSError:
            continue
    if font is None:
        pytest.skip("no TrueType font available")
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "sine=f=200:d=0.8,apad=pad_dur=0.5", "-f", "lavfi", "-i",
                    "sine=f=300:d=0.9", "-filter_complex", "[0][1]concat=n=2:v=0:a=1",
                    str(voice)], check=True)
    clips = tmp_path / "clips"
    clips.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=12",
                    "-t", "1", str(clips / "a.mp4")], check=True)
    style = Style(caption=CaptionStyle(font_path=font, font_size=24, max_line_width=240),
                  video=VideoStyle(width=270, height=480, fps=12, preset="ultrafast"))
    out = render("Stop waiting. Say it today.", tmp_path / "out" / "demo",
                 Settings(cache_dir=tmp_path / "cache"), style,
                 RenderOptions(voiceover=str(voice), footage_dir=clips, align="estimate"),
                 logger=None)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True)
    assert "video,270,480" in probe.stdout and "audio" in probe.stdout
    assert (out.parent / "captions.srt").read_text().count("-->") >= 2
