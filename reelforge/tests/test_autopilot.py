import base64
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
    # four don't fit 18:00-22:00 even 2 h apart: three do, never closer than 2 h
    slots = ap.pick_slots({}, date(2026, 10, 12), days=1, per_day=4, window=("18:00", "22:00"), min_gap=3)
    assert [s.hour for s in slots] == [18, 20, 22]
    assert [s.hour for s in ap.pick_slots({}, date(2026, 10, 12), days=1, per_day=1)] == [19]


def test_a_late_start_keeps_reels_apart_instead_of_an_hour_apart():
    # tonight from 19:00: the greedy pick was 19, 20, 22; the best set 2 h apart is 19, 21, 23
    g = ap.grid_of(grid({19: 341, 20: 250, 21: 186, 22: 158, 23: 105}))
    slots = ap.pick_slots(g, date(2026, 10, 11), days=1, per_day=3, earliest=datetime(2026, 10, 11, 18, 24))
    assert [s.hour for s in slots] == [19, 21, 23]


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
    lib = ap.build_project(t, "a", "b", music={"name": "x.mp3", "file": "uploads/x.mp3", "start": 12.5})["music"]
    assert (lib["file"], lib["source_in"], lib["source_out"]) == ("uploads/x.mp3", 12.5, None)


def song(name, **kw):
    return {"name": name, "store": {"type": "b64", "ids": [f"id-{name}"]}, **kw}


def test_library_songs_are_dealt_at_random_like_cards():
    import random
    library = [song("a.mp3"), song("b.mp3", start=7), {"id": "x", "data": song("c.mp3")}, song("off.mp3", enabled=False)]
    s = ap.settings_of({"music": "random"})
    picks = ap.pick_songs(s, 7, "uploads/t.mp3", library, recent=["a.mp3", "b.mp3"], rng=random.Random(3))
    names = [p["name"] for p in picks]
    assert sorted(names[:3]) == ["a.mp3", "b.mp3", "c.mp3"] and sorted(names[3:6]) == ["a.mp3", "b.mp3", "c.mp3"]
    assert names[0] == "c.mp3" and names[1:3] == ["a.mp3", "b.mp3"]  # never heard first, the latest last
    assert all(x != y for x, y in zip(names, names[1:])) and "off.mp3" not in names
    assert picks[names.index("b.mp3")] == {"name": "b.mp3", "file": "uploads/b.mp3", "start": 7.0}
    assert ap.pick_songs(s, 2, "uploads/t.mp3", [], rng=random.Random(1)) == [True, True]  # empty library: its song
    assert ap.pick_songs(s, 1, None, []) == [None]
    assert ap.pick_songs(ap.settings_of({"music": "none"}), 2, "x", library) == [None, None]
    assert ap.pick_songs(ap.settings_of({"music": "template"}), 1, "x", library) == [True]
    assert ap.settings_of({"music": "rotate"})["music"] == "random" and ap.settings_of(None)["music"] == "random"


def test_each_account_and_video_type_has_its_own_songs():
    import random
    library = [song("old.mp3"),  # from before the libraries: the main account's, both halves
               song("phonk.mp3", use=["gym/watch"]), song("drums.mp3", use=["gym/watch", "gym/comment"]),
               song("loop.mp3", use=["gym/comment"]), song("stars.mp3", use=["space/watch"]),
               song("off.mp3", use=["gym/comment"], enabled=False)]
    assert [x["name"] for x in ap.songs_for(library, "gym", "watch")[0]] == ["phonk.mp3", "drums.mp3"]
    loops, place = ap.songs_for(library, "gym", "comment")
    assert place == "gym/comment" and {x["name"] for x in loops} == {"drums.mp3", "loop.mp3"}
    assert ap.songs_for(library, "space", "comment") == ([library[4]], "space/watch")  # its other half
    assert ap.songs_for(library, "dilemmas", "watch") == ([library[0]], "main/watch")  # nothing yet: the main one's
    assert ap.songs_for(library, None, "comment") == ([library[0]], "main/comment")
    assert ap.songs_for([song("x.mp3", use=["gym/watch"])], "main", "watch") == ([], None)
    s = ap.settings_of({"music": "random"})
    picks = ap.pick_songs(s, ["comment", "watch", "comment", "watch"], "uploads/t.mp3", library,
                          rng=random.Random(2), account="gym")
    assert {x["name"] for x in picks[0::2]} == {"drums.mp3", "loop.mp3"}  # dealt like cards within a kind
    assert {x["name"] for x in picks[1::2]} == {"phonk.mp3", "drums.mp3"}
    assert ap.pick_songs(s, ["watch"], "uploads/t.mp3", [song("x.mp3", use=["gym/watch"])], account="main") == [True]


def clip(key, w=1080, h=1920):
    return {"key": key, "provider": key.split("_")[0], "id": key.split("_")[1], "download_url": "u",
            "width": w, "height": h, "tile": 0, "portrait": h > w}


GYM_LINES = ("Discipline is just...", "The habit that quietly ruined my twenties...", "You know a man is serious when...",
             "Real strength shows up when...", "The lift that humbled me the most...", "Every man should learn early that...")
DILEMMA_LINES = ("Would you rather know when, or how?", "Would you erase your worst memory if your best went with it?",
                 "Would you relive your best day or skip your worst one?", "If you could undo one choice, would you?")


