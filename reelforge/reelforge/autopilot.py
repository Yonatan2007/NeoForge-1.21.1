"""Autopilot: reels written, made and scheduled without anyone at the wheel.

A run tops the coming days up to ``per_day`` reels at the audience's best
hours (Metricool's activity data for the chosen platforms). Gemini writes
each script with its caption and YouTube title (and hashtags, only when the
``hashtags`` setting is on: the user doesn't want them). Each reel is a copy
of the user's template reel with the new script, so the look, voice, captions,
music and footage searches stay the same; the stock clips are planned for the
whole batch at once so no two reels share a clip, and clips used by recent
autopilot reels are ruled out so the feed keeps changing. The music comes
from the account's music library, dealt at random like cards: each account's
library has a half for its longer videos and one for its comment videos.

The steps that need the user's connectors (the Higgsfield voice and upload,
Metricool's scheduling) are done by Claude in its session; ``panelrun
autopilot-*`` does the rest (see panelrun.py and the jackk-reel skill).

Growing the account (settings below): every post carries a question as its
first comment; scripts are kept short, open on their strongest words and are
built around phrases people really search for; Gemini learns from how the
earlier reels did (``insights.py``); some Instagram posts go out as Trial
Reels (shown to non-followers first) once Instagram allows them for the
account; some invite a collaborator; and TikTok can wait for the user to add
a trending sound at volume 0 over the reel's own music (TikTok only lets the
account owner attach a sound, in its own app), reminded by Metricool's app.

Accounts: the main account and one per niche the user added (``niches``),
each posting to its own Metricool brand with its own settings, queue and
numbers. An account can also be posted by hand (``posting: "manual"``): its
reels wait in the panel, each with its video to save, caption and first
comment, for the user to post in the apps around its time; it needs no
Metricool brand. A batch mixes two kinds of video (``kinds_for``): longer
"watch" reels with a voice, and 7-10 s "comment" videos, one line on screen
and no voice, that ask something people answer in the comments
(``build_comment_project``).

Panel documents (``settings_doc``, ``insights_doc``):
    settings/autopilot   the main account's settings (``DEFAULTS``); settings/autopilot-<account> another's
    music/<id>           the music libraries: {name, store, start (seconds into the song), enabled,
                         use: ["<account>/watch" | "<account>/comment", ...] (where it plays), ...}
    autopilot/<slot id>  one reel (``slot_id``): {account, kind, slot, tz, status, project_id, title, script, caption, hashtags,
                         youtube_title, first_comment, search_phrase, trial, collaborator, tiktok_sound,
                         reply_to, platforms, song, master_url, master_media_id, cover, cover_ms,
                         metricool: {id, uuid, plannerUrl}, metricool_tiktok, clips, problems, created, updated};
                         a reel posted by hand also has posting "manual", video (the panel asset to save) and
                         text (the post's text to copy); its status goes "ready" -> "posted" (the user's tap)
    insights/latest      how the main account's reels did and what its next batch learns (``insights.py``);
                         insights/<account> another account's
    replies/<id>         a viewer's comment with reply drafts; status "idea" makes it a video reply
"""
from __future__ import annotations

import copy
import itertools
import json
import random
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import niches

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
    "length": "short",              # "short" (~20-30 s) | "medium" (~30-40 s) | "long" (~40-55 s), see ideas.LENGTHS
    "first_comment": True,          # a question to answer, posted as each post's first comment
    "search": True,                 # captions and titles around phrases people search for (search.py)
    "trial_reels": "some",          # Instagram Trial Reels: "off" | "some" (1 in 3) | "all"; only once allowed
    "collaborators": [],            # Instagram accounts invited as collaborators ...
    "collab_every": 7,              # ... on one reel in this many
    "tiktok_sound": "off",          # "remind": TikTok waits for a trending sound added at volume 0 (by the user)
    "hashtags": False,              # hashtags under the caption; off: posts carry none (the user's choice)
    "niche": niches.DEFAULT,        # what the account is about (niches.NICHES)
    "name": "",                     # the account's name in the panel
    "brand_id": None,               # the Metricool brand it posts to
    "mix": "watch",                 # "watch" (longer videos only) | "half" | "comments" (2 in 3 comment videos)
    "posting": "metricool",         # "metricool" posts them; "manual": they wait in the panel for the user to post
    "tz": None,                     # a manual account's time zone (the panel saves the viewer's); else the brand's
}
POSTINGS = ("metricool", "manual")
MAIN = "main"                       # the first account: settings/autopilot, insights/latest, ids "ap-<slot>"
# The accounts the panel offers, each posting through its own Metricool brand or by hand: the main one and one per niche
ACCOUNTS = ({"id": MAIN, "niche": "philosophy", "name": "Main account"},
            {"id": "dilemmas", "niche": "dilemmas", "name": "Would you rather"},
            {"id": "psychology", "niche": "psychology", "name": "Everyday psychology"},
            {"id": "space", "niche": "space", "name": "Space and science"},
            {"id": "gym", "niche": "gym", "name": "Gym and discipline"})
