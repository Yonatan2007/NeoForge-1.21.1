"""Claude's side of the reelforge control panel (``panel/``).

The panel keeps each reel in its Artifact database (``projects/<id>``) and
its files in the Artifact's asset store; a plan or render is a task
(``tasks/<id>``) that Claude carries out with these commands:

    python -m reelforge.panelrun stage  DOC.json --assets DIR --work DIR
        Turn the project document (as ArtifactData saved it) into a project
        folder. Prints {"need": [asset ids]} until every file is in DIR.
    python -m reelforge.panelrun plan   --work DIR            -> DIR/plan.json
    python -m reelforge.panelrun review --work DIR            -> DIR/review/review.json
        Stock clips to choose from before rendering, with DIR/review/review.jpg
        (all their thumbnails on one sheet) to upload to the asset store.
    python -m reelforge.panelrun ideas  TASK.json
        Script ideas from Gemini for an "ideas" task (topic, count, tone,
        avoid, examples); needs GEMINI_API_KEY in the environment.

Autopilot (reels made and scheduled in batches, see autopilot.py):

    python -m reelforge.panelrun autopilot-prepare IN.json --assets DIR --out DIR
        Slots at the best hours, Gemini's scripts and posts, a work folder per
        reel with its footage planned -> DIR/plan.json (with each reel's
        Higgsfield voice request).
    python -m reelforge.panelrun autopilot-render --plan PLAN --voices VOICES.json [--jobs 2]
        Render every reel that has a voice URL, two at a time, and check them
        -> results.json next to the plan.
        Also covers.jpg: every reel's opening with its hook, numbered, to look over.
    python -m reelforge.panelrun autopilot-swap --plan PLAN --ids ID[,ID...] [--choices FILE]
        Another opening clip for those reels (the next spare, or the clip FILE
        names per reel); autopilot-render then makes them again.
    python -m reelforge.panelrun autopilot-posts --plan PLAN --results RES --media MEDIA.json
        Metricool createScheduledPost arguments for the reels that passed.
    python -m reelforge.panelrun autopilot-videos --plan PLAN --results RES
        For an account posted by hand: each passed reel's video to upload to
        the asset store -> videos-upload.json (under the asset size limit).
    python -m reelforge.panelrun autopilot-docs --plan PLAN --results RES --media MEDIA.json
                                               --covers COVERS.json --metricool MC.json
        The panel's queue documents (autopilot/<slot id>) as an ArtifactData batch.
        An account posted by hand takes --videos VIDEOS.json (the uploaded
        videos' asset ids) instead of --media and --metricool.
    python -m reelforge.panelrun autopilot-insights --codes
        The Metricool metric ids to read for the next command.
    python -m reelforge.panelrun autopilot-insights IN.json --out DIR
        How the reels did and what the next batch learns -> DIR/insights.json
        (the panel's insights/latest) and DIR/reposts.json (Trial Reels to
        post again as normal reels).
    python -m reelforge.panelrun render --work DIR [--voiceover URL]
        Renders; DIR/progress.json follows it, a DIR/cancel file stops it,
        DIR/result.json holds the result (or the error).
    python -m reelforge.panelrun status --work DIR
        The task fields to write back: {status, stage, progress, message, log}.
    python -m reelforge.panelrun outputs --work DIR
        Files to upload to the asset store, keyed like the panel's outputs
        (the video re-encoded under the asset size limit when needed).
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import config, media
from .config import Project, Settings

ASSET_LIMIT = 18 * 1024 * 1024   # the asset store takes 20 MiB per file; keep a margin
BATCH_DEPTH = 30                 # clips read per stock search for a batch of reels (one reel: 8)
NEAR_HOURS = 48                  # reels this close in time never share a clip
LOG_LINES = 200


def _doc_body(raw: dict) -> dict:
    """ArtifactData's saved file may wrap the document ({id, version, data})."""
    for key in ("data", "document", "fields"):
        if isinstance(raw.get(key), dict) and "project" in raw[key]:
            return raw[key]
    return raw


def _asset_file(assets: Path, asset_id: str) -> Path | None:
    hits = sorted(p for p in assets.rglob(f"{asset_id}*") if p.is_file())
    return hits[0] if hits else None


def _missing(files: list[dict], assets: Path) -> list[str]:
    return [i for f in files for i in (f.get("store") or {}).get("ids", []) if not _asset_file(assets, i)]


def _unpack(files: list[dict], assets: Path, up: Path) -> None:
    """Write the panel's file records (raw or base64 parts in ``assets``) into ``up``."""
    up.mkdir(parents=True, exist_ok=True)
    for f in files:
        store = f.get("store") or {}
        parts = [_asset_file(assets, i) for i in store.get("ids", [])]
        dst = up / f["name"]
        if store.get("type") == "b64":
            with open(dst, "wb") as out:
                for part in parts:
                    out.write(base64.b64decode("".join(part.read_text(encoding="utf-8").split())))
        elif parts:
            dst.write_bytes(parts[0].read_bytes())


