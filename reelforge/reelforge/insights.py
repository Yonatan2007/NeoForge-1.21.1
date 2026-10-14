"""How the autopilot's reels did, and what the next batch learns from it.

Claude reads Metricool's analytics with the user's connector
(``getAnalyticsDataByMetrics`` with ``codes(network)``, and the follower
counts in ``FOLLOWERS``) and runs ``panelrun autopilot-insights``, which:

* matches every Instagram, TikTok and YouTube post to its autopilot reel (by
  its time, then its text);
* scores each reel against the account's usual numbers on each platform
  (views, and likes, comments, saves and shares per view);
* writes ``learnings``: the hooks that did best and worst with their numbers,
  how long people watch, which lengths and hours did well. Gemini reads them
  before writing the next batch and the panel shows them ("What's working");
* decides what happens to Trial Reels (Instagram shows them to non-followers
  first) once they have run 48 hours: the ones that beat the account's usual
  views are posted again as normal reels, so followers see them too; a trial
  Instagram refused is posted again right away. Instagram only offers Trial
  Reels to bigger accounts (``TRIAL_FOLLOWERS``): until then ``trial_eligible``
  is false and autopilot posts normal reels.

The result is the panel document ``insights/latest``.
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import autopilot as ap

# Metricool field ids, in the order they are requested (getAnalyticsDataByMetrics answers rows in that order)
METRICS = {
    "instagram": (("date", "IGRE02"), ("text", "IGRE03"), ("url", "IGRE06"), ("views", "IGRE23"),
                  ("reach", "IGRE11"), ("likes", "IGRE10"), ("comments", "IGRE07"), ("saves", "IGRE12"),
                  ("shares", "IGRE21"), ("watch", "IGRE24"), ("view_rate", "IGRE28")),
    "tiktok": (("date", "TKPO02"), ("text", "TKPO05"), ("url", "TKPO03"), ("duration", "TKPO06"),
               ("views", "TKPO07"), ("reach", "TKPO11"), ("likes", "TKPO08"), ("comments", "TKPO09"),
               ("shares", "TKPO10"), ("watch", "TKPO15"), ("full_rate", "TKPO13")),
    "youtube": (("date", "YTVP02"), ("text", "YTVP17"), ("url", "YTVP05"), ("views", "YTVP06"),
                ("likes", "YTVP09"), ("comments", "YTVP11"), ("shares", "YTVP12"), ("watch", "YTVP08")),
}
FOLLOWERS = {"instagram": "IGEV01", "tiktok": "TKEV07"}
LABEL = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
TEXT = ("date", "text", "url")
SETTLE = timedelta(hours=24)      # younger posts are still growing: not ranked, not in the usual numbers
TRIAL_RUN = timedelta(hours=48)   # Instagram tests a Trial Reel on non-followers this long before it's judged
TRIAL_WIN = 1.5                   # a trial that gets this many times the usual views is posted to followers
TRIAL_GIVE_UP = timedelta(days=8)


def codes(network: str) -> list[str]:
    """The metric ids to request for ``network``, in the order ``rows_of`` reads them."""
    return [code for _, code in METRICS[network]]


def _num(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _stamp(s) -> datetime | None:
    """Metricool's "YYYYMMDDHHMMSS" (UTC) -> a naive UTC datetime."""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?$", str(s or ""))
    if not m:
        return None
    return datetime(*(int(g or 0) for g in m.groups()))


def rows_of(network: str, answer: dict | None) -> list[dict]:
    """The posts in a getAnalyticsDataByMetrics answer for ``codes(network)``."""
    out = []
    for r in (answer or {}).get("rows") or []:
        if not isinstance(r, list) or not r or not r[0]:
            continue
        post = {"network": network}
        for (name, _), v in zip(METRICS[network], r):
            post[name] = v if name in TEXT else _num(v)
        post["at"] = _stamp(post.get("date"))
        out.append(post)
    return out


