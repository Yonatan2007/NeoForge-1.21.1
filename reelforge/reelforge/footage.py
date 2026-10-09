"""Stock footage: search Mixkit (no key needed), Pexels and Pixabay, rank
the candidates (orientation, resolution, length, brightness, plus any visual
judgement the caller adds from the thumbnail) and download the best clip per
search. Cropping and colour grading happen later, in ``look.prepare_clip``."""
from __future__ import annotations

import hashlib
import html
import io
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests
from PIL import Image, ImageStat

from .media import USER_AGENT, download

log = logging.getLogger(__name__)
SEARCH_TTL = 24 * 3600  # Pixabay asks API users to cache results for 24 h

# Mean thumbnail brightness (0..1) each footage palette aims for. The
# reference reel's sunny footage averages about 0.5 (graded frames measured
# at 0.51; raw stock is a little darker before the lift), moody night and
# rain B-roll about 0.2.
LUMA_TARGETS = {"bright": 0.48, "moody": 0.22}


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
    alt_urls: list[str] = field(default_factory=list)  # fallback downloads, tried in order
    thumb_size: tuple[int, int] | None = None           # pixel size of the thumbnail image

    @property
    def key(self) -> str:
        return f"{self.provider}_{self.id}"

    @property
    def portrait(self) -> bool:
        return self.height > self.width


ExtraScore = Callable[[Candidate, Image.Image | None], float]


def describes(c: Candidate, words: set[str] | None) -> bool:
    """True when the clip's page address (which names what it shows, e.g.
    ".../foggy-sky-during-a-starry-night-in-the-forest-30910/") mentions one
    of ``words`` (or its plural)."""
    if not words:
        return False
    slug = set(re.findall(r"[a-z]+", c.page_url.lower().split("://", 1)[-1].split("/", 1)[-1]))
    return any(w in slug or f"{w}s" in slug for w in words)


def rendition_rank(width: int, height: int) -> tuple:
    """Lower is better. Portrait files closest to 1080 wide win (full
    resolution without a 4K download). Landscape files need ~2160 px of
    height to survive a 9:16 centre crop without upscaling."""
    if height > width:
        return (0, 0 if width >= 1080 else 1, abs(width - 1080))
    return (1, 0 if height >= 1920 else 1, abs(height - 2160))


def score(c: Candidate, min_duration: float, portrait_weight: float = 3.0,
          luma_target: float = LUMA_TARGETS["bright"]) -> float:
    """Heuristic fit of a clip: portrait orientation (worth ``portrait_weight``,
    which the caller lowers for square output and makes negative for wide
    output, so landscape clips win there), a full-HD source,
    enough length for a shot, and thumbnail brightness near ``luma_target``."""
    s = portrait_weight if c.portrait else 0.0
    s += 1.0 if min(c.width, c.height) >= 1080 else 0.0
    s += 1.0 if c.duration >= min_duration else -1.0
    if c.luma is not None:
        s += 2.5 * (1.0 - abs(c.luma - luma_target) / 0.5)
    return s


def thumbnail_image(url: str | None, session: requests.Session,
                    cache_dir: Path) -> Image.Image | None:
    """The picture at ``url`` as RGB, downloaded only once: the bytes are kept
    in ``cache_dir`` (keyed by URL) so the orientation check during a search
    and the scoring afterwards share one download. None if unavailable."""
    if not url:
        return None
    path = Path(cache_dir) / f"{hashlib.sha1(url.encode()).hexdigest()[:20]}.img"
    try:
        if path.is_file():
            data = path.read_bytes()
        else:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            data = r.content
        with Image.open(io.BytesIO(data)) as img:
            rgb = img.convert("RGB")
    except (requests.RequestException, OSError):
        return None  # includes undecodable bytes (Pillow raises an OSError subclass)
    if not path.is_file():  # cache only what decoded, so a bad response is retried
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return rgb


def image_luma(img: Image.Image) -> float:
    """Mean brightness 0..1 of a picture."""
    small = img.convert("L")
    small.thumbnail((64, 64))
    return ImageStat.Stat(small).mean[0] / 255.0


