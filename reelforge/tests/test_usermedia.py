import subprocess

import numpy as np
import pytest
from PIL import Image

from reelforge import usermedia


def colour(path, rgb, size=(640, 360)):
    Image.new("RGB", size, rgb).save(path)
    return path


def test_media_kind_and_info(tmp_path):
    img = colour(tmp_path / "a.png", (10, 20, 30))
    vid = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=10",
                    "-t", "1", str(vid)], check=True)
    (tmp_path / "s.txt").write_text("hi")
    assert usermedia.media_kind(img) == "image" and usermedia.media_kind(vid) == "video"
    assert usermedia.media_kind(tmp_path / "s.txt") == "text"
    info = usermedia.media_info(vid)
    assert info["kind"] == "video" and (info["width"], info["height"]) == (320, 240)
    assert info["duration"] == pytest.approx(1.0, abs=0.15)
    thumb = usermedia.thumbnail(vid, tmp_path / "t.jpg", max_side=120)
    assert max(Image.open(thumb).size) <= 120


@pytest.mark.parametrize("size,motion", [((1600, 900), "auto"), ((400, 1200), "zoom-in"),
                                         ((300, 300), "pan-left")])
def test_image_to_video_cover_fits_without_letterboxing(tmp_path, size, motion):
    rng = np.random.default_rng(3)
    Image.fromarray(rng.integers(60, 200, (size[1], size[0], 3), dtype=np.uint8)).save(tmp_path / "p.jpg")
    out = usermedia.image_to_video(tmp_path / "p.jpg", tmp_path / "clips", 1.0, motion, 270, 480, 12)
    info = usermedia.media_info(out)
    assert (info["width"], info["height"]) == (270, 480)
    frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    arr = np.frombuffer(frame, np.uint8).reshape(480, 270, 3)
    assert arr[:4].mean() > 30 and arr[-4:].mean() > 30  # no black bars
    assert usermedia.image_to_video(tmp_path / "p.jpg", tmp_path / "clips", 1.0, motion, 270, 480, 12) == out


def test_signatures_rank_similar_scenes_higher(tmp_path):
    sunset = usermedia.signature(colour(tmp_path / "s.png", (235, 140, 60)))
    sunset2 = usermedia.signature(colour(tmp_path / "s2.png", (225, 150, 70)))
    lake = usermedia.signature(colour(tmp_path / "l.png", (40, 150, 160)))
    night = usermedia.signature(colour(tmp_path / "n.png", (8, 10, 20)))
    assert usermedia.similarity(sunset, sunset2) > usermedia.similarity(sunset, lake)
    assert usermedia.similarity(sunset, sunset2) > usermedia.similarity(sunset, night)
    thumb = Image.new("RGB", (64, 64), (230, 145, 65))
    assert usermedia.reference_score(thumb, [lake, sunset]) == pytest.approx(
        max(usermedia.similarity(usermedia.signature(thumb), r) for r in (lake, sunset)), abs=1e-6)
    assert usermedia.reference_score(None, [sunset]) == 0.0
    assert any("sunset" in t or "golden" in t for t in usermedia.palette_terms([sunset]))


def test_builtin_reference_signatures_are_shipped():
    refs = usermedia.builtin_reference_signatures()
    assert len(refs) >= 10 and all(r.shape == (usermedia.SIGNATURE_SIZE,) for r in refs)
