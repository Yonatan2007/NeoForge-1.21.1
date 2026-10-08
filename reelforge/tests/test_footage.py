import io
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests
from PIL import Image

from reelforge import footage
from reelforge.footage import (LUMA_TARGETS, Candidate, Mixkit, Pexels, Pixabay, fetch_footage,
                               rendition_rank, score, thumbnail_image)

FIXTURES = Path(__file__).parent / "fixtures" / "mixkit"
SITE = "https://mixkit.co/free-stock-video"

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


def jpeg(size, color=(140, 150, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def cand(id, width=1080, height=1920, **kw) -> Candidate:
    base = dict(provider="fake", page_url="", download_url=f"u{id}", duration=10, author="",
                thumbnail=None, query="q")
    return Candidate(id=str(id), width=width, height=height, **{**base, **kw})


class Resp:
    def __init__(self, code, body=b"", headers=None):
        self.status_code, self.content, self.headers = code, body, headers or {}

    @property
    def text(self):
        return self.content.decode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Site(requests.Session):
    """Serves recorded pages and pictures by URL (404 otherwise) and logs requests."""

    def __init__(self, routes):
        super().__init__()
        self.routes, self.log = routes, []

    def get(self, url, params=None, **kw):
        key = url + ("?" + urlencode(params) if params else "")
        self.log.append(key)
        body = self.routes.get(key)
        return Resp(404) if body is None else Resp(200, body)


class FakeProvider(footage._Provider):
    """Returns the same candidates for every query."""
    name = "fake"

    def __init__(self, cache_dir, cands, routes=None):
        super().__init__("", cache_dir, Site(routes or {}))
        self.cands = cands

    def search(self, query, per_page=15, allow_landscape=True):
        return [Candidate(**{**c.__dict__, "query": query, "alt_urls": list(c.alt_urls)})
                for c in self.cands]


# --------------------------------------------------------------------------- Pexels / Pixabay

def test_pexels_parse_picks_full_hd_portrait_with_fallbacks():
    (c,) = Pexels.parse(PEXELS, "rain")
    assert (c.download_url, c.width, c.height, c.author, c.query) == ("fhd", 1080, 1920, "Ann", "rain")
    assert c.alt_urls == ["uhd", "sd"]  # next-best renditions if the first download fails
    assert c.portrait and c.key == "pexels_1"


def test_pixabay_parse_prefers_4k_for_landscape_crop():
    (c,) = Pixabay.parse(PIXABAY, "fog")
    assert (c.download_url, c.height, c.thumbnail, c.alt_urls) == ("L", 2160, "t.jpg", ["M"])
    assert not c.portrait


# --------------------------------------------------------------------------- scoring

def test_rendition_rank_and_score_follow_palette_and_portrait_weight():
    assert rendition_rank(1080, 1920) < rendition_rank(2160, 3840) < rendition_rank(1920, 1080)
    dim, bright = cand("a", luma=0.2), cand("b", luma=0.5)
    assert score(dim, 5, luma_target=LUMA_TARGETS["moody"]) > score(bright, 5, luma_target=LUMA_TARGETS["moody"])
    assert score(bright, 5) > score(dim, 5)  # the default palette is the bright reference look
    landscape = cand("c", width=1920, height=1080, luma=0.5)
    assert score(bright, 5) - score(landscape, 5) == pytest.approx(3.0)
    assert score(bright, 5, portrait_weight=0.5) - score(landscape, 5, portrait_weight=0.5) == pytest.approx(0.5)
    assert score(cand("d", duration=3), 5) == score(cand("e"), 5) - 2.0  # too short for a shot


# --------------------------------------------------------------------------- thumbnails

def test_thumbnail_is_downloaded_once_and_bad_bytes_are_not_cached(tmp_path):
    site = Site({"https://cdn/t.jpg": jpeg((720, 1280)), "https://cdn/bad.jpg": b"<html>oops</html>"})
    assert thumbnail_image("https://cdn/t.jpg", site, tmp_path).size == (720, 1280)
    assert thumbnail_image("https://cdn/t.jpg", site, tmp_path).mode == "RGB"
    assert thumbnail_image("https://cdn/bad.jpg", site, tmp_path) is None
    assert thumbnail_image("https://cdn/missing.jpg", site, tmp_path) is None
    assert thumbnail_image(None, site, tmp_path) is None
    assert thumbnail_image("https://cdn/bad.jpg", site, tmp_path) is None
    assert site.log.count("https://cdn/t.jpg") == 1
    assert site.log.count("https://cdn/bad.jpg") == 2  # retried: nothing undecodable is cached


# --------------------------------------------------------------------------- Mixkit (recorded pages)

def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


SUNSET = f"{SITE}/discover/sunset%20mountains/"
MIXKIT_ROUTES = {
    SUNSET + "?orientation=vertical": fixture("discover_sunset_mountains_vertical.html"),
    SUNSET: fixture("discover_sunset_mountains_any.html"),
    f"{SITE}/discover/sunset%20mountains%20golden/?orientation=vertical": fixture("discover_no_results.html"),
    f"{SITE}/discover/sunset%20mountains%20golden/": fixture("discover_no_results.html"),
    f"{SITE}/the-sun-hiding-on-the-horizon-3133/": fixture("video_3133.html"),
    f"{SITE}/sunset-behind-mountains-2123/": fixture("video_2123.html"),
    f"{SITE}/road-between-tranquil-waters-and-mountains-at-sunset-100699/": fixture("video_100699.html"),
    "https://assets.mixkit.co/videos/3133/3133-thumb-720-0.jpg": jpeg((720, 1280), (200, 170, 110)),
    "https://assets.mixkit.co/videos/2123/2123-thumb-720-0.jpg": jpeg((1280, 720), (120, 120, 140)),
    "https://assets.mixkit.co/uurzr6k7jjj6qj5y9su8yixzce9c": jpeg((1280, 720), (110, 140, 170)),
    f"{SITE}/download/100699/?context=sidebar&type=2160p": fixture("download_100699.html"),
}


def mixkit(tmp_path, per_query=3):
    site = Site(MIXKIT_ROUTES)
    return Mixkit(tmp_path, session=site, per_query=per_query, delay=0), site


def test_mixkit_reads_video_object_and_keeps_free_license_only():
    obj = Mixkit.video_object(fixture("video_99841.html").decode(),
                              "colorful-rain-soaked-bokeh-lights-at-night-99841")
    assert obj["name"] == "Colorful rain-soaked bokeh lights at night"
    assert Mixkit.video_object(fixture("video_99841.html").decode(), "other-clip-5") is None
    restricted = dict(obj, license="https://mixkit.co/license/#videoRestricted")
    (c,) = Mixkit.parse({"videos": [obj, restricted]}, "rain")  # a cache entry without thumb_size
    assert (c.provider, c.id, c.duration, c.portrait) == ("mixkit", "99841", 23.0, True)
    assert c.download_url.endswith("/99841-video-1080.mp4")  # full HD first...
    assert [u.rsplit("/", 1)[1] for u in c.alt_urls] == ["99841-video-720.mp4"]  # ...720p as fallback
    assert c.page_url == "https://mixkit.co/free-stock-video/colorful-rain-soaked-bokeh-lights-at-night-99841/"


def test_mixkit_searches_vertical_first_then_any_orientation(tmp_path):
    m, site = mixkit(tmp_path)
    cands = {c.id: c for c in m.search("sunset mountains")}
    assert site.log[:2] == [SUNSET + "?orientation=vertical", SUNSET]  # 1 vertical hit < per_query
    assert set(cands) == {"3133", "2123", "100699"}

    v = cands["3133"]  # orientation comes from the thumbnail's size
    assert (v.portrait, v.width, v.height, v.thumb_size) == (True, 1080, 1920, (720, 1280))
    assert [v.download_url, *v.alt_urls] == ["https://assets.mixkit.co/videos/3133/3133-1080.mp4",
                                             "https://assets.mixkit.co/videos/3133/3133-720.mp4"]
    land = cands["2123"]
    assert (land.portrait, land.width, land.height) == (False, 1920, 1080)
    assert [land.download_url, *land.alt_urls] == [
        f"https://assets.mixkit.co/videos/2123/2123-{r}.mp4" for r in (2160, 1080, 720)]
    new = cands["100699"]  # opaque file names: HD/4K via the download page, 720p contentUrl last
    assert not new.portrait
    assert [new.download_url, *new.alt_urls] == [
        f"{SITE}/download/100699/?context=sidebar&type=2160p",
        f"{SITE}/download/100699/?context=sidebar&type=1080p",
        "https://assets.mixkit.co/2ww5hnd1qfphhofj5ekc9zee3qk2"]

    requests_made = len(site.log)
    assert {c.id for c in m.search("sunset mountains")} == set(cands)  # cached for a day
    assert len(site.log) == requests_made


def test_mixkit_portrait_only_skips_the_any_orientation_search(tmp_path):
    m, site = mixkit(tmp_path)
    assert [c.id for c in m.search("sunset mountains", allow_landscape=False)] == ["3133"]
    assert SUNSET not in site.log


def test_mixkit_drops_words_until_something_is_found(tmp_path):
    m, site = mixkit(tmp_path)
    assert m.search("sunset mountains golden")
    discover = [u for u in site.log if "/discover/" in u]
    assert discover == [f"{SITE}/discover/sunset%20mountains%20golden/?orientation=vertical",
                        f"{SITE}/discover/sunset%20mountains%20golden/",
                        SUNSET + "?orientation=vertical", SUNSET]


def test_mixkit_resolves_download_pages_to_files(tmp_path):
    m, _ = mixkit(tmp_path)
    assert m.resolve(f"{SITE}/download/100699/?context=sidebar&type=2160p") == \
        "https://assets.mixkit.co/9jzllbfetaw1vcjbw4vqhfphp6r9"
    assert m.resolve("https://assets.mixkit.co/x-720.mp4") == "https://assets.mixkit.co/x-720.mp4"
    with pytest.raises(requests.HTTPError):
        m.resolve(f"{SITE}/download/100699/?context=sidebar&type=1080p")  # not recorded: 404


def test_fetch_footage_reuses_mixkit_thumbnails_and_downloads_with_fallbacks(tmp_path, monkeypatch):
    got = []

    def fake_download(url, dst, **kw):
        got.append(url)
        if "2160" in url or "9jzllbfetaw1" in url:
            raise requests.HTTPError("403")
        return Path(dst)

    def extra(c, img):
        sizes.setdefault(c.id, img.size)
        return 0.0

    monkeypatch.setattr(footage, "download", fake_download)
    m, site = mixkit(tmp_path)
    sizes = {}
    picks = fetch_footage(["sunset mountains", "sunset mountains"], [m], tmp_path, min_duration=5,
                          portrait_weight=0.0, extra_score=extra)
    assert sizes == {"3133": (720, 1280), "2123": (1280, 720), "100699": (1280, 720)}
    assert sum("thumb" in u or "uurzr6k7" in u for u in site.log) == 3  # one fetch per thumbnail
    # With orientation ignored, brightness ranks 2123 > 100699 > 3133 (the warm one is too bright).
    assert [c.id for c, _ in picks] == ["2123", "100699"]
    assert got == ["https://assets.mixkit.co/videos/2123/2123-2160.mp4",   # 403 -> 1080p
                   "https://assets.mixkit.co/videos/2123/2123-1080.mp4",
                   "https://assets.mixkit.co/9jzllbfetaw1vcjbw4vqhfphp6r9",  # 4K via its download page
                   "https://assets.mixkit.co/2ww5hnd1qfphhofj5ekc9zee3qk2"]  # 1080p page 404 -> 720p


def test_provider_backs_off_on_429(tmp_path):
    class Flaky(requests.Session):
        def __init__(self):
            super().__init__()
            self.codes = [429, 503, 200]

        def get(self, url, **kw):
            return Resp(self.codes.pop(0), b"ok", {"Retry-After": "0"})

    assert Mixkit(tmp_path, session=Flaky(), delay=0)._get("https://mixkit.co/x") == "ok"


# --------------------------------------------------------------------------- fetch_footage

def test_fetch_footage_never_repeats_a_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "download", lambda url, dst, **kw: Path(dst))
    p = FakeProvider(tmp_path, [cand(0), cand(1, duration=11)])
    picks = fetch_footage(["a", "b", "c"], [p], tmp_path, min_duration=5)
    assert [c.key for c, _ in picks] == ["fake_0", "fake_1"]  # third query has nothing new


def test_fetch_footage_without_providers_explains_setup(tmp_path):
    with pytest.raises(footage.FootageError, match="PEXELS_API_KEY"):
        fetch_footage(["a"], [], tmp_path, min_duration=5)


def test_fetch_footage_stops_at_count_and_falls_back_to_alt_urls(tmp_path, monkeypatch):
    tried = []

    def fake_download(url, dst, **kw):
        tried.append(url)
        if not url.endswith("720"):
            raise requests.HTTPError("404")
        return Path(dst)

    class OneEach(FakeProvider):
        def search(self, query, per_page=15, allow_landscape=True):
            return [cand(query, download_url="uhd-2160", alt_urls=["hd-1080", "sd-720"], query=query)]

    monkeypatch.setattr(footage, "download", fake_download)
    picks = fetch_footage(["a", "b", "c"], [OneEach(tmp_path, [])], tmp_path, min_duration=5, count=2)
    assert [c.id for c, _ in picks] == ["a", "b"]
    assert tried[:3] == ["uhd-2160", "hd-1080", "sd-720"]


def test_fetch_footage_takes_the_next_clip_when_a_download_fails(tmp_path, monkeypatch):
    def fake_download(url, dst, **kw):
        if url == "u0":
            raise requests.ConnectionError("reset")
        return Path(dst)

    monkeypatch.setattr(footage, "download", fake_download)
    p = FakeProvider(tmp_path, [cand(0, duration=30), cand(1)])
    (pick,) = fetch_footage(["a"], [p], tmp_path, min_duration=5)
    assert pick[0].id == "1"


def test_extra_score_sees_the_thumbnail_and_can_change_the_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "download", lambda url, dst, **kw: Path(dst))
    routes = {"https://cdn/sky.jpg": jpeg((720, 1280), (120, 160, 220))}
    p = FakeProvider(tmp_path, [cand("plain"), cand("sky", duration=3, thumbnail="https://cdn/sky.jpg")],
                     routes)
    seen = {}

    def extra(c, img):
        seen[c.id] = img
        return 10.0 if img is not None and img.getpixel((0, 0))[2] > 200 else 0.0

    (pick,) = fetch_footage(["a"], [p], tmp_path, min_duration=5, extra_score=extra)
    assert pick[0].id == "sky" and pick[0].thumb_size == (720, 1280)
    assert seen["plain"] is None and seen["sky"].size == (720, 1280)
    (pick,) = fetch_footage(["a"], [p], tmp_path / "again", min_duration=5)
    assert pick[0].id == "plain"  # without the visual bonus the long-enough clip wins


def test_portrait_weight_lets_landscape_win_for_wide_output(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "download", lambda url, dst, **kw: Path(dst))
    p = FakeProvider(tmp_path, [cand("tall", duration=4), cand("wide", width=1920, height=1080, duration=30)])
    assert fetch_footage(["a"], [p], tmp_path, min_duration=5)[0][0].id == "tall"
    assert fetch_footage(["a"], [p], tmp_path, min_duration=5, portrait_weight=0.0)[0][0].id == "wide"
    assert fetch_footage(["a"], [p], tmp_path, min_duration=5, allow_landscape=False)[0][0].id == "tall"


def test_credits_list_every_clip(tmp_path):
    c = cand(1, provider="mixkit", page_url="https://mixkit.co/v-1/", author="Mixkit", thumb_size=(720, 1280))
    path = footage.write_credits([(c, tmp_path / "c.mp4")], tmp_path / "credits.txt")
    assert "https://mixkit.co/v-1/" in path.read_text()
    assert json.loads(path.with_suffix(".json").read_text())[0]["thumb_size"] == [720, 1280]