def followers_of(answer: dict | None) -> int | None:
    """The latest follower count in an answer for one ``FOLLOWERS`` metric
    (rows are [value, ..., "YYYYMMDD"])."""
    best = None
    for r in (answer or {}).get("rows") or []:
        if isinstance(r, list) and len(r) >= 2 and _num(r[0]) is not None:
            day = str(r[-1])
            if best is None or day > best[0]:
                best = (day, int(_num(r[0])))
    return best[1] if best else None


def trial_eligible(followers: dict | None) -> bool:
    """Whether Instagram offers the account Trial Reels (it does for accounts of about 1,000 followers up)."""
    n = (followers or {}).get("instagram")
    return n is not None and n >= ap.TRIAL_FOLLOWERS


# --------------------------------------------------------------------------- matching

def _norm(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(text or "").lower())).strip()


def _slot_utc(reel: dict) -> datetime | None:
    try:
        local = datetime.fromisoformat(str(reel["slot"]))
        return local.replace(tzinfo=ZoneInfo(reel.get("tz") or "UTC")).astimezone(timezone.utc).replace(tzinfo=None)
    except (KeyError, ValueError):
        return None


def _same_text(post: dict, reel: dict) -> bool:
    text = _norm(post.get("text"))
    if not text:
        return False
    if post["network"] == "youtube":
        return text == _norm(reel.get("youtube_title"))
    caption = _norm(reel.get("caption"))
    return bool(caption) and caption[:60] in text


def match(posts: list[dict], reels: list[dict]) -> tuple[dict[str, dict[str, dict]], list[dict]]:
    """{reel id: {network: post}} for the posts that are autopilot reels
    (posted from 15 minutes before to 90 after the reel's slot, the one with
    the same text first; else the same text at any time), and the others."""
    when = {r["id"]: _slot_utc(r) for r in reels}
    found: dict[str, dict[str, dict]] = {}
    others = []
    for post in posts:
        near = []
        if post.get("at"):
            for r in reels:
                t = when.get(r["id"])
                if t and timedelta(minutes=-15) <= post["at"] - t <= timedelta(minutes=90):
                    near.append(r)
        near.sort(key=lambda r: (not _same_text(post, r), abs((post["at"] - when[r["id"]]).total_seconds())))
        pick = near[0] if near else next((r for r in reels if _same_text(post, r)), None)
        if pick is None or post["network"] in found.get(pick["id"], {}):
            others.append(post)
            continue
        found.setdefault(pick["id"], {})[post["network"]] = post
    return found, others


# --------------------------------------------------------------------------- scoring

def _rate(post: dict) -> float | None:
    if not post.get("views"):
        return None
    return sum(post.get(k) or 0 for k in ("likes", "comments", "saves", "shares")) / post["views"]


def usual(posts: list[dict], now: datetime) -> dict[str, dict]:
    """Per network, the median views and engagement rate of the posts old enough to judge."""
    out = {}
    for net in METRICS:
        settled = [p for p in posts if p["network"] == net and p.get("views") is not None
                   and (p.get("at") is None or now - p["at"] >= SETTLE)]
        if not settled:
            continue
        rates = [r for r in (_rate(p) for p in settled) if r is not None]
        out[net] = {"views": statistics.median(p["views"] for p in settled),
                    "rate": statistics.median(rates) if rates else None, "posts": len(settled)}
    return out


def _score(post: dict, base: dict) -> float:
    """How a post did against the network's usual: log2 of its views over the
    usual, plus half the log2 of its engagement rate over the usual."""
    lift = math.log2((post["views"] + 1) / (base["views"] + 1))
    rate = _rate(post)
    if rate is not None and base.get("rate") is not None:
        lift += 0.5 * math.log2((rate + 0.002) / (base["rate"] + 0.002))
    return lift


