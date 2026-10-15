import json
from datetime import datetime

from reelforge import autopilot as ap, insights, panelrun

TZ = "Asia/Jerusalem"  # UTC+3 in October


def ig(at, text, views, likes=1, saves=0, shares=0, watch=4.0, url="https://ig/1"):
    """A row of the Instagram answer, in insights.METRICS order."""
    return [at, text, url, str(views), str(views - 10), str(likes), "0", str(saves), str(shares), str(watch), "25.0"]


def tt(at, text, views, likes=1, shares=0, duration=48, url="https://tt/1"):
    return [at, text, url, str(duration), str(views), None, str(likes), "0", str(shares), None, None]


def reel(rid, slot, caption, script="Hook line. More words.", **kw):
    return {"id": rid, "slot": slot, "tz": TZ, "caption": caption, "script": script, "title": f"Title {rid}",
            "youtube_title": f"YT {rid}", "duration": 48, **kw}


REELS = [reel("ap-1", "2026-10-11T19:00", "You are not loyal to them.\n\nMore.", "You are not loyal. Rest."),
         reel("ap-2", "2026-10-11T21:00", "You are waiting for a readiness.\n\nMore.", "You live as if you had a spare life. Rest."),
         reel("ap-3", "2026-10-12T10:00", "Forgive your younger self.\n\nMore.", "You punish yourself. Rest."),
         reel("ap-4", "2026-10-14T10:00", "Too new to judge.\n\nMore.", "New one. Rest.")]
STATS = {
    "instagram": {"rows": [ig("20261011160758", "You are not loyal to them.\n\nMore.\n\n#a", 160),
                           ig("20261011180341", "You are waiting for a readiness.\n\nMore.", 170, likes=3),
                           ig("20261012070304", "Forgive your younger self.\n\nMore.", 180),
                           ig("20261014070310", "Too new to judge.\n\nMore.", 20),
                           ig("20261009143249", "most people spend their lives as someone else.\n\nSave this.", 140),
                           ["", "a row without a date"]]},
    "tiktok": {"rows": [tt("20261011160511", "You are not loyal to them. More. #a", 600),
                        tt("20261011180335", "You are waiting for a readiness. More.", 87000, likes=18000, shares=100),
                        tt("20261012070228", "Forgive your younger self. More.", 900, likes=30),
                        tt("20261009142819", "most people spend their lives as someone else.", 80000, likes=300)]},
    "youtube": {"rows": [["20261011160626", "YT ap-1", "https://yt/1", None, None, None, None, None]]},
}
NOW = datetime(2026, 10, 14, 8, 0)  # UTC


def test_rows_followers_and_eligibility():
    rows = insights.rows_of("instagram", STATS["instagram"])
    assert len(rows) == 5 and rows[0]["views"] == 160.0 and rows[0]["url"] == "https://ig/1"
    assert rows[0]["at"] == datetime(2026, 10, 11, 16, 7, 58) and rows[0]["watch"] == 4.0
    assert insights.followers_of({"rows": [["51.0", "20261012"], ["118.0", "20261013"], [None, "20261014"]]}) == 118
    assert insights.followers_of({"rows": []}) is None
    assert not insights.trial_eligible({"instagram": 1}) and insights.trial_eligible({"instagram": 1200})
    assert not insights.trial_eligible({})
    assert insights.codes("tiktok")[4] == "TKPO07"


def test_posts_are_matched_to_their_reels_by_time_then_text():
    posts = [p for net in insights.METRICS for p in insights.rows_of(net, STATS[net])]
    found, others = insights.match(posts, REELS)
    assert set(found) == {"ap-1", "ap-2", "ap-3", "ap-4"}
    assert set(found["ap-1"]) == {"instagram", "tiktok", "youtube"}
    assert found["ap-2"]["tiktok"]["views"] == 87000
    assert [o["text"][:11] for o in others] == ["most people", "most people"]  # made outside autopilot
    # no time: the text decides
    late = [{**posts[0], "at": None}]
    assert set(insights.match(late, REELS)[0]) == {"ap-1"}


def test_learnings_rank_a_platform_hit_first_and_keep_new_reels_out():
    doc, again = insights.build(STATS, REELS, tz=TZ, now=NOW, followers={"instagram": 1, "tiktok": 118})
    assert doc["trial_eligible"] is False and doc["followers"] == {"instagram": 1, "tiktok": 118}
    assert doc["judged"] == 4  # ap-4 is under a day old; the outside video counts once for both platforms
    assert [e["id"] for e in doc["top"]][:2] == ["ap-2", None]
    best = doc["learnings"][0]
    assert best.startswith('Did best: "You live as if you had a spare life." — TikTok 87,000 views (')
    assert "18,000 likes" in best and "Instagram 170 views" in best
    assert any(line.startswith("Did least") for line in doc["learnings"])
    assert any("Instagram viewers watch 4.0 s of a 48 s reel" in line for line in doc["learnings"])
    assert doc["reels"]["ap-4"]["score"] is None and doc["reels"]["ap-4"]["stats"]["instagram"]["views"] == 20
    assert doc["reels"]["ap-2"]["stats"]["tiktok"]["shares"] == 100
    assert doc["usual"]["tiktok"]["views"] > 0 and again == []
    json.dumps(doc)  # it goes into the panel's database as is