def stage(doc_path: Path, assets: Path, work: Path) -> dict:
    doc = _doc_body(json.loads(doc_path.read_text(encoding="utf-8")))
    files = doc.get("files") or []
    need = _missing(files, assets)
    if need:
        return {"need": need}
    up = work / "uploads"
    _unpack(files, assets, up)
    project = config.from_dict(Project, doc["project"])
    config.save_project(project, work / "project.json")
    out = {"ready": True, "work": str(work), "files": sorted(p.name for p in up.iterdir()),
           "voice": project.voice.source, "script_words": len(project.script.split())}
    if project.voice.source == "higgsfield" and not project.voice.file:
        # Claude makes the voice first: this is the generate_audio call to make
        from . import voiceover
        from .script import parse_script
        script = parse_script(project.script, project.auto_emphasis)
        if script.words:
            out["higgsfield_request"] = voiceover.higgsfield_request(
                script, project.voice.higgsfield_preset,
                project.voice.voice_id or Settings.from_env().higgsfield_voice_id,
                voice_type=project.voice.voice_type if project.voice.voice_id else "preset")
    return out


def _load(work: Path, voiceover: str | None = None) -> Project:
    project = config.load_project(work / "project.json")
    if voiceover:
        project.voice.source, project.voice.file = "file", voiceover
    return project


def _settings(work: Path) -> Settings:
    s = Settings.from_env()
    s.cache_dir = Path(s.cache_dir) if s.cache_dir else work.parent / ".cache"
    return s


def plan(work: Path) -> dict:
    from .pipeline import plan_project
    result = plan_project(_load(work), work, _settings(work))
    (work / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def review(work: Path) -> dict:
    from .pipeline import review_footage
    out_dir = work / "review"
    try:
        result = review_footage(_load(work), work, _settings(work), out_dir)
    except Exception as exc:  # reported to the panel
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "sheet": str(out_dir / result["sheet"]) if result.get("sheet") else None,
            "json": str(out_dir / "review.json"), "opening": len(result["opening"]),
            "pool": len(result["pool"]), "stock_shots": result["stock_shots"], "warnings": result["warnings"]}


def ideas(task_path: Path) -> dict:
    from . import ideas as gemini
    raw = json.loads(task_path.read_text(encoding="utf-8"))
    task = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    try:
        result = gemini.generate(topic=task.get("topic") or "", count=task.get("count") or 5,
                                 tone=task.get("tone") or "", avoid=task.get("avoid") or [],
                                 examples=task.get("examples") or [])
    except (gemini.IdeasError, OSError, ValueError) as exc:  # network errors are OSErrors
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "result": result}


# --------------------------------------------------------------------------- autopilot

def _plain(text) -> str:
    """Text compared without case, punctuation or extra spaces."""
    return " ".join(re.findall(r"\w+", str(text or "").lower()))