def _hook(text) -> str:
    """The first sentence: what is on screen first."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    m = re.match(r"^(.+?[.?!…])(\s|$)", text)
    return (m.group(1) if m else text)[:160]


def _stats(post: dict) -> dict:
    return {k: post.get(k) for k in ("views", "likes", "comments", "saves", "shares", "reach", "watch",
                                     "view_rate", "full_rate", "url") if post.get(k) is not None}


def entries(found: dict[str, dict[str, dict]], others: list[dict], reels: list[dict], base: dict,
            now: datetime) -> list[dict]:
    """Every reel (and other post) old enough to judge, with its score and
    numbers, best first. A reel scores what it did on its best platform: one
    that takes off on TikTok alone is a hit. Other posts (made outside
    autopilot) are one entry per video, its platforms matched by their text."""
    by_id = {r["id"]: r for r in reels}
    out = []

    def entry(posts: dict[str, dict], reel: dict | None) -> dict | None:
        scored = {net: _score(p, base[net]) for net, p in posts.items()
                  if p.get("views") is not None and net in base and (p.get("at") is None or now - p["at"] >= SETTLE)}
        if not scored:
            return None
        best = max(scored, key=scored.get)
        first = next(iter(posts.values()))
        hook = _hook(reel["script"]) if reel and reel.get("script") else _hook(first.get("text"))
        times = {net: round((posts[net]["views"] + 1) / (base[net]["views"] + 1), 2)
                 for net in sorted(scored, key=scored.get, reverse=True)}
        return {"id": reel["id"] if reel else None, "title": (reel or {}).get("title") or hook[:60], "hook": hook,
                "slot": (reel or {}).get("slot"), "duration": (reel or {}).get("duration") or first.get("duration"),
                "score": round(scored[best], 3),
                "best": {"network": best, "views": posts[best]["views"], "times": times[best]},
                "times": times, "stats": {net: _stats(p) for net, p in posts.items()}}

    for rid, posts in found.items():
        e = entry(posts, by_id.get(rid))
        if e:
            out.append(e)
    videos: dict[str, dict[str, dict]] = {}
    for p in others:
        key = _norm(_hook(p.get("text")))[:40]
        group = videos.setdefault(key, {})
        if p["network"] not in group:
            group[p["network"]] = p
        else:
            videos[f"{key}#{len(videos)}"] = {p["network"]: p}
    for posts in videos.values():
        e = entry(posts, None)
        if e:
            out.append(e)
    return sorted(out, key=lambda e: -e["score"])


def _count(n: float | None) -> str:
    return "?" if n is None else f"{int(round(n)):,}"


def _times(x: float) -> str:
    if x < 0.1:
        return "under a tenth of"
    return f"{x:.0f}×" if x >= 10 else f"{x:.1f}×"


def _many(n, word: str) -> str:
    """'1 save', '3 saves'."""
    return f"{_count(n)} {word[:-1] if n == 1 else word}"


def _line(kind: str, e: dict) -> str:
    """'Did best: "<hook>" — TikTok 87,191 views (181× the usual; 18,822 likes, ...), Instagram 166 views (1.2×).'"""
    parts = []
    for n, (net, x) in enumerate(e["times"].items()):
        st = e["stats"][net]
        extra = ", ".join(_many(st[k], k) for k in ("likes", "shares", "saves", "comments") if st.get(k))
        usual_word = " the usual" if n == 0 or x < 0.1 else ""
        parts.append(f"{LABEL[net]} {_many(st.get('views'), 'views')} ({_times(x)}{usual_word}"
                     f"{'; ' + extra if extra and n == 0 else ''})")
    return f'{kind}: "{e["hook"]}" — {", ".join(parts)}.'


def learnings(ranked: list[dict], posts: list[dict], tz: str, top: int = 3) -> list[str]:
    """Plain lines for Gemini's brief and the panel: the best and weakest
    reels, how long people watch, and which lengths and hours did better."""
    lines = [_line("Did best", e) for e in ranked[:top]]
    if len(ranked) > top:
        lines += [_line("Did least", e) for e in ranked[-top:][::-1] if e not in ranked[:top]]
    ig = [p for p in posts if p["network"] == "instagram" and p.get("watch")]
    long = [e["duration"] for e in ranked if e.get("duration")]
    if len(ig) >= 3:
        watch = statistics.median(p["watch"] for p in ig)
        of = f" of a {statistics.median(long):.0f} s reel" if long else ""
        lines.append(f"Instagram viewers watch {watch:.1f} s{of} on average: the first two seconds decide whether "
                     "they stay.")
    tt = [p["full_rate"] for p in posts if p["network"] == "tiktok" and p.get("full_rate") is not None]
    if len(tt) >= 3:
        lines.append(f"On TikTok {statistics.median(tt):.0f} % of viewers watch to the end.")
    timed = [e for e in ranked if e.get("duration")]
    short = [e["score"] for e in timed if e["duration"] < 35]
    longer = [e["score"] for e in timed if e["duration"] >= 35]
    if len(short) >= 3 and len(longer) >= 3:
        gap = statistics.median(short) - statistics.median(longer)
        if abs(gap) >= 0.5:
            better = "under 35 s" if gap > 0 else "35 s and longer"
            lines.append(f"Reels {better} did {_times(2 ** abs(gap))} better than the others.")
    slotted = [e for e in ranked if e.get("slot")]
    if len(slotted) >= 9:
        hours: dict[int, list[float]] = {}
        for e in slotted:
            hours.setdefault(datetime.fromisoformat(e["slot"]).hour, []).append(e["score"])
        means = {h: sum(v) / len(v) for h, v in hours.items() if len(v) >= 2}
        if len(means) >= 2:
            hi, lo = max(means, key=means.get), min(means, key=means.get)
            if means[hi] - means[lo] >= 0.5:
                lines.append(f"Posts at {hi:02d}:00 did best and at {lo:02d}:00 worst ({tz} time).")
    return lines


# --------------------------------------------------------------------------- trial reels

def _ig_status(live: dict | None) -> str | None:
    for p in (live or {}).get("providers") or []:
        if p.get("network") == "instagram":
            return str(p.get("status") or "").upper()
    return None


def trials(reels: list[dict], found: dict[str, dict[str, dict]], live: dict[str, dict], base: dict,
           prior: dict | None, now: datetime) -> tuple[dict[str, dict], list[str]]:
    """Each Trial Reel's state ({status: "running" | "shared" | "kept" |
    "failed", reason}) and the reels to post again as normal Instagram reels:
    a trial Instagram refused at once, a trial that beat the usual views after
    48 hours. Decided ones stay decided (``prior``: the last ``trials``)."""
    out = dict(prior or {})
    again = []
    usual_views = (base.get("instagram") or {}).get("views")
    for r in reels:
        if not r.get("trial") or (out.get(r["id"]) or {}).get("status") in ("shared", "kept", "failed"):
            continue
        uuid = str((r.get("metricool") or {}).get("uuid") or "")
        post = (found.get(r["id"]) or {}).get("instagram")
        started = _slot_utc(r)
        if _ig_status(live.get(uuid)) in ("ERROR", "FAILED"):
            out[r["id"]] = {"status": "failed", "reason": "Instagram didn't take it as a Trial Reel; posted as a normal reel."}
            again.append(r["id"])
        elif post and post.get("views") is not None and started and now - started >= TRIAL_RUN:
            if usual_views is not None and post["views"] >= max(TRIAL_WIN * usual_views, usual_views + 25):
                out[r["id"]] = {"status": "shared", "views": post["views"],
                                "reason": f"{_count(post['views'])} views from non-followers, "
                                          f"{_times((post['views'] + 1) / (usual_views + 1))} the usual: posted for followers."}
                again.append(r["id"])
            else:
                out[r["id"]] = {"status": "kept", "views": post["views"],
                                "reason": f"{_count(post['views'])} views from non-followers: not posted again."}
        elif started and now - started >= TRIAL_GIVE_UP:
            out[r["id"]] = {"status": "kept", "reason": "No numbers came back for it."}
        elif started and started <= now:
            out[r["id"]] = {"status": "running"}
    return out, again


def next_free(taken: list[datetime], now: datetime, window: list[str], gap: float = 2.0) -> datetime:
    """The best-placed full hour (``ap.FALLBACK_HOURS`` order) inside ``window``
    in the next two days, at least an hour from now and ``gap`` hours from
    every ``taken`` time (all wall-clock times of the brand)."""
    start, end = (int(x[:2]) * 60 + int(x[3:5]) for x in window)
    rank = {h: n for n, h in enumerate(ap.FALLBACK_HOURS)}
    for day in range(3):
        d = (now + timedelta(days=day)).date()
        cands = [datetime(d.year, d.month, d.day, h) for h in sorted(rank, key=rank.get) if start <= h * 60 <= end]
        cands = [c for c in cands if c >= now + timedelta(hours=1)
                 and all(abs((c - t).total_seconds()) >= gap * 3600 for t in taken)]
        if cands:
            return cands[0]
    return (now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)


def reposts(ids: list[str], reels: list[dict], settings: dict, brand_id: str, taken: list[datetime],
            now: datetime) -> list[dict]:
    """createScheduledPost arguments that post these trial reels again as
    normal Instagram reels, each at the next free hour."""
    by_id = {r["id"]: r for r in reels}
    out = []
    busy = list(taken)
    for rid in ids:
        r = by_id[rid]
        if not r.get("master_url"):
            continue
        at = next_free(busy, now, settings["window"])
        busy.append(at)
        item = {**r, "id": rid, "slot": at.strftime("%Y-%m-%dT%H:%M"), "trial": False, "collaborator": None}
        post = ap.metricool_post(item, settings, brand_id, r["master_url"], r.get("cover_ms"), ["instagram"])
        out.append({**post, "part": "repost"})
    return out


# --------------------------------------------------------------------------- the panel document

def build(stats: dict, reels: list[dict], *, tz: str, now: datetime, followers: dict | None = None,
          live: list[dict] | None = None, prior: dict | None = None, settings: dict | None = None,
          brand_id: str = "") -> tuple[dict, list[dict]]:
    """(``insights/latest``, reposts). ``stats``: {network: getAnalyticsDataByMetrics answer};
    ``reels``: the autopilot documents (with ``id``); ``now`` in UTC; ``live``:
    getScheduledPosts data (to see refused trials, and the hours already taken);
    ``prior``: the last ``insights/latest``."""
    settings = ap.settings_of(settings)
    posts = [p for net in METRICS for p in rows_of(net, (stats or {}).get(net))]
    found, others = match(posts, reels)
    base = usual(posts, now)
    ranked = entries(found, others, reels, base, now)
    mine = [e for e in ranked if e["id"]]
    live_by_uuid = {str(p.get("uuid")): p for p in live or []}
    trial_state, again = trials(reels, found, live_by_uuid, base, (prior or {}).get("trials"), now)
    local_now = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    taken = ap.taken_slots(live or [], tz)
    posts_again = reposts(again, reels, settings, brand_id, taken, local_now)
    for p in posts_again:
        trial_state[p["slot_id"]]["repost_at"] = p["date"]
    doc = {
        "updated": now.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "followers": {k: v for k, v in (followers or {}).items() if v is not None},
        "trial_eligible": trial_eligible(followers),
        "trial_followers": ap.TRIAL_FOLLOWERS,
        "usual": {net: {"views": round(b["views"], 1), "rate": round(b["rate"], 4) if b.get("rate") is not None else None,
                        "posts": b["posts"]} for net, b in base.items()},
        "judged": len(ranked),
        "top": ranked[:3],
        "bottom": [e for e in ranked[-3:][::-1] if e not in ranked[:3]],
        "learnings": learnings(ranked, posts, tz),
        "reels": {e["id"]: {"score": e["score"], "stats": e["stats"]} for e in mine},
        "trials": trial_state,
    }
    # reels too new to judge still show their numbers so far
    for rid, by_net in found.items():
        doc["reels"].setdefault(rid, {"score": None, "stats": {net: _stats(p) for net, p in by_net.items()}})
    return doc, posts_again