def test_trial_reels_are_judged_after_two_days():
    settings = ap.settings_of({"platforms": ["instagram", "tiktok"]})
    reels = [reel("t-win", "2026-10-11T19:00", "You are not loyal to them.\n\nMore.", trial=True,
                  master_url="https://cdn/win.mp4", metricool={"uuid": "u1"}, first_comment="Who?"),
             reel("t-lose", "2026-10-11T21:00", "You are waiting for a readiness.\n\nMore.", trial=True, metricool={"uuid": "u2"}),
             reel("t-fail", "2026-10-13T21:00", "Refused.\n\nMore.", trial=True, master_url="https://cdn/f.mp4",
                  metricool={"uuid": "u3"}),
             reel("t-new", "2026-10-13T23:00", "Fresh.\n\nMore.", trial=True, metricool={"uuid": "u4"}),
             reel("t-done", "2026-10-12T10:00", "Forgive your younger self.\n\nMore.", trial=True)]
    stats = {"instagram": {"rows": [ig("20261011160758", "You are not loyal to them.\n\nMore.", 900),
                                    ig("20261011180341", "You are waiting for a readiness.\n\nMore.", 110),
                                    ig("20261012070304", "Forgive your younger self.\n\nMore.", 5000),
                                    ig("20261009143249", "Other one.", 100), ig("20261008143249", "Another.", 120)]}}
    live = [{"uuid": "u3", "providers": [{"network": "instagram", "status": "ERROR"}],
             "publicationDate": {"dateTime": "2026-10-13T21:00:00", "timezone": TZ}},
            {"uuid": "x", "providers": [], "publicationDate": {"dateTime": "2026-10-14T19:00:00", "timezone": TZ}}]
    prior = {"trials": {"t-done": {"status": "kept"}}}
    doc, again = insights.build(stats, reels, tz=TZ, now=NOW, live=live, prior=prior, settings=settings, brand_id="7")
    t = doc["trials"]
    assert t["t-win"]["status"] == "shared" and t["t-lose"]["status"] == "kept" and t["t-fail"]["status"] == "failed"
    assert t["t-new"] == {"status": "running"} and t["t-done"] == {"status": "kept"}  # decided ones stay decided
    assert [p["slot_id"] for p in again] == ["t-win", "t-fail"]
    for p in again:
        info = json.loads(p["info"])
        assert [x["network"] for x in info["providers"]] == ["instagram"] and info["instagramData"]["type"] == "REEL"
        assert p["part"] == "repost" and p["blogId"] == "7"
    win = json.loads(next(p for p in again if p["slot_id"] == "t-win")["info"])
    assert win["media"] == ["https://cdn/win.mp4"] and win["firstCommentText"] == "Who?"
    # the next free hours: 11:00 local now (08:00 UTC); 19:00 is taken
    dates = sorted(p["date"] for p in again)
    assert dates == ["2026-10-14T12:00:00+03:00", "2026-10-14T21:00:00+03:00"]
    assert t["t-win"]["repost_at"] in dates


def test_next_free_hour_keeps_away_from_other_posts():
    now = datetime(2026, 10, 14, 11, 30)
    taken = [datetime(2026, 10, 14, 19, 0), datetime(2026, 10, 14, 12, 0)]
    assert insights.next_free(taken, now, ["08:00", "23:30"]) == datetime(2026, 10, 14, 21, 0)
    late = datetime(2026, 10, 14, 23, 10)
    assert insights.next_free([], late, ["08:00", "23:30"]) == datetime(2026, 10, 15, 19, 0)


def test_panelrun_insights_reads_the_saved_documents(tmp_path):
    saved = tmp_path / "db" / "autopilot"
    saved.mkdir(parents=True)
    for r in REELS:
        body = {k: v for k, v in r.items() if k != "id"}
        (saved / f"{r['id']}.json").write_text(json.dumps(body))
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"brand": {"id": "7", "timezone": TZ}, "stats": STATS, "reels_dir": str(saved),
                               "followers": {"instagram": {"rows": [["1.0", "20261013"]]}}, "now": "2026-10-14T08:00:00Z"}))
    out = panelrun.autopilot_insights(inp, tmp_path / "out")
    assert out["ok"] and out["judged"] == 4 and out["followers"] == {"instagram": 1} and out["posts_again"] == 0
    assert out["best"][0] == "You live as if you had a spare life."
    doc = json.loads((tmp_path / "out" / "insights.json").read_text())
    assert set(doc["reels"]) == {"ap-1", "ap-2", "ap-3", "ap-4"}
    assert json.loads((tmp_path / "out" / "reposts.json").read_text()) == []
    codes = panelrun.insights_codes()
    assert codes["posts"]["instagram"][0] == "IGRE02" and codes["followers"]["instagram"] == ["IGEV01"]


def test_counts_of_one_read_singular():
    assert insights._many(1, "saves") == "1 save"
    assert insights._many(1.0, "views") == "1 view"
    assert insights._many(18822, "likes") == "18,822 likes"


def test_learnings_compare_comment_videos_with_longer_ones():
    def entry(i, kind, score):
        return {"id": f"{kind}{i}", "kind": kind, "score": score, "hook": f"{kind} {i}", "duration": 8 if kind == "comment" else 40,
                "times": {"tiktok": round(2 ** score, 2)}, "stats": {"tiktok": {"views": 100}}}
    ranked = sorted([entry(i, "comment", 1.5) for i in range(3)] + [entry(i, "watch", 0.0) for i in range(3)],
                    key=lambda e: -e["score"])
    lines = insights.learnings(ranked, [], TZ)
    assert "Comment videos did 2.8× better than the longer ones." in lines
    assert not any(line.startswith("Reels under 35 s") for line in lines)  # comment videos aren't "short reels"