MIXES = ("watch", "half", "comments")
COMMENT_SECONDS = (7.0, 10.0)       # a comment video's length range
PLATFORMS = ("instagram", "youtube", "tiktok")
LENGTHS = ("short", "medium", "long")
TRIAL_EVERY = 3           # "some" trial reels: one in this many Instagram posts
TRIAL_FOLLOWERS = 1000    # Instagram offers Trial Reels to accounts about this big
FIRST_COMMENT_MAX = 150   # TikTok's limit for a first comment (Instagram and YouTube take more)
MAX_COLLABORATORS = 3
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
    s["length"] = s["length"] if s["length"] in LENGTHS else "short"
    s["first_comment"] = bool(s["first_comment"])
    s["search"] = bool(s["search"])
    s["trial_reels"] = s["trial_reels"] if s["trial_reels"] in ("off", "some", "all") else "some"
    s["collaborators"] = usernames(s["collaborators"])
    try:
        s["collab_every"] = min(30, max(1, int(s["collab_every"] or 7)))
    except (TypeError, ValueError):
        s["collab_every"] = 7
    s["tiktok_sound"] = "remind" if s["tiktok_sound"] == "remind" else "off"
    s["hashtags"] = bool(s["hashtags"])
    s["niche"] = s["niche"] if s["niche"] in niches.NICHES else niches.DEFAULT
    s["name"] = str(s["name"] or "").strip()[:60]
    s["brand_id"] = str(s["brand_id"]).strip() if s["brand_id"] not in (None, "") else None
    s["mix"] = s["mix"] if s["mix"] in MIXES else "watch"
    s["posting"] = s["posting"] if s["posting"] in POSTINGS else "metricool"
    s["tz"] = time_zone(s["tz"])
    return s


def time_zone(name) -> str | None:
    """``name`` if it is a time zone (IANA, e.g. "Asia/Jerusalem"), else None."""
    try:
        return str(name) if name and ZoneInfo(str(name)) else None
    except (ValueError, KeyError, OSError):  # ZoneInfoNotFoundError is a KeyError
        return None


def by_hand(settings: dict) -> bool:
    """The account's reels wait in the panel for the user to post them (no Metricool)."""
    return settings.get("posting") == "manual"


# --------------------------------------------------------------------------- accounts

def account_key(account: str | None) -> str:
    """A safe account id ("" and None mean the main account)."""
    key = re.sub(r"[^a-z0-9-]", "", str(account or "").lower())[:30]
    return key or MAIN


def settings_doc(account: str | None = None) -> str:
    """The account's settings document id in the "settings" collection."""
    key = account_key(account)
    return "autopilot" if key == MAIN else f"autopilot-{key}"


def insights_doc(account: str | None = None) -> str:
    """The account's document id in the "insights" collection."""
    key = account_key(account)
    return "latest" if key == MAIN else key


def account_of(doc: dict | None) -> str:
    """The account a queue, reply or task document belongs to (none: the main account)."""
    return account_key((doc or {}).get("account"))


def kinds_for(count: int, mix: str) -> list[str]:
    """The kind of each of ``count`` reels in posting order: "watch" (a longer
    narrated video) or "comment" (one line on screen). "half" alternates,
    starting with a comment video; "comments" makes two in three of them."""
    if mix == "half":
        return ["comment" if i % 2 == 0 else "watch" for i in range(count)]
    if mix == "comments":
        return ["watch" if i % 3 == 1 else "comment" for i in range(count)]
    return ["watch"] * count