class _Provider:
    name = ""

    def __init__(self, key: str, cache_dir: Path, session: requests.Session | None = None):
        self.key = key
        self.cache_dir = Path(cache_dir) / "search"
        self.thumb_dir = Path(cache_dir) / "thumbs"
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

    def search(self, query: str, per_page: int = 15, allow_landscape: bool = True,
               wide: bool = False) -> list[Candidate]:
        """Candidates for ``query``, cached for a day. ``allow_landscape``
        lets a provider widen a thin portrait search to other orientations;
        ``wide`` (16:9 output) asks for landscape clips instead."""
        # the slug keeps cache files readable; the hash keeps every query apart
        # (non-Latin searches have an empty slug) and the name short
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:50]
        digest = hashlib.sha1(query.encode()).hexdigest()[:10]
        variant = ("_wide" if wide else "") + ("" if allow_landscape else "_portrait")
        cache = self.cache_dir / f"{self.name}_{slug}_{digest}_{per_page}{variant}.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < SEARCH_TTL:
            data = json.loads(cache.read_text())
        else:
            data = self._request(query, per_page, allow_landscape, wide)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
        return self.parse(data, query)

    def resolve(self, url: str) -> str:
        """The direct file URL behind a candidate's download URL."""
        return url

    def _request(self, query: str, per_page: int, allow_landscape: bool) -> dict:
        raise NotImplementedError

    @staticmethod
    def parse(data: dict, query: str) -> list[Candidate]:
        raise NotImplementedError


def _ranked_files(files: list[dict], w: str, h: str) -> list[dict]:
    """Renditions with a size, best first (see ``rendition_rank``)."""
    files = [f for f in files if f.get(w) and f.get(h)]
    return sorted(files, key=lambda f: rendition_rank(int(f[w]), int(f[h])))


class Pexels(_Provider):
    """https://www.pexels.com/api/documentation/#videos-search"""
    name = "pexels"
    url = "https://api.pexels.com/v1/videos/search"

    def _request(self, query: str, per_page: int, allow_landscape: bool, wide: bool = False) -> dict:
        # Pexels has plenty of video in both orientations, so the filter always applies.
        return self._fetch(self.url, headers={"Authorization": self.key},
                           params={"query": query, "orientation": "landscape" if wide else "portrait",
                                   "size": "medium", "per_page": per_page}).json()

    @staticmethod
    def parse(data: dict, query: str) -> list[Candidate]:
        out = []
        for v in data.get("videos", []):
            files = _ranked_files([f for f in v.get("video_files", [])
                                   if f.get("file_type") == "video/mp4" and f.get("link")],
                                  "width", "height")
            if not files:
                continue
            best = files[0]
            out.append(Candidate("pexels", str(v["id"]), v.get("url", ""), best["link"],
                                 int(best["width"]), int(best["height"]), float(v.get("duration") or 0),
                                 (v.get("user") or {}).get("name", ""), v.get("image"), query,
                                 alt_urls=[f["link"] for f in files[1:]]))
        return out


class Pixabay(_Provider):
    """https://pixabay.com/api/docs/#api_search_videos"""
    name = "pixabay"
    url = "https://pixabay.com/api/videos/"

    def _request(self, query: str, per_page: int, allow_landscape: bool, wide: bool = False) -> dict:
        # Pixabay has no orientation filter: every result comes back, ranked later.
        return self._fetch(self.url, params={"key": self.key, "q": query[:100], "video_type": "film",
                                             "safesearch": "true", "per_page": max(3, per_page)}).json()

    @staticmethod
    def parse(data: dict, query: str) -> list[Candidate]:
        out = []
        for hit in data.get("hits", []):
            files = _ranked_files([f for f in (hit.get("videos") or {}).values() if f.get("url")],
                                  "width", "height")
            if not files:
                continue
            best = files[0]
            thumb = best.get("thumbnail") or (hit.get("videos") or {}).get("medium", {}).get("thumbnail")
            out.append(Candidate("pixabay", str(hit["id"]), hit.get("pageURL", ""), best["url"],
                                 int(best["width"]), int(best["height"]), float(hit.get("duration") or 0),
                                 hit.get("user", ""), thumb, query,
                                 alt_urls=[f["url"] for f in files[1:]]))
        return out


