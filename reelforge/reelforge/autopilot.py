"""Autopilot: reels written, made and scheduled without anyone at the wheel.

A run tops the coming days up to ``per_day`` reels at the audience's best
hours (Metricool's activity data for the chosen platforms). Gemini writes
each script with its caption, hashtags and YouTube title. Each reel is a copy
of the user's template reel with the new script, so the look, voice, captions,
music and footage searches stay the same; the stock clips are planned for the
whole batch at once so no two reels share a clip, and clips used by recent
autopilot reels are ruled out so the feed keeps changing. The music comes
from the user's music library, dealt at random like cards.

The steps that need the user's connectors (the Higgsfield voice and upload,
Metricool's scheduling) are done by Claude in its session; ``panelrun
autopilot-*`` does the rest (see panelrun.py and the jackk-reel skill).

Panel documents:
    settings/autopilot   the user's settings (``DEFAULTS``)
    music/<id>           the music library: {name, store, start (seconds into the song), enabled, ...}
    autopilot/<slot id>  one reel: {slot, tz, status, project_id, title, script, caption, hashtags,
                         youtube_title, platforms, song, master_url, master_media_id, cover, cover_ms,
                         metricool: {id, uuid, plannerUrl}, clips, problems, created, updated}
"""
from __future__ import annotations

import copy
import itertools
import json
import random
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULTS = {
    "enabled": True,
    "per_day": 3,
    "days": 7,                      # how far ahead a run fills
    "platforms": ["instagram", "youtube"],
    "approval": "auto",             # "auto" posts on its own; "review" makes Metricool drafts to approve
    "window": ["08:00", "23:30"],   # earliest and latest posting time
    "min_gap": 3,                   # hours between two reels
    "template": None,               # project id whose look, voice and music are copied
    "format": "template",           # "template" | "9:16" | "4:5" (4:5 with black bars)
    "music": "random",              # "random" (from the music library) | "template" (its song) | "none"
    "topic": "",
    "tone": "",
    "ai_label": False,              # label the posts as AI-generated on the platforms
}
PLATFORMS = ("instagram", "youtube", "tiktok")
# hours that do well on short video when Metricool has no data yet, best first
FALLBACK_HOURS = (19, 12, 21, 9, 17, 15, 22, 11, 20, 13, 18, 10, 14, 16, 23, 8)
MIN_GAP_FLOOR = 2.0       # two reels are never posted closer than this (hours): a day gets fewer instead


def _minutes(hhmm: str) -> int:
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(hhmm or "").strip())
    if not m:
        raise ValueError(f"not a time: {hhmm!r}")
    return min(23, int(m.group(1))) * 60 + min(59, int(m.group(2)))


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def settings_of(raw: dict | None) -> dict:
    """The user's settings with defaults filled in and values kept in range."""
    s = {**copy.deepcopy(DEFAULTS), **{k: v for k, v in (raw or {}).items() if k in DEFAULTS}}
    s["per_day"] = min(6, max(1, int(s["per_day"] or 3)))
    s["days"] = min(14, max(1, int(s["days"] or 7)))
    s["min_gap"] = min(8.0, max(1.0, float(s["min_gap"] or 3)))
    s["platforms"] = [p for p in dict.fromkeys(s["platforms"] or []) if p in PLATFORMS] or ["instagram"]
    s["approval"] = "review" if s["approval"] == "review" else "auto"
    s["format"] = s["format"] if s["format"] in ("template", "9:16", "4:5") else "template"
    s["music"] = "random" if s["music"] == "rotate" else s["music"] if s["music"] in ("random", "template", "none") \
        else "random"
    try:
        start, end = (_minutes(x) for x in list(s["window"] or [])[:2])
    except (TypeError, ValueError):
        start, end = 8 * 60, 23 * 60 + 30
    if end <= start:
        start, end = 8 * 60, 23 * 60 + 30
    s["window"] = [_hhmm(start), _hhmm(end)]
    s["ai_label"] = bool(s["ai_label"])
    return s


# --------------------------------------------------------------------------- when to post

