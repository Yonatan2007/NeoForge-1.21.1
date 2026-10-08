"""Stock footage: search Mixkit (no key needed), Pexels and Pixabay, score
candidates for mood and fit, download, and normalise each clip to a graded
1080x1920 H.264 file."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageStat

from .config import VideoStyle
from .media import USER_AGENT, download, run

log = logging.getLogger(__name__)
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
SEARCH_TTL = 24 * 3600  # Pixabay asks API users to cache results for 24 h


class FootageError(RuntimeError):
    pass


@dataclass
class Candidate:
    provider: str
    id: str
    page_url: str
    download_url: str
    width: int
    height: int
    duration: float
    author: str
    thumbnail: str | None
    query: str
    luma: float | None = None
    alt_url: str | None = None  # fallback download (e.g. 720p when 1080p is missing)

    @property
    def key(self) -> str:
        return f"{self.provider}_{self.id}"

    @property
    def portrait(self) -> bool:
        return self.height > self.width


def rendition_rank(width: int, height: int) -> tuple:
    """Lower is better. Portrait files closest to 1080 wide win (full
    resolution without a 4K download). Landscape files need ~2160 px of
    height to survive a 9:16 centre crop without upscaling."""
    if height > width:
        return (0, 0 if width >= 1080 else 1, abs(width - 1080))
    return (1, 0 if height >= 1920 else 1, abs(height - 2160))


def score(c: Candidate, min_duration: float) -> float:
    """Heuristic fit for the moody 9:16 look."""
    s = 3.0 if c.portrait else 0.0
    s += 1.0 if min(c.width, c.height) >= 1080 else 0.0
    s += 1.0 if c.duration >= min_duration else -1.0
    if c.luma is not None:
        # Dim but not black: peak at 22% mean luminance.
        s += 2.5 * (1.0 - abs(c.luma - 0.22) / 0.5)
    return s


class _Provider:
    name = ""

    def __init__(self, key: str, cache_dir: Path, session: requests.Session | None = None):
        self.key = key
        self.cache_dir = cache_dir / "search"
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def _fetch(self, url: str, tries: int = 4, **kwargs) -> requests.Response:
        """GET with back-off on 429/5xx, honouring Retry-After."""
        for attempt in range(tries):
            r = self.session.get(url, timeout=30, **kwargs)
            retryable = r.status_code == 429 or r.status_code >= 500
            if not retryable or attempt == tries - 1:
                break
            wait = r.headers.get("Retry-After", "")
            time.sleep(float(wait) if wait.isdigit() else 5.0 * (attempt + 1))
        r.raise_for_status()
        return r

    def search(self, query: str, per_page: int = 15) -> list[Candidate]:
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
        cache = self.cache_dir / f"{self.name}_{slug}_{per_page}.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < SEARCH_TTL:
            data = json.loads(cache.read_text())
        else:
            data = self._request(query, per_page)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
        return self.parse(data, query)

    def _request(self, query: str, per_page: int) -> dict:
        raise NotImplementedError

    @staticmethod
    def parse(data: dict, query: str) -> list[Candidate]:
        raise NotImplementedError


class Pexels(_Provider):
    """https://www.pexels.com/api/documentation/#videos-search"""
    name = "pexels"
    url = "https://api.pexels.com/v1/videos/search"

    def _request(self, query: str, per_page: int) -> dict:
        return self._fetch(self.url, headers={"Authorization": self.key},
                           params={"query": query, "orientation": "portrait",
                                   "size": "medium", "per_page": per_page}).json()

    @staticmethod
    def parse(data: dict, query: str) -> list[Candidate]:
        out = []
        for v in data.get("videos", []):
            files = [f for f in v.get("video_files", [])
                     if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")]
            if not files:
                continue
            best = min(files, key=lambda f: rendition_rank(f["width"], f["height"]))
            out.append(Candidate("pexels", str(v["id"]), v.get("url", ""), best["link"],
                                 int(best["width"]), int(best["height"]), float(v.get("duration") or 0),
                                 (v.get("user") or {}).get("name", ""), v.get("image"), query))
        return out


class Pixabay(_Provider):
    """https://pixabay.com/api/docs/#api_search_videos"""
    name = "pixabay"
    url = "https://pixabay.com/api/videos/"

    def _request(self, query: str, per_page: int) -> dict:
        return self._fetch(self.url, params={"key": self.key, "q": query[:100], "video_type": "film",
                                             "safesearch": "true", "per_page": max(3, per_page)}).json()

    @staticmethod
    def parse(data: dict, query: str) -> list[Candidate]:
        out = []
        for hit in data.get("hits", []):
            files = [f for f in (hit.get("videos") or {}).values()
                     if f.get("url") and f.get("width") and f.get("height")]
            if not files:
                continue
            best = min(files, key=lambda f: rendition_rank(f["width"], f["height"]))
            thumb = best.get("thumbnail") or (hit.get("videos") or {}).get("medium", {}).get("thumbnail")
            out.append(Candidate("pixabay", str(hit["id"]), hit.get("pageURL", ""), best["url"],
                                 int(best["width"]), int(best["height"]), float(hit.get("duration") or 0),
                                 hit.get("user", ""), thumb, query))
        return out


class Mixkit(_Provider):
    """Keyless free stock video from https://mixkit.co, vertical clips only.

    Only items under the Mixkit Stock Video *Free* License are used: free for
    commercial work, social media and ads, no attribution required. Items under
    the *Restricted* License (personal, non-monetised use only) are skipped."""
    name = "mixkit"
    base = "https://mixkit.co"

    def __init__(self, cache_dir: Path, session: requests.Session | None = None,
                 per_query: int = 8, delay: float = 1.0):
        super().__init__("", cache_dir, session)
        self.per_query, self.delay = per_query, delay

    def _get(self, url: str, **kwargs) -> str:
        time.sleep(self.delay)  # be polite: a search costs ~1 + per_query page loads
        return self._fetch(url, **kwargs).text

    def _request(self, query: str, per_page: int) -> dict:
        words, slugs = query.split(), []
        while words and not slugs:  # "clock ticking dark" -> "clock ticking" -> "clock"
            page = self._get(f"{self.base}/free-stock-video/discover/{quote(' '.join(words))}/",
                             params={"orientation": "vertical"})
            slugs = list(dict.fromkeys(re.findall(r'href="/free-stock-video/([a-z0-9-]+-\d+)/"', page)))
            words = words[:-1]
        videos = []
        for slug in slugs[: self.per_query]:
            obj = self.video_object(self._get(f"{self.base}/free-stock-video/{slug}/"), slug)
            if obj:
                videos.append(obj)
        return {"videos": videos}

    @staticmethod
    def video_object(page: str, slug: str) -> dict | None:
        """The schema.org VideoObject describing ``slug`` on its detail page."""
        for blk in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                data = json.loads(blk)
            except ValueError:
                continue
            for item in data.get("@graph", []) if isinstance(data, dict) else []:
                if item.get("@type") == "VideoObject" and f"/{slug}/" in item.get("@id", ""):
                    return item
        return None

    @staticmethod
    def parse(data: dict, query: str) -> list[Candidate]:
        out = []
        for v in data.get("videos", []):
            url = v.get("contentUrl") or ""
            if not v.get("license", "").endswith("#videoFree") or not url.endswith(".mp4"):
                continue
            m = re.search(r"-(\d+)/", v.get("@id", ""))
            d = re.fullmatch(r"PT(?:(\d+)M)?(?:(\d+)S)?", v.get("duration") or "")
            seconds = int(d.group(1) or 0) * 60 + int(d.group(2) or 0) if d else 0
            hd = re.sub(r"-720\.mp4$", "-1080.mp4", url)
            # Results come from the vertical filter, so treat them as 1080x1920.
            out.append(Candidate("mixkit", m.group(1) if m else url, v["@id"].split("#")[0], hd,
                                 1080, 1920, float(seconds), "Mixkit", v.get("thumbnailUrl"), query,
                                 alt_url=url if hd != url else None))
        return out


def thumbnail_luma(url: str | None, session: requests.Session) -> float | None:
    if not url:
        return None
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("L")
        img.thumbnail((64, 64))
        return ImageStat.Stat(img).mean[0] / 255.0
    except (requests.RequestException, OSError):
        return None


def fetch_footage(queries: list[str], providers: list[_Provider], cache_dir: Path,
                  min_duration: float, allow_landscape: bool = True,
                  shortlist: int = 6, count: int | None = None) -> list[tuple[Candidate, Path]]:
    """One clip per query (in order) until ``count`` clips, never the same clip twice."""
    if not providers:
        raise FootageError("no stock provider configured: enable Mixkit, set PEXELS_API_KEY "
                           "and/or PIXABAY_API_KEY, or pass --footage-dir with your own clips")
    used: set[str] = set()
    picks: list[tuple[Candidate, Path]] = []
    for query in queries:
        if count is not None and len(picks) >= count:
            break
        cands: list[Candidate] = []
        for p in providers:
            try:
                cands += p.search(query)
            except requests.RequestException as exc:
                log.warning("%s search failed for %r: %s", p.name, query, exc)
        cands = [c for c in cands if c.key not in used and (c.portrait or allow_landscape)]
        if not cands:
            log.warning("no footage for %r", query)
            continue
        cands.sort(key=lambda c: score(c, min_duration), reverse=True)
        top = cands[:shortlist]
        for c in top:
            c.luma = thumbnail_luma(c.thumbnail, providers[0].session)
        best = max(top, key=lambda c: score(c, min_duration))
        used.add(best.key)
        dst = cache_dir / "footage" / f"{best.key}.mp4"
        try:
            path = download(best.download_url, dst, timeout=120)
        except requests.HTTPError:
            if not best.alt_url:
                raise
            path = download(best.alt_url, dst, timeout=120)
        log.info("footage %-28s <- %s (%dx%d, %.0fs)", query, best.key, best.width, best.height, best.duration)
        picks.append((best, path))
    if not picks:
        raise FootageError("stock search returned nothing usable")
    return picks


def local_footage(directory: Path) -> list[Path]:
    clips = sorted(p for p in Path(directory).iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not clips:
        raise FootageError(f"no video files in {directory}")
    return clips


def grade_filter(vs: VideoStyle) -> str:
    """Cover-scale and centre-crop to 9:16, conform frame rate, then grade:
    lower saturation and brightness, more contrast, vignette, split-tone
    (cool shadows, warm highlights) and film grain."""
    w, h = vs.width, vs.height
    chain = [
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={w}:{h}",
        "setsar=1",
        f"fps={vs.fps}",
        f"eq=contrast={vs.contrast}:brightness={vs.brightness}:saturation={vs.saturation}:gamma={vs.gamma}",
        f"vignette=angle={vs.vignette}",
    ]
    if vs.tint:
        chain.append(f"colorbalance={vs.tint}")
    if vs.grain:
        chain.append(f"noise=alls={vs.grain}:allf=t")
    chain.append("format=yuv420p")
    return ",".join(chain)


def prepare_clip(src: Path, out_dir: Path, vs: VideoStyle) -> Path:
    vf = grade_filter(vs)
    st = src.stat()  # same-named clips from different folders must not collide
    key = f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}|{vf}|{vs.max_clip_seconds}"
    tag = hashlib.sha1(key.encode()).hexdigest()[:10]
    dst = out_dir / f"{src.stem}_{tag}.mp4"
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", str(vs.max_clip_seconds), "-an",
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", str(dst)])
    return dst


def write_credits(picks: list[tuple[Candidate, Path]], path: Path) -> Path:
    lines = ["Stock footage used (Mixkit Free / Pexels / Pixabay licences; credit appreciated):", ""]
    for c, _ in picks:
        lines.append(f"- {c.provider}: {c.author or 'unknown'} - {c.page_url}  [query: {c.query}]")
    path.write_text("\n".join(lines) + "\n")
    (path.with_suffix(".json")).write_text(json.dumps([asdict(c) for c, _ in picks], indent=2))
    return path