class Mixkit(_Provider):
    """Keyless free stock video from https://mixkit.co, vertical clips first.

    Only items under the Mixkit Stock Video *Free* License are used: free for
    commercial work, social media and ads, no attribution required. Items under
    the *Restricted* License (personal, non-monetised use only) are skipped.

    A search reads the vertical results first and, when they are thin and
    landscape is allowed, adds results of any orientation. Mixkit's listings
    don't say which is which, so each clip's thumbnail (same aspect as the
    video) is fetched once and its size decides. mixkit.co rate-limits bursts,
    so every page load waits ``delay`` seconds; thumbnails and video files
    come from the assets CDN and don't count."""
    name = "mixkit"
    base = "https://mixkit.co"
    PORTRAIT_LADDER = (1080, 720)        # 1080x1920 is full resolution for 9:16
    LANDSCAPE_LADDER = (2160, 1080, 720)  # a 9:16 crop of 4K still has 1215x2160 px

    def __init__(self, cache_dir: Path, session: requests.Session | None = None,
                 per_query: int = 8, delay: float = 1.0):
        super().__init__("", cache_dir, session)
        self.per_query, self.delay = per_query, delay

    def _get(self, url: str, **kwargs) -> str:
        time.sleep(self.delay)  # be polite: a search costs ~2 + per_query page loads
        return self._fetch(url, **kwargs).text

    def _discover(self, phrase: str, vertical: bool) -> list[str]:
        """Clip slugs ("sunset-behind-mountains-2123") of one results page."""
        page = self._get(f"{self.base}/free-stock-video/discover/{quote(phrase)}/",
                         params={"orientation": "vertical"} if vertical else None)
        return list(dict.fromkeys(re.findall(r'href="/free-stock-video/([a-z0-9-]+-\d+)/"', page)))

    def _request(self, query: str, per_page: int, allow_landscape: bool, wide: bool = False) -> dict:
        words, found = query.split(), []
        while words and not found:  # "clock ticking dark" -> "clock ticking" -> "clock"
            phrase = " ".join(words)
            if wide:  # the unfiltered listing is mostly landscape
                found = [(slug, False) for slug in self._discover(phrase, vertical=False)]
                words = words[:-1]
                continue
            found = [(slug, True) for slug in self._discover(phrase, vertical=True)]
            if allow_landscape and len(found) < self.per_query:
                seen = {slug for slug, _ in found}
                found += [(slug, False) for slug in self._discover(phrase, vertical=False)
                          if slug not in seen]
            words = words[:-1]
        videos = []
        for slug, vertical in found[: self.per_query]:
            obj = self.video_object(self._get(f"{self.base}/free-stock-video/{slug}/"), slug)
            if obj:
                img = thumbnail_image(obj.get("thumbnailUrl"), self.session, self.thumb_dir)
                videos.append(dict(obj, vertical=vertical, thumb_size=list(img.size) if img else None))
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

    @classmethod
    def download_page(cls, video_id: str, resolution: int) -> str:
        """The site's own download link for one resolution (see ``resolve``)."""
        return f"{cls.base}/free-stock-video/download/{video_id}/?context=sidebar&type={resolution}p"

    @classmethod
    def parse(cls, data: dict, query: str) -> list[Candidate]:
        out = []
        for v in data.get("videos", []):
            url = v.get("contentUrl") or ""
            m = re.search(r"-(\d+)/", v.get("@id", ""))
            is_mp4 = url.endswith(".mp4") or v.get("encodingFormat") == "video/mp4"
            if not v.get("license", "").endswith("#videoFree") or not m or not url or not is_mp4:
                continue
            d = re.fullmatch(r"PT(?:(\d+)M)?(?:(\d+)S)?", v.get("duration") or "")
            seconds = int(d.group(1) or 0) * 60 + int(d.group(2) or 0) if d else 0
            width, height = cls._frame_size(v)
            ladder = cls.PORTRAIT_LADDER if height > width else cls.LANDSCAPE_LADDER
            urls = cls._downloads(m.group(1), url, ladder)
            out.append(Candidate("mixkit", m.group(1), v["@id"].split("#")[0], urls[0],
                                 width, height, float(seconds), "Mixkit", v.get("thumbnailUrl"), query,
                                 alt_urls=urls[1:],
                                 thumb_size=tuple(v["thumb_size"]) if v.get("thumb_size") else None))
        return out

    @staticmethod
    def _frame_size(v: dict) -> tuple[int, int]:
        """Nominal video size (short side 1080) from the thumbnail's aspect.
        Without a thumbnail, clips from the vertical search are portrait and
        others are assumed landscape (search caches written before thumbnails
        were recorded only hold vertical results)."""
        if v.get("thumb_size"):
            w, h = v["thumb_size"]
            return (round(1080 * w / h), 1080) if w >= h else (1080, round(1080 * h / w))
        return (1080, 1920) if v.get("vertical", True) else (1920, 1080)

    @classmethod
    def _downloads(cls, video_id: str, content_url: str, ladder: tuple[int, ...]) -> list[str]:
        """Download URLs, best resolution first. Classic files are named
        ``…-720.mp4`` and the other sizes sit next to them; newer items have
        opaque file names, so their HD/4K files come from the download page
        and the 720p ``contentUrl`` is the last resort."""
        if re.search(r"-720\.mp4$", content_url):
            return [re.sub(r"-720\.mp4$", f"-{r}.mp4", content_url) for r in ladder]
        return [cls.download_page(video_id, r) for r in ladder if r > 720] + [content_url]

    def resolve(self, url: str) -> str:
        """Download pages name the real file in the modal they return."""
        if not url.startswith(f"{self.base}/free-stock-video/download/"):
            return url
        m = re.search(r'data-download--modal-url-value="([^"]+)"', self._get(url))
        if not m:
            raise FootageError(f"no file on {url}")
        return html.unescape(m.group(1))