def usernames(raw) -> list[str]:
    """Instagram usernames from a list or "a, @b c" text: without "@", at most three."""
    items = raw if isinstance(raw, list) else re.split(r"[\s,;]+", str(raw or ""))
    names = [re.sub(r"[^a-z0-9._]", "", str(u).strip().lstrip("@").lower())[:30] for u in items]
    return list(dict.fromkeys(n for n in names if n))[:MAX_COLLABORATORS]


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


def slot_id(dt: datetime, account: str | None = None) -> str:
    key = account_key(account)
    return f"ap-{dt:%Y%m%d-%H%M}" if key == MAIN else f"ap-{key}-{dt:%Y%m%d-%H%M}"


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

def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        base[k] = _merge(dict(base.get(k) or {}), v) if isinstance(v, dict) else copy.deepcopy(v)
    return base


def build_project(template: dict, script: str, title: str, *, music: str | dict | bool | None = True,
                  banned: list[str] | set[str] = (), fmt: str = "template", niche: str | None = None) -> dict:
    """A new reel from the template's settings: the same look, voice and
    searches with a new script. ``music`` True keeps the template's song, a
    library pick (``pick_songs``) or a string upload path uses that song, None
    means no music. The template's own clips
    stay with it (only its reference pictures carry over), and landscape clips
    are left out: in a tall frame they look zoomed in. ``niche`` sets the
    footage the reel searches for and its grade (``niches``)."""
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
    if niche and niche != niches.DEFAULT:
        n = niches.get(niche)
        _merge(fs, n.footage)
        _merge(p.setdefault("style", {}), n.style)
    return p


def comment_seconds(line: str) -> float:
    """A comment video's length: time to read its line twice, within ``COMMENT_SECONDS``."""
    lo, hi = COMMENT_SECONDS
    return round(min(hi, max(lo, 5.5 + 0.3 * len(str(line).split()))), 1)


def build_comment_project(template: dict, line: str, title: str, *, music: str | dict | bool | None = True,
                          banned: list[str] | set[str] = (), fmt: str = "template", niche: str | None = None) -> dict:
    """A comment video: one clip with the whole of ``line`` laid along its
    skyline in one soft arc from the first frame to the last, the music and
    no voice, 7-10 s (``comment_seconds``), cut so it loops cleanly."""
    p = build_project(template, line, title, music=music, banned=banned, fmt=fmt, niche=niche)
    seconds = comment_seconds(line)
    p["voice"].update(source="none", file=None, words_per_second=10.0)
    p.setdefault("duration", {}).update(target=seconds, max_intro=0.0)
    style = p.setdefault("style", {})
    # the whole line at once, from the first frame to the last, on one soft arc (the opening
    # shot is chosen for a smooth skyline that curves only a little; a niche without skylines
    # keeps its fixed layout), big enough to read
    hook = style.setdefault("hook", {})
    hook.update(mode="terrain", sentences=3, hold=60.0, reveal="all",
                curve="fixed" if hook.get("curve") == "fixed" else "gentle",
                fallback="arc", size=68, small_scale=0.9, big_scale=1.6)
    style.setdefault("video", {}).update(max_shot=seconds + 0.5, fade_in=0.0, fade_out=0.0, voice_delay=0.0)
    p.setdefault("music", {}).update(fade_in=0.0, fade_out=0.3, duck=False, volume_db=-12.0)
    p.setdefault("footage", {})["queries"] = None
    if niche and niche != niches.DEFAULT:  # the niche's own searches for that one clip
        _merge(p["footage"], niches.get(niche).comment_footage)
    return p


def library_songs(library: list[dict] | None) -> list[dict]:
    """The usable songs of the music libraries (documents as saved, or wrapped as ArtifactData returns them)."""
    out = []
    for raw in library or []:
        song = raw["data"] if isinstance(raw.get("data"), dict) and "store" in raw["data"] else raw
        if song.get("name") and song.get("store") and song.get("enabled", True):
            out.append(song)
    return out


VIDEO_KINDS = ("watch", "comment")


def song_places(song: dict) -> list[str]:
    """Where a library song plays: "<account>/<kind>" places (a song from
    before the accounts had libraries: both halves of the main account's)."""
    use = song.get("use")
    return [str(u) for u in use] if isinstance(use, list) else [f"{MAIN}/{k}" for k in VIDEO_KINDS]