def lines_of(pool, n, **extra):
    """``n`` comment lines from ``pool`` as Gemini's line writer returns them, each a different sentence."""
    return [{"kind": "comment", "title": f"L{i}", "script": pool[i], "why": "", "caption": "Be honest.", "hashtags": [],
             "youtube_title": "Y", **extra} for i in range(n)]


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


def test_hashtags_stay_off_unless_turned_on():
    assert ap.without_hashtags("Let it go. #healing\n\nSave this.\n\n#a #b_c #d") == "Let it go.\n\nSave this."
    assert ap.without_hashtags("No tags & no #") == "No tags & no #"
    item = {"id": "ap-20261012-1900", "slot": "2026-10-12T19:00", "tz": "Asia/Jerusalem", "title": "T",
            "caption": "Hook line #sneaky\n\nMore.", "hashtags": ["a", "b"], "youtube_title": "YT"}
    off = json.loads(ap.metricool_post(item, ap.settings_of({}), "1", "u", None)["info"])
    assert off["text"] == "Hook line\n\nMore."
    on = json.loads(ap.metricool_post(item, ap.settings_of({"hashtags": True}), "1", "u", None)["info"])
    assert on["text"] == "Hook line #sneaky\n\nMore.\n\n#a #b"


def test_quality_gate():
    ok = {"ok": True, "result": {"duration": 44, "warnings": []}}
    assert ap.quality_problems(ok, [clip("a_1")]) == []
    assert ap.quality_problems({"ok": False, "error": "boom"}, []) == ["The render failed: boom"]
    bad = {"ok": True, "result": {"duration": 8, "warnings": ["Found 3 stock clips for 9 shots; some clips repeat."]}}
    problems = ap.quality_problems(bad, [clip("a_1", 1920, 1080)])
    assert len(problems) == 3


def test_growth_settings_are_kept_in_range():
    s = ap.settings_of({"length": "epic", "first_comment": 0, "search": 1, "trial_reels": "sometimes",
                        "collaborators": "@Night.Thoughts, other_page;@x y z", "collab_every": "0", "tiktok_sound": "yes"})
    assert (s["length"], s["first_comment"], s["search"], s["trial_reels"]) == ("short", False, True, "some")
    assert s["collaborators"] == ["night.thoughts", "other_page", "x"]  # three at most, without "@"
    assert s["collab_every"] == 1 and s["tiktok_sound"] == "off"
    d = ap.settings_of(None)
    assert (d["length"], d["first_comment"], d["trial_reels"], d["collaborators"], d["tiktok_sound"], d["hashtags"]) == \
        ("short", True, "some", [], "off", False)
    assert ap.settings_of({"hashtags": 1})["hashtags"] is True
    assert ap.settings_of({"tiktok_sound": "remind", "collab_every": "x"})["collab_every"] == 7


def test_first_comment_fits_tiktok():
    assert ap.first_comment(' "What are you still waiting to feel ready for?" ') == "What are you still waiting to feel ready for?"
    long = ap.first_comment("word " * 60)
    assert len(long) <= 150 and long.endswith("…") and "  " not in long


def items_for(n):
    return [{"id": f"ap-{i}", "slot": f"2026-10-15T{10 + i:02d}:00", "tz": "Asia/Jerusalem", "title": f"T{i}",
             "caption": "Cap", "hashtags": [], "youtube_title": "YT"} for i in range(n)]


def test_extras_share_out_trials_collaborators_and_sounds():
    s = ap.settings_of({"platforms": ["instagram", "youtube", "tiktok"], "collaborators": ["a", "b"],
                        "collab_every": 3, "tiktok_sound": "remind"})
    items = items_for(7)
    # "a" was invited last time, two reels ago: "b" is next and the third reel is due
    history = [{"title": "x"}, {"title": "y", "collaborator": "a"}, {"title": "z"}]
    sounds = [{"name": "Lo-fi one", "artist": "A", "song_clip_id": "1", "genre": "LO-FI", "rank": 1}, {"name": "Two", "song_clip_id": "2"}]
    ap.plan_extras(items, s, trial_ok=True, history=history, sounds=sounds)
    assert [it["collaborator"] for it in items] == [None, "b", None, None, "a", None, None]
    trials = [it["trial"] for it in items]
    assert not any(it["trial"] for it in items if it["collaborator"])  # Instagram doesn't allow both
    assert trials.count(True) == 2 and trials[2] is True  # one in three of the others
    assert [it["tiktok_sound"]["song_clip_id"] for it in items] == ["1", "2", "1", "2", "1", "2", "1"]
    assert "rank" not in items[0]["tiktok_sound"]

    not_yet = items_for(3)
    ap.plan_extras(not_yet, ap.settings_of({"trial_reels": "all"}), trial_ok=False)
    assert not any(it["trial"] for it in not_yet) and not any(it["collaborator"] for it in not_yet)
    every = items_for(3)
    ap.plan_extras(every, ap.settings_of({"trial_reels": "all"}), trial_ok=True)
    assert all(it["trial"] for it in every) and all(it["tiktok_sound"] is None for it in every)
    first = items_for(2)
    ap.plan_extras(first, ap.settings_of({"collaborators": ["a"], "collab_every": 5}))
    assert first[0]["collaborator"] == "a"  # never invited before: the first reel does it


