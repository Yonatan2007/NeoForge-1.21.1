import json
from datetime import date, datetime

import pytest

from reelforge import autopilot as ap, config, panelrun
from reelforge.config import Project


def grid(peaks, base=10.0):
    """A Metricool best-time answer: every day, ``peaks`` {hour: value}, other hours ``base``."""
    return {"data": [{"dayOfWeek": d, "bestTimesByHour": [{"hourOfDay": h, "value": peaks.get(h, base)}
                                                          for h in range(24)]} for d in range(1, 8)]}


def test_settings_are_kept_in_range():
    s = ap.settings_of({"per_day": 40, "days": 0, "min_gap": 0.2, "platforms": ["instagram", "x", "tiktok"],
                        "approval": "maybe", "window": ["23:00", "07:00"], "format": "square", "unknown": 1})
    assert (s["per_day"], s["days"], s["min_gap"]) == (6, 7, 1.0)  # 0 days falls back to the default week
    assert s["platforms"] == ["instagram", "tiktok"] and s["approval"] == "auto" and s["format"] == "template"
    assert s["window"] == ["08:00", "23:30"] and "unknown" not in s
    assert ap.settings_of(None)["per_day"] == 3


def test_best_hours_from_two_platforms_are_combined_fairly():
    ig = ap.grid_of(grid({19: 1000, 12: 500}, base=100))
    yt = ap.grid_of(grid({9: 10}, base=1))  # a tiny channel still counts
    both = ap.combine([ig, yt])
    assert both[1][19] == pytest.approx((1.0 + 0.1) / 2) and both[1][9] == pytest.approx((0.1 + 1.0) / 2)
    assert ap.combine([{}]) == {}
    short = ap.grid_of({"7": [5.0] * 18 + [9.0] + [5.0] * 5, "1": [1] * 24})  # day: 24 values from midnight
    assert short[7][18] == 9.0 and short[7][17] == 5.0 and short[1][0] == 1.0
    assert short == ap.grid_of({"data": [{"dayOfWeek": d, "bestTimesByHour": [{"hourOfDay": h, "value": v}
                                         for h, v in hrs.items()]} for d, hrs in short.items()]})


def test_slots_take_the_busiest_hours_apart_from_each_other():
    g = ap.grid_of(grid({19: 100, 20: 99, 21: 98, 12: 90, 9: 80}))
    slots = ap.pick_slots(g, date(2026, 10, 12), days=2, per_day=3, min_gap=3)
    # 19 is busiest; 20 and 21 are too close to it, so 12 and then 9 (exactly 3 h from 12)
    assert [(s.day, s.hour) for s in slots] == [(12, 9), (12, 12), (12, 19), (13, 9), (13, 12), (13, 19)]
    # taken slots count toward the day and keep their distance; the window and "not before" hold
    taken = [datetime(2026, 10, 12, 19), datetime(2026, 10, 12, 12)]
    slots = ap.pick_slots(g, date(2026, 10, 12), days=1, per_day=3, min_gap=3, taken=taken)
    assert [s.hour for s in slots] == [9]
    slots = ap.pick_slots(g, date(2026, 10, 12), days=1, per_day=2, window=("10:00", "18:00"),
                          earliest=datetime(2026, 10, 12, 13))
    assert all(13 <= s.hour <= 18 for s in slots) and len(slots) == 2


def test_slots_squeeze_closer_when_a_day_is_short_and_fall_back_without_data():
    slots = ap.pick_slots({}, date(2026, 10, 12), days=1, per_day=4, window=("18:00", "22:00"), min_gap=3)
    assert len(slots) == 4 and len(set(slots)) == 4
    assert [s.hour for s in ap.pick_slots({}, date(2026, 10, 12), days=1, per_day=1)] == [19]


def test_taken_slots_are_read_in_the_brands_time_zone():
    posts = [{"publicationDate": {"dateTime": "2026-10-12T17:30:00", "timezone": "Europe/London"}},
             {"publicationDate": {"dateTime": "2026-10-12T19:00:00", "timezone": "Asia/Jerusalem"}}, {}]
    assert ap.taken_slots(posts, "Asia/Jerusalem") == [datetime(2026, 10, 12, 19, 30), datetime(2026, 10, 12, 19)]
    assert ap.with_offset(datetime(2026, 10, 12, 19), "Asia/Jerusalem") == "2026-10-12T19:00:00+03:00"


def template_project():
    p = config.to_dict(config.from_dict(Project, config.preset_dict("reference")))
    p["script"] = "Old script. Old words."
    p["footage"]["items"] = [{"path": "uploads/ref.jpg", "role": "reference"}, {"path": "uploads/mine.mp4", "role": "footage"}]
    p["footage"]["picks"] = [{"key": "mixkit_1"}]
    p["footage"]["banned"] = ["mixkit_9"]
    p["footage"]["allow_landscape"] = True
    p["style"]["video"]["draft"] = True
    p["voice"].update(source="file", file="uploads/voice.mp3")
    p["music"]["file"] = "uploads/song.mp3"
    return p