def grid_of(payload: dict | None) -> dict[int, dict[int, float]]:
    """Metricool's getBestTimeToPostByNetwork answer -> {day of week (Monday = 1): {hour: value}}.
    Also takes the short form {"1": [24 values from midnight], ..., "7": [...]}."""
    out: dict[int, dict[int, float]] = {}
    if payload and "data" not in payload:
        return {int(d): {h: float(v or 0) for h, v in enumerate(values[:24])}
                for d, values in payload.items() if str(d).isdigit() and isinstance(values, list)}
    for d in (payload or {}).get("data") or []:
        for x in d.get("bestTimesByHour") or []:
            out.setdefault(int(d["dayOfWeek"]), {})[int(x["hourOfDay"])] = float(x.get("value") or 0)
    return out


def combine(grids: list[dict[int, dict[int, float]]]) -> dict[int, dict[int, float]]:
    """The platforms' activity together: each grid scaled to its own busiest
    hour (a big audience doesn't drown a small one), then averaged."""
    total: dict[int, dict[int, float]] = {}
    used = 0
    for g in grids:
        peak = max((v for day in g.values() for v in day.values()), default=0.0)
        if peak <= 0:
            continue
        used += 1
        for d, hours in g.items():
            for h, v in hours.items():
                total.setdefault(d, {})[h] = total.get(d, {}).get(h, 0.0) + v / peak
    return {d: {h: v / used for h, v in hours.items()} for d, hours in total.items()} if used else {}


def _best_hours(cands: list[datetime], score: dict[int, float], k: int, gap: float,
                busy: list[datetime]) -> list[datetime]:
    """The ``k`` candidates with the highest total score that are all ``gap``
    hours apart from each other and from ``busy`` ([] if no such set)."""
    apart = lambda a, b: abs((a - b).total_seconds()) >= gap * 3600
    free = [c for c in cands if all(apart(c, b) for b in busy)]
    best, best_score = [], -1.0
    for combo in itertools.combinations(free, k):
        if all(apart(a, b) for a, b in itertools.combinations(combo, 2)):
            total = sum(score.get(c.hour, 0.0) for c in combo)
            if total > best_score:
                best, best_score = list(combo), total
    return best


def pick_slots(grid: dict[int, dict[int, float]], today: date, days: int, per_day: int,
               window: tuple[str, str] | list[str] = ("08:00", "23:30"), min_gap: float = 3,
               taken: list[datetime] | tuple = (), earliest: datetime | None = None) -> list[datetime]:
    """The hours to post on each of ``days`` days from ``today``: up to
    ``per_day`` a day counting the slots already ``taken`` that day, on the
    hour, inside ``window``, not before ``earliest``: the set of hours with
    the most audience activity that are at least ``min_gap`` hours apart.
    When a day can't fit them (a late start, a short window) the gap shrinks
    an hour at a time down to ``MIN_GAP_FLOOR``; past that the day gets fewer
    reels rather than two close together. Without activity data, hours that
    do well on short video."""
    start_m, end_m = _minutes(window[0]), _minutes(window[1])
    picked: list[datetime] = []
    for i in range(days):
        day = today + timedelta(days=i)
        busy = [t for t in taken if t.date() == day]
        need = per_day - len(busy)
        if need <= 0:
            continue
        cands = [datetime.combine(day, time(h)) for h in range(24) if start_m <= h * 60 <= end_m]
        if earliest is not None:
            cands = [c for c in cands if c >= earliest]
        score = grid.get(day.isoweekday()) or {}
        if not score:
            score = {h: float(len(FALLBACK_HOURS) - n) for n, h in enumerate(FALLBACK_HOURS)}
        floor = min(float(min_gap), MIN_GAP_FLOOR)
        chosen: list[datetime] = []
        for k in range(min(need, len(cands)), 0, -1):  # as many as fit, then the best of them
            gap = float(min_gap)
            while gap >= floor and not chosen:
                chosen = _best_hours(cands, score, k, gap, busy)
                gap -= 1
            if chosen:
                break
        picked += chosen
    return sorted(picked)


def slot_id(dt: datetime) -> str:
    return f"ap-{dt:%Y%m%d-%H%M}"


def now_in(tz: str) -> datetime:
    """The wall-clock time in ``tz``, without tzinfo (slots are wall-clock times there)."""
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None, second=0, microsecond=0)


def with_offset(dt: datetime, tz: str) -> str:
    """"YYYY-MM-DDTHH:MM:SS+HH:MM" for a wall-clock time in ``tz`` (what Metricool's ``date`` takes)."""
    return dt.replace(tzinfo=ZoneInfo(tz)).isoformat(timespec="seconds")


