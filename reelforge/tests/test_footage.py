from pathlib import Path

import pytest

from reelforge import footage
from reelforge.config import VideoStyle
from reelforge.footage import Candidate, Pexels, Pixabay, fetch_footage, rendition_rank, score

PEXELS = {"videos": [{
    "id": 1, "url": "https://www.pexels.com/video/1/", "duration": 12, "image": "thumb.jpg",
    "user": {"name": "Ann"},
    "video_files": [
        {"file_type": "video/mp4", "width": 2160, "height": 3840, "link": "uhd"},
        {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "fhd"},
        {"file_type": "video/mp4", "width": 540, "height": 960, "link": "sd"},
    ]}, {"id": 2, "video_files": []}]}

PIXABAY = {"hits": [{
    "id": 7, "pageURL": "https://pixabay.com/videos/id-7/", "duration": 9, "user": "Bo",
    "videos": {
        "large": {"url": "L", "width": 3840, "height": 2160, "thumbnail": "t.jpg"},
        "medium": {"url": "M", "width": 1920, "height": 1080, "thumbnail": "t2.jpg"},
        "tiny": {"url": "", "width": 0, "height": 0},
    }}]}


def test_pexels_parse_picks_full_hd_portrait():
    (c,) = Pexels.parse(PEXELS, "rain")
    assert (c.download_url, c.width, c.height, c.author, c.query) == ("fhd", 1080, 1920, "Ann", "rain")
    assert c.portrait and c.key == "pexels_1"


def test_pixabay_parse_prefers_4k_for_landscape_crop():
    (c,) = Pixabay.parse(PIXABAY, "fog")
    assert (c.download_url, c.height, c.thumbnail) == ("L", 2160, "t.jpg")
    assert not c.portrait


def test_rendition_rank_and_mood_score():
    assert rendition_rank(1080, 1920) < rendition_rank(2160, 3840) < rendition_rank(1920, 1080)
    base = dict(provider="p", page_url="", download_url="", author="", thumbnail=None, query="q")
    dim = Candidate(id="a", width=1080, height=1920, duration=10, luma=0.2, **base)
    bright = Candidate(id="b", width=1080, height=1920, duration=10, luma=0.7, **base)
    landscape = Candidate(id="c", width=1920, height=1080, duration=10, luma=0.2, **base)
    assert score(dim, 5) > score(bright, 5)
    assert score(dim, 5) > score(landscape, 5)


class FakeProvider:
    name = "fake"

    def __init__(self):
        import requests
        self.session = requests.Session()

    def search(self, query, per_page=15):
        base = dict(provider="fake", page_url="", author="", thumbnail=None, query=query)
        return [Candidate(id=str(i), download_url=f"u{i}", width=1080, height=1920,
                          duration=10 + i, **base) for i in range(2)]


def test_fetch_footage_never_repeats_a_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "download", lambda url, dst, **kw: Path(dst))
    picks = fetch_footage(["a", "b", "c"], [FakeProvider()], tmp_path, min_duration=5)
    assert [c.key for c, _ in picks] == ["fake_0", "fake_1"]  # third query has nothing new


def test_fetch_footage_without_providers_explains_setup(tmp_path):
    with pytest.raises(footage.FootageError, match="PEXELS_API_KEY"):
        fetch_footage(["a"], [], tmp_path, min_duration=5)


def test_grade_filter_crops_to_vertical():
    vf = footage.grade_filter(VideoStyle())
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in vf
    assert "crop=1080:1920" in vf and vf.endswith("format=yuv420p")


MIXKIT_PAGE = """<html><script type="application/ld+json" data-test-id="schema_org_data-0">
{"@context":"https://schema.org","@graph":[
 {"@type":"VideoObject","@id":"https://mixkit.co/free-stock-video/other-clip-5/#video","name":"x"},
 {"@type":"VideoObject","@id":"https://mixkit.co/free-stock-video/rain-bokeh-at-night-99841/#video",
  "name":"Rain bokeh at night","license":"https://mixkit.co/license/#videoFree","duration":"PT0M23S",
  "thumbnailUrl":"https://assets.mixkit.co/t.jpg",
  "contentUrl":"https://assets.mixkit.co/videos/99841/99841-video-720.mp4"}]}
</script></html>"""


def test_mixkit_reads_video_object_and_keeps_free_license_only():
    obj = footage.Mixkit.video_object(MIXKIT_PAGE, "rain-bokeh-at-night-99841")
    assert obj["name"] == "Rain bokeh at night"
    restricted = dict(obj, license="https://mixkit.co/license/#videoRestricted")
    (c,) = footage.Mixkit.parse({"videos": [obj, restricted]}, "rain")
    assert (c.provider, c.id, c.duration, c.portrait) == ("mixkit", "99841", 23.0, True)
    assert c.download_url.endswith("99841-video-1080.mp4")  # full HD first...
    assert c.alt_url.endswith("99841-video-720.mp4")        # ...720p as fallback
    assert c.page_url == "https://mixkit.co/free-stock-video/rain-bokeh-at-night-99841/"


def test_fetch_footage_stops_at_count_and_falls_back_to_alt_url(tmp_path, monkeypatch):
    import requests

    def fake_download(url, dst, **kw):
        if url.endswith("1080"):
            raise requests.HTTPError("404")
        return Path(dst)

    class OneEach(FakeProvider):
        def search(self, query, per_page=15):
            base = dict(provider="fake", page_url="", author="", thumbnail=None, query=query)
            return [Candidate(id=query, download_url="hd-1080", alt_url="sd-720", width=1080,
                              height=1920, duration=10, **base)]

    monkeypatch.setattr(footage, "download", fake_download)
    picks = fetch_footage(["a", "b", "c"], [OneEach()], tmp_path, min_duration=5, count=2)
    assert [c.id for c, _ in picks] == ["a", "b"]


def test_provider_backs_off_on_429(tmp_path):
    import requests

    class Resp:
        def __init__(self, code):
            self.status_code, self.headers, self.text = code, {"Retry-After": "0"}, "ok"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    class Session(requests.Session):
        def __init__(self):
            super().__init__()
            self.codes = [429, 503, 200]

        def get(self, url, **kw):
            return Resp(self.codes.pop(0))

    m = footage.Mixkit(tmp_path, session=Session(), delay=0)
    assert m._get("https://mixkit.co/x") == "ok"
