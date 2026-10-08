"""Stock footage: search Pexels and Pixabay, score candidates for mood and
fit, download, and normalise each clip to a graded 1080x1920 H.264 file."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

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
        r = self.session.get(self.url, timeout=30, headers={"Authorization": self.key},
                             params={"query": query, "orientation": "portrait",
                                     "size": "medium", "per_page": per_page})
        r.raise_for_status()
        return r.json()

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
        r = self.session.get(self.url, timeout=30,
                             params={"key": self.key, "q": query[:100], "video_type": "film",
                                     "safesearch": "true", "per_page": max(3, per_page)})
        r.raise_for_status()
        return r.json()

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
                  shortlist: int = 6) -> list[tuple[Candidate, Path]]:
    """One clip per query, never the same clip twice."""
    if not providers:
        raise FootageError("no stock provider configured: set PEXELS_API_KEY and/or "
                           "PIXABAY_API_KEY, or pass --footage-dir with your own clips")
    used: set[str] = set()
    picks: list[tuple[Candidate, Path]] = []
    for query in queries:
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
        path = download(best.download_url, cache_dir / "footage" / f"{best.key}.mp4", timeout=120)
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
    lines = ["Stock footage used (Pexels / Pixabay licences, attribution appreciated):", ""]
    for c, _ in picks:
        lines.append(f"- {c.provider}: {c.author or 'unknown'} - {c.page_url}  [query: {c.query}]")
    path.write_text("\n".join(lines) + "\n")
    (path.with_suffix(".json")).write_text(json.dumps([asdict(c) for c, _ in picks], indent=2))
    return path