def test_metricool_post_carries_comment_trial_and_collaborator():
    s = ap.settings_of({"platforms": ["instagram", "youtube", "tiktok"]})
    item = {**items_for(1)[0], "first_comment": "Which line hit hardest?", "trial": True}
    info = json.loads(ap.metricool_post(item, s, "1", "https://cdn/x.mp4", None)["info"])
    assert info["firstCommentText"] == "Which line hit hardest?" and info["instagramData"]["type"] == "TRIAL_REEL"
    assert "showReelOnFeed" not in info["instagramData"] and info["autoPublish"] is True
    collab = {**item, "collaborator": "night.thoughts"}
    ig = json.loads(ap.metricool_post(collab, s, "1", "u", None)["info"])["instagramData"]
    assert ig["type"] == "REEL" and ig["collaborators"] == [{"username": "night.thoughts", "deleted": False}]
    off = json.loads(ap.metricool_post(item, ap.settings_of({"first_comment": False}), "1", "u", None)["info"])
    assert off["firstCommentText"] == ""


def test_tiktok_waits_for_its_sound_in_remind_mode():
    s = ap.settings_of({"platforms": ["instagram", "youtube", "tiktok"], "tiktok_sound": "remind"})
    posts = ap.metricool_posts(items_for(1)[0], s, "1", "https://cdn/x.mp4", 1200)
    assert [p["part"] for p in posts] == ["main", "tiktok"]
    main, tt = (json.loads(p["info"]) for p in posts)
    assert [p["network"] for p in main["providers"]] == ["instagram", "youtube"] and "tiktokData" not in main
    assert main["autoPublish"] is True
    assert [p["network"] for p in tt["providers"]] == ["tiktok"] and tt["autoPublish"] is False  # Metricool's app reminds
    assert tt["tiktokData"]["title"] == "YT" and tt["media"] == ["https://cdn/x.mp4"]
    only = ap.metricool_posts(items_for(1)[0], ap.settings_of({"platforms": ["tiktok"], "tiktok_sound": "remind"}), "1", "u", None)
    assert [p["part"] for p in only] == ["tiktok"]
    plain = ap.metricool_posts(items_for(1)[0], ap.settings_of({"platforms": ["instagram", "tiktok"]}), "1", "u", None)
    assert [p["part"] for p in plain] == ["main"]


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
    assert info["text"] == "Hook line" and info["publicationDate"]["dateTime"] == "2026-10-12T19:00:00"


# --------------------------------------------------------------------------- the panelrun commands

