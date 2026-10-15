import shutil
import subprocess

import numpy as np
import pytest

from reelforge.assemble import Timeline, plan_shots
from reelforge.config import VideoStyle
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
    tl = Timeline([Solid(0, 2.4), Solid(200, 2.0)], [0.0, 2.0], vs)
    assert tl.frame(1.0)[0, 0, 0] == 0
    assert tl.frame(2.1)[0, 0, 0] == pytest.approx(50, abs=1)   # a = 0.25
    assert tl.frame(2.3)[0, 0, 0] == pytest.approx(150, abs=1)  # a = 0.75
    assert tl.frame(3.0)[0, 0, 0] == 200


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_render_project_end_to_end(tmp_path):
    """Tiny full render: synthetic voice, a user picture and a user clip, no
    stock, estimated timings, draft size, terrain hook, music bed."""
    pytest.importorskip("moviepy")
    from PIL import Image

    from reelforge import config
    from reelforge.config import FootageItem, Project, Settings
    from reelforge.pipeline import render_project

    def ff(*args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)

    up = tmp_path / "proj" / "uploads"
    up.mkdir(parents=True)
    ff("-f", "lavfi", "-i", "sine=f=200:d=1.0,apad=pad_dur=0.5", "-f", "lavfi", "-i",
       "sine=f=300:d=1.2", "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(up / "voice.wav"))
    ff("-f", "lavfi", "-i", "sine=f=440:d=6", str(up / "music.wav"))
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=12", "-t", "4", str(up / "clip.mp4"))
    sky = Image.new("RGB", (400, 600), (90, 170, 220))
    sky.paste((120, 110, 90), (0, 380, 400, 600))
    sky.save(up / "mountain.png")

    p = config.from_dict(Project, config.preset_dict("reference"))
    p.script = "Stop waiting for a sign. Say it today."
    p.voice = config.VoiceSettings(source="file", file="uploads/voice.wav", align="estimate")
    p.music = config.MusicSettings(file="uploads/music.wav", source_in=1.0, source_out=3.0)
    p.footage.items = [FootageItem(path="uploads/mountain.png"), FootageItem(path="uploads/clip.mp4")]
    p.footage.stock = False
    p.style.video.aspect, p.style.video.draft, p.style.video.fps = "1:1", True, 12
    p.style.video.letterbox = True
    p.style.video.preset = "ultrafast"
    (tmp_path / "proj" / "output").mkdir()
    (tmp_path / "proj" / "output" / "credits.txt").write_text("a stock clip from an older render")
    stages = []
    result = render_project(p, tmp_path / "proj", Settings(cache_dir=tmp_path / "cache",
                                                          home_dir=tmp_path / "home"),
                            progress=lambda st, f, m: stages.append((st, f)))
    out = tmp_path / "proj" / result["video"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True)
    assert "video,540,960" in probe.stdout and "audio" in probe.stdout  # square picture on black
    assert (tmp_path / "proj" / result["cover"]).stat().st_size > 0
    assert (tmp_path / "proj" / result["srt"]).read_text().count("-->") >= 2
    assert result["credits"] is None  # no stock in this render: the old credits are gone
    fracs = [f for _, f in stages]
    assert fracs == sorted(fracs) and fracs[-1] == pytest.approx(1.0)


def test_hook_shot_is_never_cut_or_split():
    vs = VideoStyle(min_shot=1.0, max_shot=2.0)
    s = parse_script("One two three four five six. Seven eight. Nine ten.")
    for i, w in enumerate(s.words):
        w.start, w.end = i * 0.5, i * 0.5 + 0.4
    shots = plan_shots(s.words, 6.0, vs, keep_until=3.4)
    assert shots[0][0] == 0.0 and shots[0][1] >= 3.4
    assert all(b - a <= 2.0 + 1e-9 for a, b in shots[1:])


def timed(text, times):
    s = parse_script(text)
    for w, (a, b) in zip(s.words, times):
        w.start, w.end = a, b
    return s.words


def test_a_short_hook_does_not_turn_the_reel_into_one_shot():
    # hook "Why do we wait?" ends at 1.5 s; the next sentence runs to the end
    text = "Why do we wait? " + " ".join(["word"] * 40) + "."
    times = [(i * 0.4, i * 0.4 + 0.35) for i in range(4)] + \
            [(1.6 + i * 0.45, 1.6 + i * 0.45 + 0.4) for i in range(40)]
    words = timed(text, times)
    shots = plan_shots(words, 19.4, VideoStyle(), keep_until=1.5)
    assert shots[0] == (0.0, 3.0)  # the hook picture, at least min_shot long
    assert len(shots) >= 3 and all(b - a <= 7.0 + 1e-9 for a, b in shots)
    assert shots[-1][1] == 19.4


def test_hook_is_cut_when_the_next_sentence_follows_immediately():
    words = timed("If you lost it, who would you trust? Not who would show up for you.",
                  [(0.3 * i, 0.3 * i + 0.3) for i in range(8)] + [(3.2 + 0.3 * i, 3.5 + 0.3 * i)
                                                                 for i in range(7)])
    words[7].end = 3.1  # "trust?" is drawn out; "Not" starts 0.1 s later
    shots = plan_shots(words, 9.1, VideoStyle(), keep_until=3.1)
    assert shots[0] == (0.0, 3.1)  # closer than the pre-roll: cut where the hook ends


class Mark:
    def __init__(self, value):
        self.value = value

    def overlay(self, frame, t):
        out = frame.copy()
        out[0, 0, 1] = self.value
        return out


def test_timeline_applies_overlays_in_order():
    vs = VideoStyle(crossfade=0, fade_in=0, fade_out=0)
    tl = Timeline([Solid(10, 3.0)], [0.0], vs, [Mark(1), Mark(2)])
    assert tl.frame(1.0)[0, 0, 1] == 2 and tl.frame(1.0)[1, 1, 0] == 10


def test_frame_progress_reports_fractions_and_keeps_proglog_working():
    from reelforge.assemble import _FrameProgress

    seen = []
    logger = _FrameProgress(seen.append)
    logger(message="MoviePy - writing")           # proglog's own callback path must still work
    for i in logger.iter_bar(frame_index=range(4)):
        pass
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0) and all(0 <= f <= 1 for f in seen)