def _read(path: Path | str) -> dict | list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def autopilot_prepare(inp: Path, assets: Path, out: Path, writer=None, searcher=None, line_writer=None) -> dict:
    """Plan a batch for one account (``account``, default the main one):
    slots, scripts with their posts, a work folder per reel (project.json and
    uploads) with its stock clips assigned. The settings' ``mix`` makes some
    reels comment videos (one line on screen, no voice). Gemini writes for
    the account's niche, learns from ``insights`` (the account's insights
    document), answers the viewers' comments in ``replies`` first, and builds
    the posts around real searches (``search_phrases``, or YouTube's
    suggestions); each reel then gets its extras (trial, collaborator,
    TikTok sound from ``sounds``). The hours already taken are Metricool's
    ``scheduled`` posts and ``queued``: the slots ("YYYY-MM-DDTHH:MM") of the
    account's reels still waiting to be posted by hand. An account posted by
    hand has no brand: its times are in the settings' ``tz``."""
    import random
    import secrets
    from datetime import datetime, timedelta

    from . import autopilot as ap, ideas, niches, search
    from .pipeline import slugify

    data = _read(inp)
    settings = ap.settings_of(data.get("settings"))
    account = ap.account_key(data.get("account"))
    niche = settings["niche"]
    brand = data.get("brand") or {}
    tz = brand.get("timezone") or settings["tz"] or "UTC"
    template = _doc_body(data["template"])
    now = datetime.fromisoformat(data["now"]) if data.get("now") else ap.now_in(tz)
    best = data.get("best") or {}
    grid = ap.combine([ap.grid_of(best.get(p)) for p in settings["platforms"] if best.get(p)])
    taken = set(ap.taken_slots(data.get("scheduled") or [], tz))
    for slot in data.get("queued") or []:
        try:
            taken.add(datetime.fromisoformat(str(slot)[:16]))
        except ValueError:
            continue
    taken = sorted(taken)
    slots = ap.pick_slots(grid, now.date(), settings["days"], settings["per_day"], settings["window"],
                          settings["min_gap"], taken, earliest=now + timedelta(hours=3))
    history = data.get("history") or []
    template_project = template["project"]
    tsong = (template_project.get("music") or {}).get("file")
    kinds = ap.kinds_for(len(slots), settings["mix"])
    # each kind from its half of the account's library; the same picks when this runs again after
    # fetching files (seeded by the day)
    songs = ap.pick_songs(settings, kinds, tsong, data.get("library"), [h["song"] for h in history if h.get("song")],
                          random.Random(str(data.get("seed") or now.date())), account=account)
    picked = {song["name"] for song in songs if isinstance(song, dict)}
    library = [{"name": song["name"], "store": song["store"]} for song in ap.library_songs(data.get("library"))
               if song["name"] in picked]
    files = list({f["name"]: f for f in [*(template.get("files") or []), *(data.get("files") or []), *library]}.values())
    need = _missing(files, assets)
    if need:
        return {"need": need}
    recent = [k for h in history[-40:] for k in h.get("clips") or []]
    plan = {"tz": tz, "brand_id": str(brand.get("id") or ""), "settings": settings, "account": account, "niche": niche,
            "template_id": data["template"].get("id") or data.get("template_id"), "model": None,
            "files": files, "items": [],
            "banned": sorted(set((template_project.get("footage") or {}).get("banned") or []) | set(recent))}
    if not slots:
        _write(out / "plan.json", plan)
        return {"ok": True, "slots": 0, "plan": str(out / "plan.json")}

    # scripts and posts: Gemini (or ideas the caller wrote, when the user chose that)
    avoid = [*(data.get("avoid") or []), *(h.get("title", "") for h in history)]
    written = [i for i in data.get("ideas") or [] if i.get("kind", "watch") == "watch"]
    lines = [i for i in data.get("ideas") or [] if i.get("kind") == "comment"]
    posted = {_plain(a) for a in avoid}  # the niche's own first lines, until each has been used once
    lines = [{"kind": "comment", "title": f["line"].rstrip(". "), "script": f["line"], "why": "",
              "caption": f.get("caption") or "Be honest.", "youtube_title": f["line"], "hashtags": [],
              "first_comment": f.get("first_comment") or ""}
             for f in niches.get(niche).first_lines if _plain(f["line"]) not in posted][:kinds.count("comment")] + lines
    writer = writer or ideas.generate
    line_writer = line_writer or ideas.generate_lines
    insights = data.get("insights") or {}
    phrases = data.get("search_phrases")
    if phrases is None and settings["search"]:
        phrases = (searcher or search.phrases)(settings["topic"], seeds=search.seeds_for(settings["topic"],
                                                                                        niches.get(niche).seeds))
    answered = {h.get("reply_to") for h in history if h.get("reply_to")}
    replies = [r for r in data.get("replies") or [] if r.get("id") and r.get("comment") and r["id"] not in answered]
    replies = replies[:max(0, kinds.count("watch") - len(written))]
    brief = dict(topic=settings["topic"], tone=settings["tone"], examples=data.get("examples") or [], post=True,
                 length=settings["length"], comment=settings["first_comment"], search=settings["search"],
                 phrases=phrases or [], learnings=insights.get("learnings") or [], hashtags=settings["hashtags"],
                 niche=niche)
    used = lambda: [*avoid, *(w["title"] for w in written + lines)]  # noqa: E731
    if replies:  # video replies to viewers' comments come first, one script each
        got = writer(count=len(replies), avoid=used(), replies=replies, **brief)
        plan["model"] = got.get("model")
        for r, idea in zip(replies, got.get("ideas") or []):
            written.append({**idea, "reply_to": r["id"]})
    while len(written) < kinds.count("watch"):
        got = writer(count=min(8, kinds.count("watch") - len(written)), avoid=used(), **brief)
        plan["model"] = got.get("model")
        if not got.get("ideas"):
            raise ideas.IdeasError("Gemini returned no scripts.")
        written += got["ideas"]
    while len(lines) < kinds.count("comment"):
        got = line_writer(count=min(10, kinds.count("comment") - len(lines)), avoid=[*used(), *(w["script"] for w in lines)],
                          **{k: v for k, v in brief.items() if k not in ("examples", "post", "length")})
        plan["model"] = plan["model"] or got.get("model")
        if not got.get("ideas"):
            raise ideas.IdeasError("Gemini returned no comment lines.")
        lines += [{**i, "kind": "comment"} for i in got["ideas"]]
    queue = {"watch": iter(written), "comment": iter(lines)}
    written = [next(queue[k]) for k in kinds]

    up = out / "_uploads"
    _unpack(files, assets, up)
    for i, (slot, idea, kind) in enumerate(zip(slots, written, kinds)):
        sid = ap.slot_id(slot, account)
        work = out / sid
        (work / "uploads").mkdir(parents=True, exist_ok=True)
        for f in up.iterdir():
            link = work / "uploads" / f.name
            if not link.exists():
                link.symlink_to(f.resolve())
        build = ap.build_comment_project if kind == "comment" else ap.build_project
        project = build(template_project, idea["script"], idea["title"], music=songs[i], banned=recent,
                        fmt=settings["format"], niche=niche)
        config.save_project(config.from_dict(Project, project), work / "project.json")
        plan["items"].append({
            "id": sid, "account": account, "kind": kind, "slot": slot.strftime("%Y-%m-%dT%H:%M"), "tz": tz,
            "title": idea["title"],
            "script": idea["script"], "why": idea.get("why", ""), "caption": idea.get("caption") or idea["title"],
            "hashtags": idea.get("hashtags") or [], "youtube_title": idea.get("youtube_title") or idea["title"],
            "first_comment": ap.first_comment(idea.get("first_comment")), "search_phrase": idea.get("search_phrase"),
            "reply_to": idea.get("reply_to"),
            "work": str(work), "project_id": f"{slugify(idea['title'], 5)}-{secrets.token_hex(3)}",
            "song": project["music"].get("file")})
    ap.plan_extras(plan["items"], settings, trial_ok=bool(insights.get("trial_eligible")), history=history,
                   sounds=data.get("sounds") or [])
    plan["phrases"] = len(phrases or [])

    _plan_footage(plan)
    _write(out / "plan.json", plan)
    items = plan["items"]
    return {"ok": True, "account": account, "slots": len(items), "plan": str(out / "plan.json"),
            "first": items[0]["slot"], "last": items[-1]["slot"], "model": plan["model"],
            "comment_videos": sum(1 for it in items if it["kind"] == "comment"),
            "replies": sum(1 for it in items if it.get("reply_to")), "trials": sum(1 for it in items if it["trial"]),
            "collabs": sum(1 for it in items if it["collaborator"]), "search_phrases": plan["phrases"]}