def test_prepare_render_posts_and_docs(tmp_path, monkeypatch):
    from reelforge import pipeline

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "abc123.mp3").write_bytes(b"ID3 song")
    template = {"id": "tpl-1", "data": {"project": template_project(),
                                       "files": [{"name": "song.mp3", "store": {"type": "raw", "ids": ["abc123"]}}]}}
    (assets / "id-lib1.mp3").write_text(base64.b64encode(b"ID3 one").decode())  # the panel keeps songs as base64
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({
        "settings": {"per_day": 2, "days": 2, "platforms": ["instagram", "youtube"]},
        "brand": {"id": "7327885", "timezone": "Asia/Jerusalem"},
        "template": template, "best": {"instagram": grid({19: 100, 12: 90})},
        "scheduled": [{"publicationDate": {"dateTime": "2026-10-12T19:00:00", "timezone": "Asia/Jerusalem"}}],
        "history": [{"title": "Old one", "clips": ["p_9"], "reply_to": "r0"}], "now": "2026-10-12T08:00",
        "library": [song("lib1.mp3", start=3), song("lib2.mp3")],
        "search_phrases": ["how to stop overthinking"],
        "insights": {"learnings": ['Did best: "Hook" — TikTok 9 views'], "trial_eligible": True},
        "replies": [{"id": "r0", "comment": "Already answered"}, {"id": "r1", "comment": "How do I stop waiting?", "platform": "tiktok"}]}))
    calls = []

    def writer(**kw):
        calls.append(kw)
        n = kw["count"]
        return {"model": "gemini-test", "ideas": [{"title": f"Idea {len(calls)}-{i}", "script": "Hook here? Then more words.",
                                                  "why": "", "caption": "Cap", "hashtags": ["x"], "youtube_title": "YT",
                                                  "first_comment": "Which line hit hardest?", "search_phrase": "how to stop overthinking"}
                                                 for i in range(n)]}

    need = panelrun.autopilot_prepare(inp, assets, tmp_path / "run", writer=writer)
    assert need == {"need": ["id-lib2.mp3"]} and not calls  # the picked songs' files come first, Gemini after
    (assets / "id-lib2.mp3").write_text(base64.b64encode(b"ID3 two").decode())
    pools = iter(range(100))
    monkeypatch.setattr(pipeline, "review_footage", lambda project, work, settings, out: {
        "opening": [clip(f"m_{next(pools)}")], "pool": [clip("p_9"), *(clip(f"p_{next(pools)}") for _ in range(4))],
        "rest_shots": 2})
    out = panelrun.autopilot_prepare(inp, assets, tmp_path / "run", writer=writer)
    assert out["ok"] and out["slots"] == 3  # 2 days x 2, minus the one already taken on day one
    plan = json.loads((tmp_path / "run" / "plan.json").read_text())
    assert calls[0]["post"] is True and "Old one" in calls[0]["avoid"]
    # the viewer's comment is answered first (the one already answered isn't), then the rest
    assert calls[0]["replies"] == [{"id": "r1", "comment": "How do I stop waiting?", "platform": "tiktok"}]
    assert calls[0]["count"] == 1 and calls[1]["count"] == 2 and "replies" not in calls[1]
    for kw in calls:
        assert (kw["length"], kw["comment"], kw["search"]) == ("short", True, True)
        assert kw["phrases"] == ["how to stop overthinking"] and kw["learnings"] == ['Did best: "Hook" — TikTok 9 views']
    items = plan["items"]
    assert [it["reply_to"] for it in items] == ["r1", None, None]
    assert all(it["first_comment"] == "Which line hit hardest?" and it["search_phrase"] == "how to stop overthinking"
               for it in items)
    assert [it["trial"] for it in items] == [False, True, False] and not any(it["collaborator"] for it in items)
    assert out["replies"] == 1 and out["trials"] == 1 and out["search_phrases"] == 1
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
        assert p["music"]["file"] == it["song"] and (work / it["song"]).read_bytes().startswith(b"ID3")
    assert len(keys) == len(set(keys))  # no clip in two reels
    songs = [it["song"] for it in items]
    assert set(songs) == {"uploads/lib1.mp3", "uploads/lib2.mp3"} and songs[0] != songs[1]

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
    assert (doc["account"], doc["kind"]) == ("main", "watch")
    assert doc["song"] == items[0]["song"].split("/")[-1]  # next runs avoid songs heard lately
    assert (doc["first_comment"], doc["reply_to"], doc["trial"], doc["metricool_tiktok"]) == \
        ("Which line hit hardest?", "r1", False, None)
    assert json.loads(json.loads(open(posts["file"]).read())[0]["info"])["firstCommentText"] == "Which line hit hardest?"
    # a TikTok post that waits for its sound is kept next to the main one
    (tmp_path / "mc2.json").write_text(json.dumps({items[0]["id"]: {"id": 1, "uuid": "u1"},
                                                   f"{items[0]['id']}/tiktok": {"id": 2, "uuid": "u2"}}))
    docs2 = panelrun.autopilot_docs(tmp_path / "run" / "plan.json", tmp_path / "run" / "results.json",
                                    tmp_path / "media.json", tmp_path / "covers.json", tmp_path / "mc2.json")
    doc2 = json.loads(open(json.loads(open(docs2["file"]).read())[0]["file_path"]).read())
    assert doc2["metricool"] == {"id": 1, "uuid": "u1"} and doc2["metricool_tiktok"] == {"id": 2, "uuid": "u2"}


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
    # a chosen opening: another reel's clip is fine when that reel is days away, not when it is near
    items = json.loads(plan.read_text())["items"]
    for it, slot in zip(items, ["2026-10-12T10:00", "2026-10-15T10:00", "2026-10-12T16:00"]):
        it["slot"] = slot
    plan.write_text(json.dumps({"items": items}))
    out = panelrun.autopilot_swap(plan, [], {"ap-2": clip("m_1"), "ap-0": clip("m_1")})
    assert out["swapped"] == {"ap-0": "m_1", "ap-2": None}  # ap-1 has m_1 3 days away; ap-0 has it 6 h from ap-2
    assert [x["key"] for x in config.load_project(tmp_path / "ap-0" / "project.json").footage.picks][0] == "m_1"


# --------------------------------------------------------------------------- accounts and comment videos

def test_accounts_have_their_own_documents_and_ids():
    assert (ap.settings_doc(None), ap.settings_doc("main"), ap.settings_doc("Space!")) == ("autopilot", "autopilot", "autopilot-space")
    assert (ap.insights_doc(""), ap.insights_doc("dilemmas")) == ("latest", "dilemmas")
    assert ap.account_of({}) == "main" and ap.account_of({"account": "psychology"}) == "psychology"
    dt = datetime(2026, 10, 20, 19, 0)
    assert ap.slot_id(dt) == "ap-20261020-1900" and ap.slot_id(dt, "space") == "ap-space-20261020-1900"
    s = ap.settings_of({"niche": "space", "brand_id": 123, "mix": "half", "name": " Space "})
    assert (s["niche"], s["brand_id"], s["mix"], s["name"]) == ("space", "123", "half", "Space")
    d = ap.settings_of({"niche": "cooking", "mix": "all", "brand_id": ""})
    assert (d["niche"], d["mix"], d["brand_id"]) == ("philosophy", "watch", None)


def test_kinds_mix_comment_videos_in():
    assert ap.kinds_for(3, "watch") == ["watch"] * 3
    assert ap.kinds_for(4, "half") == ["comment", "watch", "comment", "watch"]
    assert ap.kinds_for(6, "comments") == ["comment", "watch", "comment", "comment", "watch", "comment"]