def _search(query: str, providers: list[_Provider], allow_landscape: bool,
            wide: bool = False) -> list[Candidate]:
    cands: list[Candidate] = []
    for p in providers:
        try:
            cands += p.search(query, allow_landscape=allow_landscape, wide=wide)
        except requests.RequestException as exc:
            log.warning("%s search failed for %r: %s", p.name, query, exc)
    return cands


def _download(c: Candidate, provider: _Provider, dst: Path) -> Path:
    """Fetch ``c`` to ``dst``, trying its fallback URLs in order."""
    if dst.is_file() and dst.stat().st_size > 0:
        return dst  # downloaded by an earlier render: no need to resolve URLs again
    *fallbacks, last = [c.download_url, *c.alt_urls]
    for url in fallbacks:
        try:
            return download(provider.resolve(url), dst, timeout=120)
        except (requests.RequestException, FootageError) as exc:  # incl. timeouts on a 4K file
            log.info("%s: %s unavailable (%s), trying the next file", c.key, url, exc)
    return download(provider.resolve(last), dst, timeout=120)


def fetch_footage(queries: list[str], providers: list[_Provider], cache_dir: Path,
                  min_duration: float, allow_landscape: bool = True, shortlist: int = 6,
                  count: int | None = None, extra_score: ExtraScore | None = None,
                  portrait_weight: float = 3.0, palette: str = "bright",
                  exclude: set[str] | None = None, wide: bool = False,
                  avoid: set[str] | None = None) -> list[tuple[Candidate, Path]]:
    """One clip per query (in order) until ``count`` clips, never the same clip twice.

    Each query's candidates are ranked by ``score``; the best ``shortlist``
    get their thumbnail (fetched once, cached) for a brightness check against
    the ``palette``'s target and for ``extra_score(candidate, thumbnail)``,
    which the caller uses for visual judgement (a skyline for the hook shot,
    similarity to reference pictures). The thumbnail is None when it can't be
    fetched. If a clip fails to download, the next one on the shortlist is used.
    Clips that ``describes`` with a word in ``avoid`` are skipped."""
    if not providers:
        raise FootageError("no stock provider configured: enable Mixkit, set PEXELS_API_KEY "
                           "and/or PIXABAY_API_KEY, or pass --footage-dir with your own clips")
    by_name = {p.name: p for p in providers}
    luma_target = LUMA_TARGETS.get(palette, LUMA_TARGETS["bright"])
    used: set[str] = set(exclude or ())  # picked elsewhere, picked here, or failed to download
    picks: list[tuple[Candidate, Path]] = []
    for query in queries:
        if count is not None and len(picks) >= count:
            break
        cands = [c for c in _search(query, providers, allow_landscape, wide)
                 if c.key not in used and (c.portrait or allow_landscape) and not describes(c, avoid)]
        if not cands:
            log.warning("no footage for %r", query)
            continue
        cands.sort(key=lambda c: score(c, min_duration, portrait_weight, luma_target), reverse=True)
        ranked = []
        for c in cands[:shortlist]:
            provider = by_name[c.provider]
            img = thumbnail_image(c.thumbnail, provider.session, provider.thumb_dir)
            if img is not None:
                c.luma, c.thumb_size = image_luma(img), c.thumb_size or img.size
            bonus = float(extra_score(c, img)) if extra_score else 0.0
            ranked.append((score(c, min_duration, portrait_weight, luma_target) + bonus, c))
        ranked.sort(key=lambda item: item[0], reverse=True)
        for total, c in ranked:
            used.add(c.key)
            try:
                path = _download(c, by_name[c.provider], Path(cache_dir) / "footage" / f"{c.key}.mp4")
            except (requests.RequestException, FootageError) as exc:
                log.warning("download failed for %s: %s", c.key, exc)
                continue
            log.info("footage %-28s <- %s (%dx%d, %.0fs, score %.2f)",
                     query, c.key, c.width, c.height, c.duration, total)
            picks.append((c, path))
            break
    if not picks:
        raise FootageError("stock search returned nothing usable")
    return picks