def _plan_footage(plan: dict) -> None:
    """Stock clips for every reel of ``plan``, one reel after another: each
    searches without the clips the reels before it got (nor ``plan.banned``:
    the template's and recent reels'), so every reel has its own opening and
    clips; the other opening clips it found are kept for swaps. Then each
    reel's own render rules out every other reel's clips."""
    from . import autopilot as ap, voiceover
    from .pipeline import review_footage
    from .script import parse_script

    from datetime import datetime

    base = set(plan.get("banned") or [])
    used: set[str] = set()
    when: dict[str, list[datetime]] = {}  # clip -> slots of the reels using it
    st = None

    def review_with(project, work, banned):
        project.footage.picks = []
        project.footage.banned = sorted(banned)
        config.save_project(project, work / "project.json")
        try:
            return review_footage(project, work, st, work / "review")
        except Exception as exc:  # the render searches by itself then
            logging.getLogger("reelforge").warning("footage plan for %s: %s", work.name, exc)
            return {}

    for item in plan["items"]:
        work = Path(item["work"])
        if st is None:
            st = _settings(work)
            st.mixkit_per_query = max(st.mixkit_per_query, BATCH_DEPTH)
        slot = datetime.fromisoformat(item["slot"])
        project = _load(work)
        extra = 0 if item.get("kind") == "comment" else 2  # one shot: no spare clips for a longer voice
        review = {**review_with(project, work, base | used), "extra": extra}
        picks = ap.assign_clips([review], banned=base | used)[0]
        rest = review.get("rest_shots")
        if len(picks) < (4 if rest is None else int(rest)) + 1:
            # the stock sites ran out of fresh clips: reuse ones from reels far away in time
            near = {k for k, slots in when.items() if any(abs((slot - t).total_seconds()) < NEAR_HOURS * 3600 for t in slots)}
            review = {**review_with(project, work, base | near), "extra": extra}
            picks = ap.assign_clips([review], banned=base | near)[0]
        item["openings"] = [c for c in ap.spare_openings([review], [picks])[0] if c["key"] not in used]
        used |= {p["key"] for p in picks}
        for p in picks:
            when.setdefault(p["key"], []).append(slot)
        project.footage.picks = picks
        config.save_project(project, work / "project.json")
        script = parse_script(project.script, project.auto_emphasis)
        item["picks"] = len(picks)
        if item.get("kind") == "comment":  # no voice
            item.pop("voice_request", None)
            continue
        item["voice_request"] = voiceover.higgsfield_request(
            script, project.voice.higgsfield_preset,
            project.voice.voice_id or Settings.from_env().higgsfield_voice_id,
            voice_type=project.voice.voice_type if project.voice.voice_id else "preset")
    for item in plan["items"]:
        work = Path(item["work"])
        project = _load(work)
        own = {p["key"] for p in project.footage.picks}
        project.footage.banned = sorted(base | (used - own))
        config.save_project(project, work / "project.json")


def autopilot_footage(plan_path: Path) -> dict:
    """Plan the stock clips of an existing plan again (scripts and slots stay)."""
    plan = _read(plan_path)
    _plan_footage(plan)
    _write(plan_path, plan)
    return {"ok": True, "picks": {it["id"]: it["picks"] for it in plan["items"]},
            "openings": {it["id"]: len(it["openings"]) for it in plan["items"]}}