def test_a_niche_sets_the_footage_and_a_comment_video_holds_its_line():
    from reelforge import timing
    from reelforge.assemble import plan_shots
    from reelforge.pipeline import _hook_display_end, split_hook
    from reelforge.script import parse_script

    space = ap.build_project(template_project(), "The sun is a star.", "Sun", niche="space")
    fs = space["footage"]
    assert (fs["palette"], fs["hook_query"], fs["allow_landscape"]) == ("space", "night sky", True)
    assert fs["hook_searches"][0] == "milky way" and space["style"]["look"]["match"] == "off"
    dilemmas = ap.build_project(template_project(), "Would you rather?", "T", niche="dilemmas")
    assert (dilemmas["footage"]["palette"], dilemmas["footage"]["hook_query"]) == ("choices", "sun over hills")
    assert "chess" in dilemmas["footage"]["theme"] and dilemmas["style"]["look"]["match"] == "off"
    # its longer reels follow the skyline closely, word by word, like the main account's
    assert dilemmas["style"].get("hook", {}).get("curve", "terrain") == "terrain"
    assert dilemmas["style"].get("hook", {}).get("reveal", "words") == "words"
    # the gym account: only dark gym footage (wide clips cropped, the darkest first, no women, boxing or
    # outdoors), no skyline: the opening line on a fixed arc with a shadow; its comment videos keep that
    # layout and are one clip of someone lifting
    with_searches = template_project()
    with_searches["footage"].update(queries=["golden hour lake"], hook_searches=["snow mountains"])
    gym = ap.build_project(with_searches, "Five habits keep men stuck.", "T", niche="gym")
    gf = gym["footage"]
    # the main account's reference pictures and searches stay behind: their colours made gym reels
    # search "mountains" and "forest" first
    assert (gf["items"], gf["queries"], gf["hook_searches"]) == ([], None, None)
    assert (gf["palette"], gf["hook_query"], gf["allow_landscape"], gf["portrait_weight"]) == ("gym", None, True, 0.0)
    assert "barbell" in gf["theme"] and {"woman", "boxing", "forest", "gymnast"} <= set(gf["avoid"])
    assert gym["style"]["look"]["brightness"] < 0 and gym["style"]["look"]["saturation"] < 1
    assert (gym["style"]["hook"]["curve"], gym["style"]["hook"]["fallback"], gym["style"]["hook"]["shadow"]) == \
        ("fixed", "arc", True)
    gym_line = ap.build_comment_project(template_project(), "Nothing ruins a man faster than", "T", music=None, niche="gym")
    assert (gym_line["style"]["hook"]["curve"], gym_line["style"]["hook"]["reveal"]) == ("fixed", "all")
    assert gym_line["footage"]["queries"] == ["lifting weights"]
    assert "gym" in {a["id"] for a in ap.ACCOUNTS}
    plain = ap.build_project(template_project(), "Hook.", "T")
    assert plain["footage"]["allow_landscape"] is False and plain["footage"].get("hook_searches") is None
    assert plain["footage"]["items"] == [{"path": "uploads/ref.jpg", "role": "reference"}]  # the main account keeps them
    line = "Would you rather know the date you die, or how?"
    c = ap.build_comment_project(template_project(), line, "Date or how", music=None, niche="dilemmas")
    assert c["script"] == line and c["voice"]["source"] == "none" and c["voice"]["file"] is None
    assert c["duration"]["target"] == ap.comment_seconds(line) == 8.5 and c["duration"]["max_intro"] == 0
    hook_style = c["style"]["hook"]
    assert (hook_style["mode"], hook_style["hold"], hook_style["reveal"], hook_style["curve"]) == ("terrain", 60.0, "all", "gentle")
    assert c["style"]["video"]["fade_out"] == 0 and c["music"]["file"] is None
    late = template_project()
    late["music"]["start_at"] = 3.2  # the template's song comes in after its hook
    m = ap.build_comment_project(late, line, "Date or how", niche="gym")["music"]
    assert (m["start_at"], m["end_at"], m["fade_in"]) == (0.0, None, 0.0)  # a comment video's music plays from frame one
    assert ap.comment_seconds("Why?") == 7.0 and ap.comment_seconds("word " * 40) == 10.0
    # one shot, the whole line up within the first seconds and on screen to the last frame
    proj = config.from_dict(Project, c)
    script = parse_script(proj.script, proj.auto_emphasis)
    speech = timing.reading_seconds(script.words, proj.voice.words_per_second)
    plan = timing.fit_duration(speech, proj.duration, proj.style.video.voice_delay, proj.style.video.tail)
    timing.synthetic_timings(script.words, plan.intro, plan.total - plan.outro)
    hook, body = split_hook(script, proj)
    shots = plan_shots(script.words, plan.total, proj.style.video, keep_until=hook[-1].end)
    assert plan.total == 8.5 and len(shots) == 1 and not body and plan.intro == 0 and hook[0].start == 0
    assert _hook_display_end(hook, body, proj, shots[0][1]) == plan.total


def test_space_and_people_footage_follow_the_script():
    from reelforge.script import footage_queries, parse_script

    qs = footage_queries(parse_script("If the sun vanished, the planets would drift into the dark galaxy."), 5, palette="space")
    assert qs[0] == "sun" and "planet" in qs and "nebula" in qs
    ps = footage_queries(parse_script("You check your phone before you think."), 4, palette="people")
    assert ps[0] == "texting" and "thinking" in ps