def taken_slots(posts: list[dict], tz: str) -> list[datetime]:
    """Wall-clock times (in ``tz``) of posts Metricool already has (getScheduledPosts' data)."""
    out = []
    for p in posts or []:
        pd = p.get("publicationDate") or {}
        try:
            dt = datetime.fromisoformat(str(pd["dateTime"])[:19])
        except (KeyError, ValueError):
            continue
        src = pd.get("timezone") or tz
        if src != tz:
            dt = dt.replace(tzinfo=ZoneInfo(src)).astimezone(ZoneInfo(tz)).replace(tzinfo=None)
        out.append(dt)
    return out


# --------------------------------------------------------------------------- what to post

def build_project(template: dict, script: str, title: str, *, music: str | dict | bool | None = True,
                  banned: list[str] | set[str] = (), fmt: str = "template") -> dict:
    """A new reel from the template's settings: the same look, voice and
    searches with a new script. ``music`` True keeps the template's song, a
    library pick (``pick_songs``) or a string upload path uses that song, None
    means no music. The template's own clips
    stay with it (only its reference pictures carry over), and landscape clips
    are left out: in a tall frame they look zoomed in."""
    p = copy.deepcopy(template)
    p["name"], p["script"] = title, script
    fs = p.setdefault("footage", {})
    fs["items"] = [i for i in fs.get("items") or [] if i.get("role") == "reference"]
    fs["picks"] = []
    fs["banned"] = sorted(set(fs.get("banned") or []) | set(banned))
    fs["allow_landscape"] = False
    v = p.setdefault("style", {}).setdefault("video", {})
    v["draft"] = False
    if fmt == "9:16":
        v["aspect"], v["letterbox"] = "9:16", False
    elif fmt == "4:5":
        v["aspect"], v["letterbox"] = "4:5", True
    voice = p.setdefault("voice", {})
    voice["source"], voice["file"] = "higgsfield", None  # a new voiceover for the new script
    m = p.setdefault("music", {})
    if music is None:
        m["file"] = None
    elif isinstance(music, dict):
        m.update(file=music["file"], source_in=float(music.get("start") or 0), source_out=None)
    elif isinstance(music, str) and music != m.get("file"):
        m.update(file=music, source_in=0, source_out=None)
    return p


def library_songs(library: list[dict] | None) -> list[dict]:
    """The usable songs of the music library (documents as saved, or wrapped as ArtifactData returns them)."""
    out = []
    for raw in library or []:
        song = raw["data"] if isinstance(raw.get("data"), dict) and "store" in raw["data"] else raw
        if song.get("name") and song.get("store") and song.get("enabled", True):
            out.append(song)
    return out


def pick_songs(settings: dict, count: int, template_song: str | None, library: list[dict] | None,
               recent: list[str] | tuple = (), rng: random.Random | None = None) -> list:
    """The music of ``count`` new reels, each None (no music), True (the
    template's song) or a library song {name, file, start}. Library songs are
    dealt like cards: every song once, in random order with the ones heard
    lately (``recent``, oldest first) last, before any song comes again, and
    never the same song twice in a row. An empty library falls back to the
    template's song."""
    if settings["music"] == "none":
        return [None] * count
    songs = library_songs(library)
    if settings["music"] == "template" or not songs:
        return [True if template_song else None] * count
    rng = rng or random.Random()
    last_heard = {name: i for i, name in enumerate(recent or [])}
    out: list = []
    deck: list[dict] = []
    while len(out) < count:
        if not deck:
            deck = songs[:]
            rng.shuffle(deck)
            if not out:  # the first deal puts the songs heard lately at the bottom
                deck.sort(key=lambda song: last_heard.get(song["name"], -1))
            if out and len(deck) > 1 and deck[0]["name"] == out[-1]["name"]:
                deck.append(deck.pop(0))
        song = deck.pop(0)
        out.append({"name": song["name"], "file": f"uploads/{song['name']}", "start": float(song.get("start") or 0)})
    return out