def autopilot_render(plan_path: Path, voices_path: Path, jobs: int = 2) -> dict:
    """Render every reel of the plan that has a voice (comment videos need
    none), ``jobs`` at a time (each lane with its own cache), check each one
    -> results.json."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    from . import autopilot as ap

    plan = _read(plan_path)
    voices = _read(voices_path) if Path(voices_path).exists() else {}
    out = plan_path.parent / "results.json"
    results = _read(out) if out.exists() else {}
    todo = [it for it in plan["items"] if (voices.get(it["id"]) or it.get("kind") == "comment")
            and not (results.get(it["id"]) or {}).get("ok")]
    lock = threading.Lock()

    def one(n_item):
        n, item = n_item
        work = Path(item["work"])
        env = {**os.environ, "REELFORGE_CACHE": str(plan_path.parent / f".cache-{n % max(1, jobs)}"),
               "REELFORGE_MIXKIT_PER_QUERY": str(BATCH_DEPTH),
               "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        voice = [] if item.get("kind") == "comment" else ["--voiceover", voices[item["id"]]]
        subprocess.run([sys.executable, "-m", "reelforge.panelrun", "render", "--work", str(work), *voice],
                       env=env, capture_output=True, text=True)
        try:
            res = json.loads((work / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            res = {"ok": False, "error": "the render stopped without a result"}
        try:
            used = json.loads((work / "output" / "credits.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            used = []
        r = res.get("result") or {}
        try:
            wide_ok = bool(_load(work).footage.allow_landscape)
        except (OSError, ValueError):
            wide_ok = False
        entry = {"ok": bool(res.get("ok")),
                 "problems": ap.quality_problems(res, used, kind=item.get("kind", "watch"), wide_ok=wide_ok),
                 "video": str(work / r["video"]) if r.get("video") else None,
                 "cover": str(work / r["cover"]) if r.get("cover") else None,
                 "cover_ms": int(float(r.get("cover_time") or 0) * 1000) if res.get("ok") else None,
                 "duration": r.get("duration"), "clips": [f"{c['provider']}_{c['id']}" for c in used]}
        with lock:
            results[item["id"]] = entry
            _write(out, results)
        return item["id"], entry

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        done = list(pool.map(one, enumerate(todo)))
    good = [i for i, e in done if e["ok"] and not e["problems"]]
    sheet = _covers_sheet(plan, results, plan_path.parent / "covers.jpg")
    return {"ok": True, "rendered": len(done), "passed": len(good),
            "held": {i: e["problems"] for i, e in done if e["problems"]}, "results": str(out),
            "covers": str(sheet) if sheet else None}


def _covers_sheet(plan: dict, results: dict, out: Path, width: int = 240, cols: int = 6) -> Path | None:
    """Every rendered reel's cover (its opening with the hook) on one numbered
    sheet, to look over before scheduling. Numbers are the plan's order, from 1."""
    from PIL import Image, ImageDraw, ImageFont

    tiles = []
    for n, item in enumerate(plan["items"], 1):
        cover = (results.get(item["id"]) or {}).get("cover")
        if cover and Path(cover).is_file():
            with Image.open(cover) as im:
                tile = im.convert("RGB")
            tile.thumbnail((width, width * 2))
            tiles.append((n, tile))
    if not tiles:
        return None
    th = max(t.height for _, t in tiles)
    cols = min(cols, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (width + 6) + 6, rows * (th + 6) + 6), (24, 26, 32))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=26)
    for i, (n, tile) in enumerate(tiles):
        x, y = 6 + (i % cols) * (width + 6), 6 + (i // cols) * (th + 6)
        sheet.paste(tile, (x, y))
        draw.rectangle((x, y, x + 44, y + 36), fill=(0, 0, 0))
        draw.text((x + 8, y + 3), str(n), fill=(255, 255, 255), font=font)
    sheet.save(out, quality=85)
    return out


def autopilot_swap(plan_path: Path, ids: list[str], choices: dict[str, dict] | None = None,
                   near_hours: float = NEAR_HOURS) -> dict:
    """Give each named reel the next spare opening clip (the rejected one is
    banned) and drop its result so autopilot-render makes it again. A reel
    with no spare left is held back instead. ``choices`` ({reel id: a clip
    as footage.pick_of gives it}) sets the opening instead, which may be a
    clip of another reel unless that reel is within ``near_hours`` of this one."""
    from datetime import datetime

    plan = _read(plan_path)
    results_path = plan_path.parent / "results.json"
    results = _read(results_path) if results_path.exists() else {}
    picks_of = {item["id"]: {p["key"] for p in _load(Path(item["work"])).footage.picks} for item in plan["items"]}
    slot_of = {item["id"]: datetime.fromisoformat(item["slot"]) for item in plan["items"] if item.get("slot")}
    used = set().union(*picks_of.values()) if picks_of else set()
    choices = choices or {}
    swapped: dict[str, str | None] = {}
    for item in plan["items"]:
        if item["id"] not in ids and item["id"] not in choices:
            continue
        work = Path(item["work"])
        project = _load(work)
        if item["id"] in choices:
            spare = choices[item["id"]]
            here = slot_of.get(item["id"])
            near = [other for other, keys in picks_of.items() if other != item["id"] and spare["key"] in keys
                    and (here is None or other not in slot_of
                         or abs((slot_of[other] - here).total_seconds()) < near_hours * 3600)]
            if near:
                swapped[item["id"]] = None
                continue
        else:
            spare = next((c for c in item.get("openings") or [] if c["key"] not in used), None)
        if spare is None:
            entry = results.setdefault(item["id"], {"ok": False})
            entry["problems"] = [*(entry.get("problems") or []), "None of its opening clips worked on screen."]
            swapped[item["id"]] = None
            continue
        old = [p["key"] for p in project.footage.picks if p.get("slot") == "opening"]
        spare = {k: v for k, v in spare.items() if k not in ("tile", "portrait", "score", "slot")}
        project.footage.picks = [{**spare, "slot": "opening"},
                                 *(p for p in project.footage.picks if p.get("slot") != "opening" and p["key"] != spare["key"])]
        project.footage.banned = sorted((set(project.footage.banned) | set(old)) - {spare["key"]})
        config.save_project(project, work / "project.json")
        item["openings"] = [c for c in item.get("openings") or [] if c["key"] != spare["key"]]
        used.add(spare["key"])
        picks_of[item["id"]] = {p["key"] for p in project.footage.picks}
        results.pop(item["id"], None)
        swapped[item["id"]] = spare["key"]
    _write(plan_path, plan)
    _write(results_path, results)
    return {"ok": True, "swapped": swapped}


def autopilot_posts(plan_path: Path, results_path: Path, media_path: Path, now=None) -> dict:
    """createScheduledPost arguments for the reels that passed, leaving out
    slots too close to schedule (under 15 minutes away). A reel can have two
    posts (``part`` "main" and, when TikTok waits for its sound, "tiktok");
    record each answer under ``<slot_id>`` or ``<slot_id>/tiktok``."""
    from datetime import datetime, timedelta

    from . import autopilot as ap
    plan = _read(plan_path)
    posts, late = [], []
    if ap.by_hand(ap.settings_of(plan["settings"])):  # the user posts them: nothing goes to Metricool
        _write(plan_path.parent / "posts.json", posts)
        return {"ok": True, "posts": 0, "late": late, "by_hand": True, "file": str(plan_path.parent / "posts.json")}
    results, media = _read(results_path), _read(media_path)
    soon = (now or ap.now_in(plan["tz"])) + timedelta(minutes=15)
    for item in plan["items"]:
        res, m = results.get(item["id"]) or {}, media.get(item["id"]) or {}
        if not (res.get("ok") and not res.get("problems") and m.get("url")):
            continue
        if datetime.fromisoformat(item["slot"]) < soon:
            late.append(item["id"])
            continue
        posts += ap.metricool_posts(item, ap.settings_of(plan["settings"]), plan["brand_id"], m["url"], res.get("cover_ms"))
    _write(plan_path.parent / "posts.json", posts)
    return {"ok": True, "posts": len(posts), "late": late, "file": str(plan_path.parent / "posts.json")}


def autopilot_videos(plan_path: Path, results_path: Path) -> dict:
    """For an account posted by hand: the video of each reel that passed, as
    the panel keeps it (a copy under the asset store's limit when the render
    is bigger) -> videos-upload.json {reel id: file}. Upload them to the
    panel's assets and pass their ids to ``autopilot_docs`` (``videos``)."""
    plan, results = _read(plan_path), _read(results_path)
    files = {}
    for item in plan["items"]:
        res = results.get(item["id"]) or {}
        if not (res.get("ok") and not res.get("problems") and res.get("video")):
            continue
        video = Path(res["video"])
        files[item["id"]] = str(_shrink(video) if video.stat().st_size > ASSET_LIMIT else video)
    out = plan_path.parent / "videos-upload.json"
    _write(out, files)
    return {"ok": True, "videos": len(files), "file": str(out)}


def autopilot_docs(plan_path: Path, results_path: Path, media_path: Path | None, covers_path: Path,
                   metricool_path: Path | None, videos_path: Path | None = None) -> dict:
    """The panel's queue documents (``autopilot/<slot id>``) for the batch, as
    ArtifactData batch writes (one file per document). The reels stay out of
    the Reels list: their videos are on Higgsfield, their covers in the panel.
    For an account posted by hand there is no Metricool post nor Higgsfield
    upload: each reel that passed waits "ready" with its ``video`` (the panel
    asset id from ``videos``) and the ``text`` to post it with."""
    from datetime import datetime, timezone

    from . import autopilot as ap

    opt = lambda path: _read(path) if path and Path(path).exists() else {}  # noqa: E731
    plan, results, media = _read(plan_path), _read(results_path), opt(media_path)
    covers, mc, videos = _read(covers_path), opt(metricool_path), opt(videos_path)
    settings = ap.settings_of(plan["settings"])
    manual = ap.by_hand(settings)
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    review = plan["settings"]["approval"] == "review"
    folder = plan_path.parent / "docs"
    writes = []
    for item in plan["items"]:
        res = results.get(item["id"]) or {}
        m = media.get(item["id"]) or {}
        tiktok = mc.get(f"{item['id']}/tiktok") or {}
        post = mc.get(item["id"]) or tiktok
        if not res:  # no voice or no render by the end of the run
            status, res = "failed", {"problems": ["It wasn't made: its voiceover or render didn't finish."]}
        elif not res.get("ok"):
            status = "failed"
        elif res.get("problems"):
            status = "held"
        elif manual:
            status = "ready" if videos.get(item["id"]) else "failed"
            if status == "failed":
                res = {**res, "problems": ["Its video couldn't be saved in the panel."]}
        elif post.get("error") or not post.get("id"):
            status = "failed" if post.get("error") else "ready"
        else:
            status = "review" if review else "scheduled"
        ad = {"account": item.get("account") or plan.get("account") or "main", "kind": item.get("kind", "watch"),
              "slot": item["slot"], "tz": item["tz"], "status": status, "project_id": item["project_id"],
              "title": item["title"], "script": item["script"], "caption": item["caption"],
              "hashtags": item["hashtags"], "youtube_title": item["youtube_title"],
              "platforms": plan["settings"]["platforms"], "song": Path(item["song"]).name if item.get("song") else None,
              "master_url": m.get("url"),
              "master_media_id": m.get("media_id"), "cover": covers.get(item["id"]),
              "cover_ms": res.get("cover_ms"), "duration": res.get("duration"), "clips": res.get("clips") or [],
              "problems": res.get("problems") or ([post["error"]] if post.get("error") else []),
              "metricool": {k: post.get(k) for k in ("id", "uuid", "plannerUrl") if post.get(k)} or None,
              "metricool_tiktok": ({k: tiktok.get(k) for k in ("id", "uuid", "plannerUrl") if tiktok.get(k)} or None)
                                  if tiktok is not post else None,
              "first_comment": item.get("first_comment") or None, "search_phrase": item.get("search_phrase") or None,
              "trial": bool(item.get("trial")), "collaborator": item.get("collaborator"),
              "tiktok_sound": item.get("tiktok_sound"), "reply_to": item.get("reply_to"),
              "created": now, "updated": now}
        if manual:
            ad.update(posting="manual", video=videos.get(item["id"]), text=ap.reel_text(item, settings))
        _write(folder / f"autopilot-{item['id']}.json", ad)
        writes.append({"op": "set", "collection": "autopilot", "doc_id": item["id"],
                       "file_path": str(folder / f"autopilot-{item['id']}.json")})
    _write(folder / "writes.json", writes)
    counts: dict[str, int] = {}
    for w in writes:
        st = _read(w["file_path"])["status"]
        counts[st] = counts.get(st, 0) + 1
    return {"ok": True, "writes": len(writes), "file": str(folder / "writes.json"), "statuses": counts}


def _reel_docs(folder: Path) -> list[dict]:
    """The autopilot documents ArtifactData saved in ``folder`` (``<id>.json`` each), with their ids."""
    out = []
    for f in sorted(Path(folder).glob("*.json")):
        doc = _read(f)
        body = doc["data"] if isinstance(doc.get("data"), dict) and "slot" in doc["data"] else doc
        out.append({**body, "id": body.get("id") or f.stem})
    return out


def autopilot_insights(inp: Path, out: Path, now=None) -> dict:
    """How one account's reels did (insights.py; ``account`` in IN, default
    every reel given) -> OUT/insights.json, the account's insights document
    (``autopilot.insights_doc``), and OUT/reposts.json, the
    createScheduledPost arguments for the Trial Reels to post again as
    normal reels."""
    from datetime import datetime, timezone

    from . import insights

    from . import autopilot as ap

    data = _read(inp)
    brand = data.get("brand") or {}
    reels = data.get("reels") or (_reel_docs(Path(data["reels_dir"])) if data.get("reels_dir") else [])
    if data.get("account"):  # one account's reels: the brand's numbers are only theirs
        reels = [r for r in reels if ap.account_of(r) == ap.account_key(data["account"])]
    now = now or (datetime.fromisoformat(data["now"].replace("Z", "")) if data.get("now")
                  else datetime.now(timezone.utc).replace(tzinfo=None))
    followers = {net: insights.followers_of(ans) for net, ans in (data.get("followers") or {}).items()}
    doc, again = insights.build(data.get("stats") or {}, reels, tz=brand.get("timezone") or "UTC", now=now,
                                followers=followers, live=data.get("scheduled") or [],
                                prior=data.get("previous"), settings=data.get("settings"),
                                brand_id=str(brand.get("id") or ""))
    _write(out / "insights.json", doc)
    _write(out / "reposts.json", again)
    return {"ok": True, "file": str(out / "insights.json"), "reposts": str(out / "reposts.json"),
            "judged": doc["judged"], "trial_eligible": doc["trial_eligible"], "followers": doc["followers"],
            "best": [e["hook"] for e in doc["top"]], "learnings": doc["learnings"], "posts_again": len(again)}


def insights_codes() -> dict:
    """The getAnalyticsDataByMetrics requests autopilot-insights reads."""
    from . import insights
    return {"posts": {net: insights.codes(net) for net in insights.METRICS},
            "followers": {net: [code] for net, code in insights.FOLLOWERS.items()}}


class _FileFlag(threading.Event):
    """Set once a ``cancel`` file appears in the work folder."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def is_set(self) -> bool:  # type: ignore[override]
        return self.path.exists() or super().is_set()


def render(work: Path, voiceover: str | None) -> dict:
    from .pipeline import render_project
    state = {"status": "running", "stage": "voice", "progress": 0.0, "message": "Starting…", "log": [],
             "started": time.time()}
    progress_file = work / "progress.json"

    def write() -> None:
        state["log"] = state["log"][-LOG_LINES:]
        tmp = progress_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(progress_file)

    class LogToState(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            state["log"].append(self.format(record))
            write()

    handler = LogToState()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("reelforge")
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def progress(stage_name: str, frac: float, message: str) -> None:
        state.update(stage=stage_name, progress=round(frac, 3), message=message or state["message"])
        write()

    (work / "cancel").unlink(missing_ok=True)
    write()
    try:
        result = render_project(_load(work, voiceover), work, _settings(work), progress=progress,
                                cancel=_FileFlag(work / "cancel"))
        state.update(status="done", stage="finish", progress=1.0, message="Finished")
        out = {"ok": True, "result": result}
    except Exception as exc:  # reported to the panel
        cancelled = (work / "cancel").exists()
        state.update(status="cancelled" if cancelled else "error",
                     message="Render cancelled" if cancelled else (str(exc).splitlines() or ["error"])[0])
        state["log"].append(traceback.format_exc(limit=3))
        out = {"ok": False, "cancelled": cancelled, "error": str(exc) or type(exc).__name__}
    finally:
        root.removeHandler(handler)
    write()
    (work / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def status(work: Path) -> dict:
    try:
        st = json.loads((work / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "running", "stage": "voice", "progress": 0, "message": "Starting…", "log": []}
    st.pop("started", None)
    return st


def _shrink(video: Path) -> Path:
    """A copy of ``video`` under the asset limit (same size and frame rate)."""
    duration = media.duration(video)
    kbps = max(400, int(ASSET_LIMIT * 8 / 1000 / max(duration, 1) * 0.9) - 160)
    small = video.with_name(video.stem + "-panel.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-c:v", "libx264", "-preset", "medium",
                    "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(small)], check=True)
    return small


def outputs(work: Path) -> dict:
    res = json.loads((work / "result.json").read_text(encoding="utf-8"))
    if not res.get("ok"):
        return {"error": res.get("error")}
    r = res["result"]
    files: dict[str, str] = {}
    video = work / r["video"]
    if video.stat().st_size > ASSET_LIMIT:
        video = _shrink(video)
    files["video"] = str(video)
    if r.get("cover"):
        files["cover"] = str(work / r["cover"])
    for key, name in (("srt", "captions.srt.txt"), ("credits", "credits.txt"), ("timings", "timings.json")):
        if r.get(key) and (work / r[key]).is_file():
            src = work / r[key]
            dst = work / "panel-upload" / name  # .srt is not an accepted asset type: send it as text
            dst.parent.mkdir(exist_ok=True)
            dst.write_bytes(src.read_bytes())
            files[key] = str(dst)
    summary = {k: r.get(k) for k in ("duration", "warnings", "tempo", "method") if k in r}
    return {"files": files, "result": summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m reelforge.panelrun", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("doc")
    s.add_argument("--assets", required=True)
    s.add_argument("--work", required=True)
    for name in ("plan", "review", "status", "outputs"):
        sub.add_parser(name).add_argument("--work", required=True)
    sub.add_parser("ideas").add_argument("task")
    a = sub.add_parser("autopilot-prepare")
    a.add_argument("input")
    a.add_argument("--assets", required=True)
    a.add_argument("--out", required=True)
    a = sub.add_parser("autopilot-render")
    a.add_argument("--plan", required=True)
    a.add_argument("--voices", required=True)
    a.add_argument("--jobs", type=int, default=2)
    sub.add_parser("autopilot-footage").add_argument("--plan", required=True)
    a = sub.add_parser("autopilot-swap")
    a.add_argument("--plan", required=True)
    a.add_argument("--ids", default="")
    a.add_argument("--choices", help="JSON file {reel id: clip} with the opening to use")
    a.add_argument("--near-hours", type=float, default=NEAR_HOURS,
                   help="a chosen clip may be shared with reels further apart than this")
    a = sub.add_parser("autopilot-posts")
    for flag in ("--plan", "--results", "--media"):
        a.add_argument(flag, required=True)
    a = sub.add_parser("autopilot-videos")
    for flag in ("--plan", "--results"):
        a.add_argument(flag, required=True)
    a = sub.add_parser("autopilot-docs")
    for flag in ("--plan", "--results", "--covers"):
        a.add_argument(flag, required=True)
    a.add_argument("--media", help="the masters on Higgsfield (not for an account posted by hand)")
    a.add_argument("--metricool", help="Metricool's answers per slot (not for an account posted by hand)")
    a.add_argument("--videos", help="for an account posted by hand: {reel id: panel asset id of its video}")
    a = sub.add_parser("autopilot-insights")
    a.add_argument("input", nargs="?")
    a.add_argument("--out")
    a.add_argument("--codes", action="store_true", help="print the Metricool metric ids to request")
    r = sub.add_parser("render")
    r.add_argument("--work", required=True)
    r.add_argument("--voiceover")
    args = ap.parse_args(argv)
    work = Path(getattr(args, "work", None) or ".").resolve()
    if args.cmd == "stage":
        out = stage(Path(args.doc), Path(args.assets), work)
    elif args.cmd == "plan":
        out = plan(work)
    elif args.cmd == "review":
        out = review(work)
    elif args.cmd == "ideas":
        out = ideas(Path(args.task))
    elif args.cmd == "autopilot-prepare":
        out = autopilot_prepare(Path(args.input), Path(args.assets), Path(args.out).resolve())
    elif args.cmd == "autopilot-render":
        out = autopilot_render(Path(args.plan).resolve(), Path(args.voices), args.jobs)
    elif args.cmd == "autopilot-footage":
        out = autopilot_footage(Path(args.plan).resolve())
    elif args.cmd == "autopilot-swap":
        out = autopilot_swap(Path(args.plan).resolve(), [i for i in args.ids.split(",") if i],
                             _read(args.choices) if args.choices else None, args.near_hours)
    elif args.cmd == "autopilot-posts":
        out = autopilot_posts(Path(args.plan), Path(args.results), Path(args.media))
    elif args.cmd == "autopilot-videos":
        out = autopilot_videos(Path(args.plan), Path(args.results))
    elif args.cmd == "autopilot-docs":
        out = autopilot_docs(Path(args.plan), Path(args.results), args.media and Path(args.media), Path(args.covers),
                             args.metricool and Path(args.metricool), args.videos and Path(args.videos))
    elif args.cmd == "autopilot-insights":
        if args.codes or not args.input:
            out = insights_codes()
        else:
            out = autopilot_insights(Path(args.input), Path(args.out or Path(args.input).parent).resolve())
    elif args.cmd == "render":
        out = render(work, args.voiceover)
    elif args.cmd == "status":
        out = status(work)
    else:
        out = outputs(work)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not (isinstance(out, dict) and out.get("ok") is False) else 1


if __name__ == "__main__":
    sys.exit(main())