def test_quality_gate_knows_comment_videos_and_wide_skies():
    short = {"ok": True, "result": {"duration": 8.5, "warnings": []}}
    assert ap.quality_problems(short, [clip("a_1")], kind="comment") == []
    assert ap.quality_problems(short, [clip("a_1")])  # too short for a longer reel
    long = {"ok": True, "result": {"duration": 30, "warnings": []}}
    wide = [clip("s_1", 1920, 1080)]
    assert ap.quality_problems(long, wide) and ap.quality_problems(long, wide, wide_ok=True) == []


def test_prepare_mixes_comment_videos_for_an_account(tmp_path, monkeypatch):
    from reelforge import pipeline

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "abc123.mp3").write_bytes(b"ID3 song")
    template = {"id": "tpl-1", "data": {"project": template_project(),
                                       "files": [{"name": "song.mp3", "store": {"type": "raw", "ids": ["abc123"]}}]}}
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({
        "account": "dilemmas",
        "settings": {"per_day": 2, "days": 2, "platforms": ["instagram", "tiktok"], "niche": "dilemmas", "mix": "half",
                     "music": "template", "brand_id": "999"},
        "brand": {"id": "999", "timezone": "Asia/Jerusalem"}, "template": template,
        "best": {"instagram": grid({19: 100, 12: 90})}, "now": "2026-10-12T08:00",
        "replies": [{"id": "r1", "comment": "Which would you pick?", "account": "dilemmas"}]}))
    seen = {"watch": [], "lines": [], "search": []}

    def writer(**kw):
        seen["watch"].append(kw)
        return {"model": "g", "ideas": [{"title": f"W{len(seen['watch'])}-{i}", "script": "Would you trade it? More words.",
                                         "why": "", "caption": "C", "hashtags": [], "youtube_title": "Y"} for i in range(kw["count"])]}

    def line_writer(**kw):
        seen["lines"].append(kw)
        return {"model": "g", "ideas": lines_of(DILEMMA_LINES, kw["count"], youtube_title="When or how?", first_comment="How.")}

    def searcher(topic, seeds=None):
        seen["search"].append(seeds)
        return ["would you rather questions"]

    monkeypatch.setattr(pipeline, "review_footage", lambda project, work, settings, out: {
        "opening": [clip(f"m_{work.name}")], "pool": [clip(f"p_{work.name}x{i}") for i in range(4)],
        "rest_shots": 0 if project.voice.source == "none" else 2})
    out = panelrun.autopilot_prepare(inp, assets, tmp_path / "run", writer=writer, searcher=searcher, line_writer=line_writer)
    assert out["ok"] and out["account"] == "dilemmas" and out["slots"] == 4 and out["comment_videos"] == 2
    items = json.loads((tmp_path / "run" / "plan.json").read_text())["items"]
    assert [it["kind"] for it in items] == ["comment", "watch", "comment", "watch"]
    assert all(it["id"].startswith("ap-dilemmas-") and it["account"] == "dilemmas" for it in items)
    assert seen["search"][0][0] == "would you rather"  # the niche's searches
    assert seen["watch"][0]["niche"] == "dilemmas" and seen["watch"][0]["replies"][0]["id"] == "r1"
    # two lines wanted, two spare in case one repeats another's sentence
    assert seen["lines"][0]["count"] == 4 and seen["lines"][0]["niche"] == "dilemmas" and "examples" not in seen["lines"][0]
    assert [it["reply_to"] for it in items] == [None, "r1", None, None]  # a video reply is a longer video
    for it in items:
        p = json.loads((tmp_path / "run" / it["id"] / "project.json").read_text())
        if it["kind"] == "comment":
            assert "voice_request" not in it and p["voice"]["source"] == "none" and len(p["footage"]["picks"]) == 1
            assert it["first_comment"] == "How." and it["title"].startswith("L")
        else:
            assert it["voice_request"]["params"]["prompt"].startswith("Would you trade it?")
            assert len(p["footage"]["picks"]) == 5


def test_the_gym_account_posts_its_own_first_line_before_any_gemini_writes(tmp_path, monkeypatch):
    from reelforge import pipeline

    monkeypatch.setattr(pipeline, "review_footage", lambda project, work, settings, out: {
        "opening": [], "pool": [clip(f"p_{work.name}x{i}") for i in range(6)],
        "rest_shots": 0 if project.voice.source == "none" else 4})

    def run(avoid, name):
        inp = tmp_path / f"{name}.json"
        inp.write_text(json.dumps({
            "account": "gym", "avoid": avoid,
            "settings": {"per_day": 2, "days": 2, "platforms": ["instagram"], "niche": "gym", "mix": "half",
                         "music": "none", "brand_id": "997", "search": False},
            "brand": {"id": "997", "timezone": "Asia/Jerusalem"},
            "template": {"id": "tpl-1", "data": {"project": template_project(), "files": []}},
            "best": {"instagram": grid({19: 100, 12: 90})}, "now": "2026-10-12T08:00"}))
        asked = []

        def line_writer(**kw):
            asked.append(kw["count"])
            return {"model": "g", "ideas": lines_of(GYM_LINES, kw["count"], first_comment="Gravity.")}

        def writer(**kw):
            return {"model": "g", "ideas": [{"title": f"W{i}", "script": "Five habits keep men stuck. More words.", "why": "",
                                             "caption": "C", "hashtags": [], "youtube_title": "Y"} for i in range(kw["count"])]}

        panelrun.autopilot_prepare(inp, tmp_path, tmp_path / name, writer=writer, line_writer=line_writer)
        items = json.loads((tmp_path / name / "plan.json").read_text())["items"]
        return [it for it in items if it["kind"] == "comment"], asked

    comments, asked = run([], "first")
    assert comments[0]["script"] == "Nothing beats a man faster than..." and asked == [3]
    assert comments[0]["first_comment"] == "Comfort. It wins without a fight." and comments[1]["first_comment"] == "Gravity."
    comments, asked = run(["Nothing beats a man faster than"], "again")  # posted before: Gemini writes them all
    assert asked == [4] and all(c["script"] != "Nothing beats a man faster than..." for c in comments)