def test_a_new_reel_keeps_the_templates_look_with_a_fresh_script():
    t = template_project()
    p = ap.build_project(t, "New hook? New words.", "New", banned={"pexels_2"}, fmt="4:5")
    assert (p["name"], p["script"]) == ("New", "New hook? New words.")
    assert p["footage"]["items"] == [{"path": "uploads/ref.jpg", "role": "reference"}]
    assert p["footage"]["picks"] == [] and p["footage"]["banned"] == ["mixkit_9", "pexels_2"]
    assert p["footage"]["allow_landscape"] is False and p["style"]["video"]["draft"] is False
    assert (p["style"]["video"]["aspect"], p["style"]["video"]["letterbox"]) == ("4:5", True)
    assert (p["voice"]["source"], p["voice"]["file"]) == ("higgsfield", None)
    assert p["music"]["file"] == "uploads/song.mp3" and t["script"] == "Old script. Old words."  # template untouched
    assert ap.build_project(t, "a", "b", music=None)["music"]["file"] is None
    rotated = ap.build_project(t, "a", "b", music="uploads/other.mp3")["music"]
    assert (rotated["file"], rotated["source_in"]) == ("uploads/other.mp3", 0)
    s = ap.settings_of({"music": "rotate", "songs": ["a.mp3", "b.mp3"]})
    assert [ap.pick_song(s, i, "uploads/song.mp3") for i in range(3)] == ["uploads/a.mp3", "uploads/b.mp3", "uploads/a.mp3"]
    assert ap.pick_song(ap.settings_of({"music": "none"}), 0, "x") is None


def clip(key, w=1080, h=1920):
    return {"key": key, "provider": key.split("_")[0], "id": key.split("_")[1], "download_url": "u",
            "width": w, "height": h, "tile": 0, "portrait": h > w}


def test_clips_are_shared_out_so_no_two_reels_repeat_one():
    reviews = [{"opening": [clip("m_1"), clip("m_2")], "pool": [clip(f"p_{i}") for i in range(6)], "rest_shots": 2},
               {"opening": [clip("m_1"), clip("m_2")], "pool": [clip(f"p_{i}") for i in range(8)], "rest_shots": 2},
               {}]
    a, b, c = ap.assign_clips(reviews, banned={"p_0"})
    assert a[0] == {k: v for k, v in clip("m_1").items() if k not in ("tile", "portrait")} | {"slot": "opening"}
    assert [x["key"] for x in a] == ["m_1", "p_1", "p_2", "p_3", "p_4"]
    assert [x["key"] for x in b] == ["m_2", "p_5", "p_6", "p_7"] and c == []
    reviews[0]["opening"].append(clip("m_3"))
    spares = ap.spare_openings(reviews, [a, b, c])
    assert [[x["key"] for x in sp] for sp in spares] == [["m_3"], [], []]  # m_1 and m_2 are taken


def test_post_text_fits_instagram():
    assert ap.post_text("Line one", ["a", "b"]) == "Line one\n\n#a #b"
    long = ap.post_text("x" * 2190, ["abcdef", "gh"])
    assert long.endswith("\n\n#abcdef") and len(long) == 2199  # the tag that doesn't fit is dropped


def test_quality_gate():
    ok = {"ok": True, "result": {"duration": 44, "warnings": []}}
    assert ap.quality_problems(ok, [clip("a_1")]) == []
    assert ap.quality_problems({"ok": False, "error": "boom"}, []) == ["The render failed: boom"]
    bad = {"ok": True, "result": {"duration": 8, "warnings": ["Found 3 stock clips for 9 shots; some clips repeat."]}}
    problems = ap.quality_problems(bad, [clip("a_1", 1920, 1080)])
    assert len(problems) == 3


def test_metricool_post_reaches_every_platform_and_drafts_in_review_mode():
    item = {"id": "ap-20261012-1900", "slot": "2026-10-12T19:00", "tz": "Asia/Jerusalem", "title": "T",
            "caption": "Hook line", "hashtags": ["a"], "youtube_title": "YT title"}
    s = ap.settings_of({"platforms": ["instagram", "youtube", "tiktok"], "approval": "review"})
    post = ap.metricool_post(item, s, "7327885", "https://cdn/x.mp4", 3240)
    info = json.loads(post["info"])
    assert post["date"] == "2026-10-12T19:00:00+03:00" and post["blogId"] == "7327885"
    assert info["draft"] is True and info["media"] == ["https://cdn/x.mp4"] and info["videoCoverMilliseconds"] == 3240
    assert [p["network"] for p in info["providers"]] == ["instagram", "youtube", "tiktok"]
    assert info["youtubeData"]["title"] == "YT title" and info["instagramData"]["type"] == "REEL"
    assert info["text"] == "Hook line\n\n#a" and info["publicationDate"]["dateTime"] == "2026-10-12T19:00:00"