def assign_clips(reviews: list[dict], banned: set[str] | list[str] = ()) -> list[list[dict]]:
    """Footage for a batch of reels from their reviews (``pipeline.review_footage``):
    each reel gets an opening clip and enough pool clips for its shots, never a
    clip another reel of the batch or ``banned`` has. Returns each reel's
    ``footage.picks`` (the opening one with slot "opening"), in the reviews' order."""
    used = set(banned)
    out = []
    for rv in reviews:
        picks = []
        opening = next((c for c in rv.get("opening") or [] if c["key"] not in used), None)
        if opening is not None:
            picks.append({**_pick(opening), "slot": "opening"})
            used.add(opening["key"])
        need = int(rv.get("rest_shots") or 0) + 2  # the real voiceover can add a shot
        for c in rv.get("pool") or []:
            if need <= 0:
                break
            if c["key"] not in used:
                picks.append(_pick(c))
                used.add(c["key"])
                need -= 1
        out.append(picks)
    return out


def spare_openings(reviews: list[dict], assigned: list[list[dict]], limit: int = 4) -> list[list[dict]]:
    """Each reel's other opening clips that no reel of the batch uses: what a
    swap tries when the first one doesn't work on screen."""
    used = {p["key"] for picks in assigned for p in picks}
    return [[_pick(c) for c in rv.get("opening") or [] if c["key"] not in used][:limit] for rv in reviews]


def _pick(c: dict) -> dict:
    return {k: v for k, v in c.items() if k not in ("tile", "portrait")}


def post_text(caption: str, hashtags: list[str]) -> str:
    """The post's text: the caption, then the hashtags (within Instagram's 2,200 characters)."""
    tags = " ".join(f"#{t}" for t in hashtags or [])
    text = caption.strip()
    if tags:
        room = 2200 - len(text) - 2
        while tags and len(tags) > room:
            tags = tags.rsplit(" ", 1)[0] if " " in tags else ""
        text = f"{text}\n\n{tags}" if tags else text
    return text[:2200]


def quality_problems(result: dict, used_clips: list[dict], min_s: float = 15, max_s: float = 90) -> list[str]:
    """Why a finished reel shouldn't be posted on its own (empty list = fine)."""
    if not result.get("ok"):
        return [f"The render failed: {result.get('error') or 'unknown error'}"]
    r = result.get("result") or {}
    problems = []
    duration = float(r.get("duration") or 0)
    if not min_s <= duration <= max_s:
        problems.append(f"It is {duration:.0f} s long (expected {min_s:.0f}-{max_s:.0f} s).")
    if any("repeat" in str(w) for w in r.get("warnings") or []):
        problems.append("Some clips repeat.")
    wide = [c for c in used_clips if int(c.get("width") or 0) > int(c.get("height") or 0)]
    if wide:
        problems.append(f"{len(wide)} wide clip(s) would look zoomed in.")
    return problems


def metricool_post(item: dict, settings: dict, brand_id: str, media_url: str, cover_ms: int | None) -> dict:
    """Arguments for Metricool's createScheduledPost: one post to every chosen
    platform at the reel's slot (a draft to approve in "review" mode)."""
    tz = item["tz"]
    dt = datetime.fromisoformat(item["slot"])
    nets = settings["platforms"]
    info: dict = {
        "autoPublish": True, "descendants": [], "draft": settings["approval"] == "review",
        "firstCommentText": "", "hasNotReadNotes": False, "media": [media_url], "mediaAltText": [],
        "providers": [{"network": n} for n in nets],
        "publicationDate": {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:00"), "timezone": tz},
        "shortener": False, "smartLinkData": {"ids": []},
        "text": post_text(item.get("caption") or item["title"], item.get("hashtags") or []),
    }
    if cover_ms is not None:
        info["videoCoverMilliseconds"] = int(cover_ms)
    if "instagram" in nets:
        info["instagramData"] = {"type": "REEL", "showReelOnFeed": True, "isAiGenerated": settings["ai_label"]}
    if "youtube" in nets:
        info["youtubeData"] = {"title": (item.get("youtube_title") or item["title"])[:100], "type": "short",
                               "privacy": "public", "madeForKids": False,
                               "isAiGeneratedContent": settings["ai_label"]}
    if "tiktok" in nets:
        info["tiktokData"] = {"privacyOption": "PUBLIC_TO_EVERYONE", "disableComment": False,
                              "disableDuet": False, "disableStitch": False, "autoAddMusic": False,
                              "isAigc": settings["ai_label"], "title": (item.get("youtube_title") or item["title"])[:90]}
    return {"slot_id": item["id"], "blogId": str(brand_id), "date": with_offset(dt, tz),
            "info": json.dumps(info, ensure_ascii=False)}