def test_each_comment_line_of_a_batch_is_a_different_sentence(tmp_path, monkeypatch):
    from reelforge import ideas, pipeline

    monkeypatch.setattr(pipeline, "review_footage", lambda project, work, settings, out: {
        "opening": [], "pool": [clip(f"p_{work.name}x{i}") for i in range(6)],
        "rest_shots": 0 if project.voice.source == "none" else 4})
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({  # three comment videos: the niche's own first line and two from Gemini
        "account": "gym", "history": [{"title": "Old", "line": "You know a man is serious when..."}],
        "settings": {"per_day": 2, "days": 3, "platforms": ["instagram"], "niche": "gym", "mix": "half",
                     "music": "none", "search": False, "posting": "manual", "tz": "Asia/Jerusalem"},
        "template": {"id": "tpl-1", "data": {"project": template_project(), "files": []}}, "now": "2026-10-12T08:00"}))

    def writer(**kw):
        return {"model": "g", "ideas": [{"title": f"W{i}", "script": "Five habits keep men stuck. More words.", "why": "",
                                         "caption": "C", "hashtags": [], "youtube_title": "Y"} for i in range(kw["count"])]}

    def writes(*answers):
        asked, answers = [], iter(answers)

        def line_writer(**kw):
            asked.append((kw["count"], kw["avoid"]))
            got = next(answers, ["Nothing humbles a man faster than..."])
            return {"model": "g", "ideas": [{"kind": "comment", "title": f"L{i}", "script": line, "why": "", "caption": "C",
                                             "hashtags": [], "youtube_title": "Y"} for i, line in enumerate(got)]}
        return line_writer, asked

    # the first line with another verb, the account's latest line with another word, and one line twice: each
    # goes; Gemini is asked again, with the batch's lines and the latest ones first
    line_writer, asked = writes(["Nothing humbles a man faster than...", "You know a man is lazy when...",
                                 "Discipline is just...", "Discipline is just..."],
                                ["Real strength shows up when...", "The lift that humbled me the most..."])
    panelrun.autopilot_prepare(inp, tmp_path, tmp_path / "run", writer=writer, line_writer=line_writer)
    items = json.loads((tmp_path / "run" / "plan.json").read_text())["items"]
    assert [it["script"] for it in items if it["kind"] == "comment"] == [
        "Nothing beats a man faster than...", "Discipline is just...", "Real strength shows up when..."]
    assert [n for n, _ in asked] == [4, 3]
    assert asked[0][1][:2] == ["Nothing beats a man faster than...", "You know a man is serious when..."]
    assert asked[1][1][:3] == ["Nothing beats a man faster than...", "Discipline is just...", "You know a man is serious when..."]
    # Gemini keeps writing the same sentence: the batch stops instead of repeating it
    line_writer, asked = writes()
    with pytest.raises(ideas.IdeasError, match="repeat the same sentence"):
        panelrun.autopilot_prepare(inp, tmp_path, tmp_path / "again", writer=writer, line_writer=line_writer)
    assert len(asked) == panelrun.LINE_TRIES


def test_comment_videos_render_without_a_voice(tmp_path, monkeypatch):
    work = tmp_path / "ap-space-1"
    work.mkdir()
    p = config.from_dict(Project, ap.build_comment_project(template_project(), "What is out there?", "T", niche="space"))
    config.save_project(p, work / "project.json")
    (tmp_path / "plan.json").write_text(json.dumps({"items": [{"id": "ap-space-1", "work": str(work), "kind": "comment"}]}))
    cmds = []

    def fake_run(cmd, env=None, **kw):
        cmds.append(cmd)
        (work / "output").mkdir()
        (work / "result.json").write_text(json.dumps({"ok": True, "result": {
            "video": "output/v.mp4", "duration": 8.0, "warnings": [], "cover_time": 3}}))
        (work / "output" / "credits.json").write_text(json.dumps([{"provider": "mixkit", "id": "7", "width": 1920, "height": 1080}]))

    monkeypatch.setattr(panelrun.subprocess, "run", fake_run)
    out = panelrun.autopilot_render(tmp_path / "plan.json", tmp_path / "no-voices.json")
    assert out["passed"] == 1 and "--voiceover" not in cmds[0]  # and a cropped starfield is fine for space