# --------------------------------------------------------------------------- the panelrun commands

def test_prepare_render_posts_and_docs(tmp_path, monkeypatch):
    from reelforge import pipeline

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "abc123.mp3").write_bytes(b"ID3 song")
    template = {"id": "tpl-1", "data": {"project": template_project(),
                                       "files": [{"name": "song.mp3", "store": {"type": "raw", "ids": ["abc123"]}}]}}
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({
        "settings": {"per_day": 2, "days": 2, "platforms": ["instagram", "youtube"]},
        "brand": {"id": "7327885", "timezone": "Asia/Jerusalem"},
        "template": template, "best": {"instagram": grid({19: 100, 12: 90})},
        "scheduled": [{"publicationDate": {"dateTime": "2026-10-12T19:00:00", "timezone": "Asia/Jerusalem"}}],
        "history": [{"title": "Old one", "clips": ["p_9"]}], "now": "2026-10-12T08:00"}))
    calls = []

    def writer(**kw):
        calls.append(kw)
        n = kw["count"]
        return {"model": "gemini-test", "ideas": [{"title": f"Idea {len(calls)}-{i}", "script": "Hook here? Then more words.",
                                                  "why": "", "caption": "Cap", "hashtags": ["x"], "youtube_title": "YT"}
                                                 for i in range(n)]}

    pools = iter(range(100))
    monkeypatch.setattr(pipeline, "review_footage", lambda project, work, settings, out: {
        "opening": [clip(f"m_{next(pools)}")], "pool": [clip("p_9"), *(clip(f"p_{next(pools)}") for _ in range(4))],
        "rest_shots": 2})
    out = panelrun.autopilot_prepare(inp, assets, tmp_path / "run", writer=writer)
    assert out["ok"] and out["slots"] == 3  # 2 days x 2, minus the one already taken on day one
    plan = json.loads((tmp_path / "run" / "plan.json").read_text())
    assert calls[0]["post"] is True and "Old one" in calls[0]["avoid"]
    items = plan["items"]
    assert [i["slot"] for i in items] == ["2026-10-12T12:00", "2026-10-13T12:00", "2026-10-13T19:00"]
    keys = []
    for it in items:
        work = tmp_path / "run" / it["id"]
        assert (work / "uploads" / "song.mp3").read_bytes() == b"ID3 song" and (work / "uploads" / "song.mp3").is_symlink()
        p = json.loads((work / "project.json").read_text())
        mine = [x["key"] for x in p["footage"]["picks"]]
        assert "p_9" not in mine and p["footage"]["picks"][0]["slot"] == "opening"
        keys += mine
        assert it["voice_request"]["params"]["prompt"].startswith("Hook here?") and it["openings"] == []
    assert len(keys) == len(set(keys))  # no clip in two reels

    # renders (faked) -> posts -> documents
    results = {items[0]["id"]: {"ok": True, "problems": [], "cover_ms": 2000, "duration": 40, "clips": ["m_0"],
                                "video": "v.mp4"},
               items[1]["id"]: {"ok": True, "problems": ["Some clips repeat."], "duration": 40},
               items[2]["id"]: {"ok": False, "problems": ["The render failed: x"]}}
    (tmp_path / "run" / "results.json").write_text(json.dumps(results))
    media = {items[0]["id"]: {"url": "https://cdn/0.mp4", "media_id": "m0"}}
    (tmp_path / "media.json").write_text(json.dumps(media))
    run = (tmp_path / "run" / "plan.json", tmp_path / "run" / "results.json", tmp_path / "media.json")
    late = panelrun.autopilot_posts(*run, now=datetime(2026, 10, 12, 11, 50))
    assert late["posts"] == 0 and late["late"] == [items[0]["id"]]  # 12:00 is too close to schedule
    posts = panelrun.autopilot_posts(*run, now=datetime(2026, 10, 12, 8, 0))
    assert posts["posts"] == 1 and posts["late"] == []
    (tmp_path / "covers.json").write_text(json.dumps({items[0]["id"]: "cov0"}))
    (tmp_path / "mc.json").write_text(json.dumps({items[0]["id"]: {"id": 1, "uuid": "u1", "plannerUrl": "https://m"}}))
    docs = panelrun.autopilot_docs(tmp_path / "run" / "plan.json", tmp_path / "run" / "results.json",
                                   tmp_path / "media.json", tmp_path / "covers.json", tmp_path / "mc.json")
    assert docs["statuses"] == {"scheduled": 1, "held": 1, "failed": 1}
    writes = json.loads(open(docs["file"]).read())
    assert [w["collection"] for w in writes] == ["autopilot"] * 3  # the queue only, not the Reels list
    doc = json.loads(open(writes[0]["file_path"]).read())
    assert (doc["status"], doc["cover"], doc["master_url"], doc["master_media_id"]) == ("scheduled", "cov0", "https://cdn/0.mp4", "m0")
    assert doc["metricool"] == {"id": 1, "uuid": "u1", "plannerUrl": "https://m"} and doc["clips"] == ["m_0"]
    assert writes[0]["doc_id"] == items[0]["id"] and doc["slot"] == items[0]["slot"]