def songs_for(library: list[dict] | None, account: str | None, kind: str) -> tuple[list[dict], str | None]:
    """The songs a reel of ``kind`` ("watch" or "comment") on ``account`` is
    dealt from, and the place they come from: that half of the account's
    library, else its other half, else the main account's library (the same
    half first). ([], None) when every one of them is empty."""
    songs = library_songs(library)
    account = account_key(account)
    other = "comment" if kind == "watch" else "watch"
    for place in dict.fromkeys((f"{account}/{kind}", f"{account}/{other}", f"{MAIN}/{kind}", f"{MAIN}/{other}")):
        hit = [s for s in songs if place in song_places(s)]
        if hit:
            return hit, place
    return [], None


def _deal(songs: list[dict], count: int, recent: list[str] | tuple, rng: random.Random) -> list[dict]:
    """``count`` songs dealt like cards: every song once, in random order with
    the ones heard lately (``recent``, oldest first) last, before any song
    comes again, and never the same song twice in a row."""
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


def pick_songs(settings: dict, kinds: int | list[str], template_song: str | None, library: list[dict] | None,
               recent: list[str] | tuple = (), rng: random.Random | None = None, account: str | None = None) -> list:
    """The music of new reels of ``kinds`` (one per reel, "watch" or
    "comment"; a number: that many longer reels), each None (no music), True
    (the template's song) or a library song {name, file, start}. Each kind is
    dealt from its half of ``account``'s library (``songs_for``, ``_deal``);
    with no song anywhere it falls back to the template's song."""
    kinds = ["watch"] * kinds if isinstance(kinds, int) else list(kinds)
    if settings["music"] == "none":
        return [None] * len(kinds)
    fallback = True if template_song else None
    if settings["music"] == "template":
        return [fallback] * len(kinds)
    rng = rng or random.Random()
    out: list = [fallback] * len(kinds)
    for kind in dict.fromkeys(kinds):
        songs, _ = songs_for(library, account, kind)
        if not songs:
            continue
        slots = [i for i, k in enumerate(kinds) if k == kind]
        for i, song in zip(slots, _deal(songs, len(slots), recent, rng)):
            out[i] = song
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
        need = int(rv.get("rest_shots") or 0) + int(rv.get("extra", 2))  # the real voiceover can add a shot
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


HASHTAG = re.compile(r"(?<![\w&])#\w+")