def test_an_account_posted_by_hand_needs_no_metricool(tmp_path, monkeypatch):
    from reelforge import pipeline

    s = ap.settings_of({"posting": "manual", "tz": "Asia/Jerusalem"})
    assert ap.by_hand(s) and s["tz"] == "Asia/Jerusalem"
    s = ap.settings_of({"posting": "fax", "tz": "Mars/Olympus"})
    assert not ap.by_hand(s) and (s["posting"], s["tz"]) == ("metricool", None)
    monkeypatch.setattr(pipeline, "review_footage", lambda project, work, settings, out: {
        "opening": [], "pool": [clip(f"p_{work.name}x{i}") for i in range(6)],
        "rest_shots": 0 if project.voice.source == "none" else 4})
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({  # no brand, no best times: its own time zone and the usual good hours
        "account": "gym", "queued": ["2026-10-12T19:00"],
        "settings": {"per_day": 2, "days": 2, "platforms": ["instagram", "tiktok"], "niche": "gym", "mix": "half",
                     "music": "none", "search": False, "posting": "manual", "tz": "Asia/Jerusalem"},
        "template": {"id": "tpl-1", "data": {"project": template_project(), "files": []}}, "now": "2026-10-12T08:00"}))

    def line_writer(**kw):
        return {"model": "g", "ideas": lines_of(GYM_LINES, kw["count"], caption="Be honest. #gym", hashtags=["gym"],
                                                first_comment="Regret.")}

    def writer(**kw):
        return {"model": "g", "ideas": [{"title": f"W{i}", "script": "Five habits keep men stuck. More words.", "why": "",
                                         "caption": "C", "hashtags": [], "youtube_title": "Y"} for i in range(kw["count"])]}

    out = panelrun.autopilot_prepare(inp, tmp_path, tmp_path / "run", writer=writer, line_writer=line_writer)
    plan_path = tmp_path / "run" / "plan.json"
    items = json.loads(plan_path.read_text())["items"]
    # 19:00 today waits to be posted already: today gets one more, tomorrow two
    assert out["slots"] == 3 and [it["slot"] for it in items] == ["2026-10-12T12:00", "2026-10-13T12:00", "2026-10-13T19:00"]
    assert all(it["tz"] == "Asia/Jerusalem" for it in items)
    small = tmp_path / "small.mp4"
    small.write_bytes(b"\0" * 1000)
    results = {items[0]["id"]: {"ok": True, "problems": [], "video": str(small), "cover_ms": 900, "duration": 8},
               items[1]["id"]: {"ok": True, "problems": [], "video": str(small), "duration": 30},
               items[2]["id"]: {"ok": True, "problems": ["Some clips repeat."], "video": str(small)}}
    results_path = tmp_path / "run" / "results.json"
    results_path.write_text(json.dumps(results))
    assert panelrun.autopilot_posts(plan_path, results_path, tmp_path / "none.json")["posts"] == 0
    videos = panelrun.autopilot_videos(plan_path, results_path)
    upload = json.loads(open(videos["file"]).read())
    assert upload == {items[0]["id"]: str(small), items[1]["id"]: str(small)}  # small enough: as rendered
    (tmp_path / "videos.json").write_text(json.dumps({items[0]["id"]: "vid0"}))  # the second didn't upload
    (tmp_path / "covers.json").write_text(json.dumps({items[0]["id"]: "cov0"}))
    docs = panelrun.autopilot_docs(plan_path, results_path, None, tmp_path / "covers.json", None, tmp_path / "videos.json")
    assert docs["statuses"] == {"ready": 1, "failed": 1, "held": 1}
    writes = json.loads(open(docs["file"]).read())
    first, second = (json.loads(open(w["file_path"]).read()) for w in writes[:2])
    assert (first["status"], first["posting"], first["video"], first["cover"]) == ("ready", "manual", "vid0", "cov0")
    # the gym niche's own first line goes first, with its first comment
    assert first["script"] == "Nothing beats a man faster than..." and first["text"] == "Be honest."
    assert first["first_comment"] == "Comfort. It wins without a fight." and first["metricool"] is None
    assert second["status"] == "failed" and second["problems"] == ["Its video couldn't be saved in the panel."]


def test_comment_videos_choose_their_one_long_clip_before_the_longer_reels(tmp_path, monkeypatch):
    from reelforge import pipeline

    def review(project, work, settings, out):  # every reel finds the same clips, the long one first
        fresh = [c for c in (clip("m_long"), *(clip(f"m_{i}") for i in range(12))) if c["key"] not in project.footage.banned]
        return {"opening": [], "pool": fresh, "rest_shots": 1 if project.voice.source == "none" else 3, "hook_slot": False}

    monkeypatch.setattr(pipeline, "review_footage", review)
    items = []
    for n, (kind, slot) in enumerate((("watch", "2026-10-18T19:00"), ("comment", "2026-10-19T12:00"))):
        work = tmp_path / f"ap-gym-{n}"
        work.mkdir()
        build = ap.build_comment_project if kind == "comment" else ap.build_project
        p = build(template_project(), "Nothing beats a man faster than...", "T", music=None, niche="gym")
        config.save_project(config.from_dict(Project, p), work / "project.json")
        items.append({"id": work.name, "kind": kind, "slot": slot, "work": str(work)})
    panelrun._plan_footage({"items": items, "banned": []})
    picks = {it["kind"]: [k["key"] for k in config.load_project(tmp_path / it["id"] / "project.json").footage.picks]
             for it in items}
    # the comment video comes second in time but chooses first: the long clip is its own, and its one
    # clip is enough (no opening to choose apart), so it doesn't fall back to clips other reels have
    assert picks["comment"] == ["m_long"] and "m_long" not in picks["watch"] and len(picks["watch"]) == 5