def best_of(queries: list[str], providers: list[_Provider], cache_dir: Path, min_duration: float,
            rank: ExtraScore, allow_landscape: bool = True, shortlist: int = 8,
            portrait_weight: float = 3.0, palette: str = "bright",
            wide: bool = False, avoid: set[str] | None = None) -> tuple[Candidate, Path] | None:
    """The single best clip across several searches, judged mostly by
    ``rank(candidate, thumbnail)`` (e.g. how clear a skyline is for the hook
    shot). Unlike ``fetch_footage`` it compares candidates of every query
    before choosing. None when nothing could be found or downloaded."""
    by_name = {p.name: p for p in providers}
    luma_target = LUMA_TARGETS.get(palette, LUMA_TARGETS["bright"])
    seen: set[str] = set()
    ranked: list[tuple[float, Candidate]] = []
    for query in dict.fromkeys(queries):
        cands = [c for c in _search(query, providers, allow_landscape, wide)
                 if c.key not in seen and (c.portrait or allow_landscape) and not describes(c, avoid)]
        cands.sort(key=lambda c: score(c, min_duration, portrait_weight, luma_target), reverse=True)
        for c in cands[:shortlist]:
            seen.add(c.key)
            provider = by_name[c.provider]
            img = thumbnail_image(c.thumbnail, provider.session, provider.thumb_dir)
            if img is not None:
                c.luma, c.thumb_size = image_luma(img), c.thumb_size or img.size
            ranked.append((score(c, min_duration, portrait_weight, luma_target) + float(rank(c, img)), c))
    ranked.sort(key=lambda item: item[0], reverse=True)
    for total, c in ranked:
        try:
            path = _download(c, by_name[c.provider], Path(cache_dir) / "footage" / f"{c.key}.mp4")
        except (requests.RequestException, FootageError) as exc:
            log.warning("download failed for %s: %s", c.key, exc)
            continue
        log.info("best of %s <- %s (%dx%d, %.0fs, score %.2f)", "/".join(dict.fromkeys(queries)),
                 c.key, c.width, c.height, c.duration, total)
        return c, path
    return None


def write_credits(picks: list[tuple[Candidate, Path]], path: Path) -> Path:
    lines = ["Stock footage used (Mixkit Free / Pexels / Pixabay licences; credit appreciated):", ""]
    for c, _ in picks:
        lines.append(f"- {c.provider}: {c.author or 'unknown'} - {c.page_url}  [query: {c.query}]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (path.with_suffix(".json")).write_text(json.dumps([asdict(c) for c, _ in picks], indent=2))
    return path
