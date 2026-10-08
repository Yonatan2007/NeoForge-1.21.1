import subprocess

import numpy as np
import pytest

from reelforge import look
from reelforge.config import ASPECTS, LookStyle, VideoStyle


def test_lab_round_trip_and_stats():
    rgb = np.random.default_rng(0).random((32, 32, 3))  # sRGB in 0..1
    back = look.lab_to_rgb(look.rgb_to_lab(rgb))
    assert np.abs(back - rgb).max() < 1e-6
    grey = np.full((8, 8, 3), 128, np.uint8)
    st = look.image_stats(grey)
    assert abs(st.mean[1]) < 0.5 and abs(st.mean[2]) < 0.5 and st.std[0] < 0.5


def test_combine_pools_statistics():
    a = look.LabStats((40.0, 0.0, 0.0), (5.0, 1.0, 1.0))
    b = look.LabStats((60.0, 0.0, 0.0), (5.0, 1.0, 1.0))
    c = look.combine([a, b])
    # the target spread is that of a single shot: differences between inputs are left out
    assert c.mean[0] == pytest.approx(50.0) and c.std[0] == pytest.approx(5.0)


def test_match_lut_moves_colours_toward_the_target(tmp_path):
    src = look.LabStats((40.0, 2.0, -6.0), (12.0, 4.0, 6.0))
    path = look.match_lut(src, look.REFERENCE_STATS, 1.0, tmp_path / "m.cube", size=9)
    text = path.read_text()
    assert "LUT_3D_SIZE 9" in text
    rows = [l for l in text.splitlines() if l and l[0].isdigit()]
    assert len(rows) == 9 ** 3
    identity = look.match_lut(src, src, 0.0, tmp_path / "id.cube", size=5).read_text()
    vals = [list(map(float, l.split())) for l in identity.splitlines() if l and l[0].isdigit()]
    grid = np.linspace(0, 1, 5)
    expected = [[r, g, b] for b in grid for g in grid for r in grid]  # .cube order: red fastest
    assert np.allclose(vals, expected, atol=0.02)


@pytest.mark.parametrize("aspect", sorted(ASPECTS))
def test_grade_filter_covers_and_crops_to_every_aspect(aspect):
    vs = VideoStyle(aspect=aspect)
    vf = look.grade_filter(LookStyle(), vs)
    assert f"crop={vs.width}:{vs.height}" in vf and vf.endswith("format=yuv420p")
    assert "force_original_aspect_ratio=increase" in vf


def test_prepare_clip_trims_sizes_and_caches(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25",
                    "-t", "3", str(src)], check=True)
    vs = VideoStyle(aspect="1:1", draft=True, fps=12)
    out = look.prepare_clip(src, tmp_path / "prep", vs, LookStyle(), look.REFERENCE_STATS,
                            trim_in=0.5, trim_out=2.0)
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                           "stream=width,height,r_frame_rate:format=duration", "-of", "csv=p=0",
                           str(out)], capture_output=True, text=True, check=True).stdout
    assert "540,540,12/1" in info
    assert float(info.strip().splitlines()[-1]) == pytest.approx(1.5, abs=0.15)
    mtime = out.stat().st_mtime_ns
    assert look.prepare_clip(src, tmp_path / "prep", vs, LookStyle(), look.REFERENCE_STATS,
                             trim_in=0.5, trim_out=2.0) == out and out.stat().st_mtime_ns == mtime


def test_target_stats_by_match_mode(tmp_path):
    assert look.target_stats(LookStyle(match="off"), []) is None
    assert look.target_stats(LookStyle(match="reference"), []) == look.REFERENCE_STATS
    assert look.target_stats(LookStyle(match="uploads"), []) == look.REFERENCE_STATS