def without_hashtags(text: str) -> str:
    """The text with every #hashtag taken out (a line left empty goes too)."""
    lines = [re.sub(r"[ \t]{2,}", " ", HASHTAG.sub("", line)).strip() for line in str(text or "").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


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


def reel_text(item: dict, settings: dict) -> str:
    """The text a reel is posted with: its caption, with its hashtags only when the setting is on."""
    caption = item.get("caption") or item["title"]
    if settings["hashtags"]:
        return post_text(caption, item.get("hashtags") or [])
    return without_hashtags(caption)[:2200]


def quality_problems(result: dict, used_clips: list[dict], min_s: float | None = None, max_s: float | None = None,
                     kind: str = "watch", wide_ok: bool = False) -> list[str]:
    """Why a finished reel shouldn't be posted on its own (empty list = fine).
    ``wide_ok``: the reel may crop wide clips (starfields, see ``niches``)."""
    lo, hi = (5, 15) if kind == "comment" else (15, 90)
    min_s, max_s = lo if min_s is None else min_s, hi if max_s is None else max_s
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
    if wide and not wide_ok:
        problems.append(f"{len(wide)} wide clip(s) would look zoomed in.")
    return problems


def first_comment(text: str | None) -> str:
    """A first comment that fits every platform (TikTok takes 150 characters), cut at a word."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip('"“”')
    if len(text) <= FIRST_COMMENT_MAX:
        return text
    cut = text[:FIRST_COMMENT_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:")
    return f"{cut}…"


def plan_extras(items: list[dict], settings: dict, *, trial_ok: bool = False, history: list[dict] | tuple = (),
                sounds: list[dict] | tuple = ()) -> None:
    """Per reel of a batch, in place: whether its Instagram post is a Trial
    Reel (``trial``), which account it invites as a collaborator
    (``collaborator``) and, in "remind" mode, the trending TikTok sound the
    user adds at volume 0 (``tiktok_sound``). Collaborators take turns, the
    one invited longest ago first (``history``: earlier reels, oldest first),
    one reel in ``collab_every``; a reel with a collaborator is never a trial
    (Instagram doesn't allow both). ``trial_ok``: Instagram offers the account
    Trial Reels (``insights.trial_eligible``)."""
    nets = settings["platforms"]
    names = settings["collaborators"] if "instagram" in nets else []
    every = settings["collab_every"]
    last_seen = {h["collaborator"]: n for n, h in enumerate(history or []) if h.get("collaborator")}
    turns = sorted(names, key=lambda u: last_seen.get(u, -1))
    marked = [n for n, h in enumerate(history or []) if h.get("collaborator")]
    since = len(history) - 1 - marked[-1] if marked else every - 1
    trials = trial_ok and "instagram" in nets and settings["trial_reels"] != "off"
    plain = 0
    for i, item in enumerate(items):
        item["collaborator"] = item["trial"] = None
        since += 1
        if turns and since >= every:
            item["collaborator"] = turns[0]
            turns.append(turns.pop(0))
            since = 0
        elif trials:
            item["trial"] = settings["trial_reels"] == "all" or plain % TRIAL_EVERY == 1
            plain += 1
        else:
            plain += 1
        item["trial"] = bool(item["trial"])
        if settings["tiktok_sound"] == "remind" and "tiktok" in nets and sounds:
            s = sounds[i % len(sounds)]
            item["tiktok_sound"] = {k: s.get(k) for k in ("name", "artist", "song_clip_id", "preview_url", "genre")}
        else:
            item["tiktok_sound"] = None


def metricool_post(item: dict, settings: dict, brand_id: str, media_url: str, cover_ms: int | None,
                   networks: list[str] | None = None, remind: bool = False) -> dict:
    """Arguments for Metricool's createScheduledPost: one post to every chosen
    platform (or ``networks``) at the reel's slot (a draft to approve in
    "review" mode), with its first comment, as a Trial Reel on Instagram when
    the reel is one, inviting its collaborator. ``remind``: Metricool's app
    reminds the user at that time to post it themselves (autoPublish off)."""
    tz = item["tz"]
    dt = datetime.fromisoformat(item["slot"])
    nets = networks or settings["platforms"]
    comment = first_comment(item.get("first_comment")) if settings["first_comment"] else ""
    info: dict = {
        "autoPublish": not remind, "descendants": [], "draft": settings["approval"] == "review",
        "firstCommentText": comment, "hasNotReadNotes": False, "media": [media_url], "mediaAltText": [],
        "providers": [{"network": n} for n in nets],
        "publicationDate": {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:00"), "timezone": tz},
        "shortener": False, "smartLinkData": {"ids": []}, "text": reel_text(item, settings),
    }
    if cover_ms is not None:
        info["videoCoverMilliseconds"] = int(cover_ms)
    if "instagram" in nets:
        ig = {"type": "REEL", "showReelOnFeed": True, "isAiGenerated": settings["ai_label"]}
        if item.get("trial") and not item.get("collaborator"):
            ig = {"type": "TRIAL_REEL", "isAiGenerated": settings["ai_label"]}
        if item.get("collaborator"):
            ig["collaborators"] = [{"username": item["collaborator"], "deleted": False}]
        info["instagramData"] = ig
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


def metricool_posts(item: dict, settings: dict, brand_id: str, media_url: str, cover_ms: int | None) -> list[dict]:
    """The reel's createScheduledPost arguments: one post, or, in "remind"
    mode, the other platforms' post and a TikTok post that Metricool's app
    reminds the user to publish (to add the trending sound at volume 0).
    Each has ``part``: "main" or "tiktok"."""
    nets = settings["platforms"]
    if settings["tiktok_sound"] != "remind" or "tiktok" not in nets:
        return [{**metricool_post(item, settings, brand_id, media_url, cover_ms), "part": "main"}]
    rest = [n for n in nets if n != "tiktok"]
    posts = [{**metricool_post(item, settings, brand_id, media_url, cover_ms, rest), "part": "main"}] if rest else []
    return posts + [{**metricool_post(item, settings, brand_id, media_url, cover_ms, ["tiktok"], remind=True),
                     "part": "tiktok"}]