def test_render_runs_each_reel_once_and_checks_it(tmp_path, monkeypatch):
    items = []
    for n in range(3):
        work = tmp_path / f"ap-{n}"
        work.mkdir()
        items.append({"id": f"ap-{n}", "work": str(work)})
    (tmp_path / "plan.json").write_text(json.dumps({"items": items}))
    (tmp_path / "voices.json").write_text(json.dumps({"ap-0": "https://v/0.mp3", "ap-1": "https://v/1.mp3"}))
    seen = []

    def fake_run(cmd, env=None, **kw):
        work = tmp_path / cmd[cmd.index("--work") + 1].split("/")[-1]
        seen.append((work.name, env["REELFORGE_CACHE"].rsplit("-", 1)[-1], cmd[-1]))
        (work / "output").mkdir()
        wide = work.name == "ap-1"
        (work / "result.json").write_text(json.dumps({"ok": True, "result": {
            "video": "output/v.mp4", "cover": "output/cover.jpg", "duration": 41.5, "warnings": [], "cover_time": 1.25}}))
        (work / "output" / "credits.json").write_text(json.dumps(
            [{"provider": "mixkit", "id": "7", "width": 1920 if wide else 1080, "height": 1080 if wide else 1920}]))

    monkeypatch.setattr(panelrun.subprocess, "run", fake_run)
    out = panelrun.autopilot_render(tmp_path / "plan.json", tmp_path / "voices.json", jobs=2)
    assert out["rendered"] == 2 and out["passed"] == 1 and list(out["held"]) == ["ap-1"]  # no voice -> not rendered
    res = json.loads((tmp_path / "results.json").read_text())
    assert res["ap-0"]["cover_ms"] == 1250 and res["ap-0"]["clips"] == ["mixkit_7"] and res["ap-0"]["video"].endswith("v.mp4")
    assert sorted(v for _, _, v in seen) == ["https://v/0.mp3", "https://v/1.mp3"]
    again = panelrun.autopilot_render(tmp_path / "plan.json", tmp_path / "voices.json", jobs=2)
    assert again["rendered"] == 0  # finished reels aren't rendered twice


def test_a_bad_opening_is_swapped_and_the_reel_made_again(tmp_path):
    from PIL import Image

    items = []
    for n in range(3):
        work = tmp_path / f"ap-{n}"
        work.mkdir()
        p = config.from_dict(Project, template_project())
        p.footage.picks = [{**clip(f"m_{n}"), "slot": "opening"}, clip(f"p_{n}")]
        config.save_project(p, work / "project.json")
        items.append({"id": f"ap-{n}", "work": str(work), "openings": [clip("m_1"), clip(f"m_{n}9")] if n != 2 else []})
        Image.new("RGB", (108, 192), (n * 80, 90, 120)).save(work / "cover.jpg")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": items}))
    results = {f"ap-{n}": {"ok": True, "problems": [], "cover": str(tmp_path / f"ap-{n}" / "cover.jpg")} for n in range(3)}
    (tmp_path / "results.json").write_text(json.dumps(results))
    sheet = panelrun._covers_sheet(json.loads(plan.read_text()), results, tmp_path / "covers.jpg", width=60, cols=2)
    assert Image.open(sheet).size == (2 * 66 + 6, 2 * (107 + 6) + 6)  # 3 covers, 2 a row, numbered
    out = panelrun.autopilot_swap(plan, ["ap-0", "ap-2"])
    assert out["swapped"] == {"ap-0": "m_09", "ap-2": None}  # m_1 is ap-1's opening; ap-2 has no spare
    p0 = config.load_project(tmp_path / "ap-0" / "project.json")
    assert [x["key"] for x in p0.footage.picks] == ["m_09", "p_0"] and p0.footage.picks[0]["slot"] == "opening"
    assert "m_0" in p0.footage.banned
    res = json.loads((tmp_path / "results.json").read_text())
    assert "ap-0" not in res and res["ap-1"]["ok"]  # ap-0 is made again by the next autopilot-render
    assert res["ap-2"]["problems"] == ["None of its opening clips worked on screen."]  # held back
    assert [c["key"] for c in json.loads(plan.read_text())["items"][0]["openings"]] == ["m_1"]
