import pytest

from reelforge import config
from reelforge.config import FootageItem, Project
from reelforge.pipeline import assign_sources, project_path, slugify, split_hook
from reelforge.script import parse_script


def project(**footage):
    p = config.from_dict(Project, config.preset_dict("reference"))
    p.script = "If you lost your memory, who would you trust? Not who would show up. The real one."
    for k, v in footage.items():
        setattr(p.footage, k, v)
    return p


def test_split_hook_by_mode():
    p = project()
    s = parse_script(p.script)
    hook, body = split_hook(s, p)
    assert [w.text for w in hook][-1] == "trust?" and body[0].text == "Not"
    p.style.hook.mode = "center"
    assert split_hook(s, p) == ([], s.words)
    p.style.hook.mode, p.style.hook.sentences = "terrain", 2
    assert split_hook(s, p)[1][0].text == "The"


def test_user_footage_fills_shots_in_order_with_pins_first():
    items = [FootageItem(path="a.mp4"), FootageItem(path="b.jpg", shot=2),
             FootageItem(path="r.jpg", role="reference")]
    p = project(items=items, queries=["sea", "sky"])
    srcs, warnings = assign_sources(p, parse_script(p.script), 4, hook_terrain=True)
    assert [s.describe() for s in srcs] == ["user:a.mp4", "stock:sea", "user:b.jpg", "stock:sky"]
    assert not warnings


def test_user_searches_drop_filler_and_theme_the_fallbacks():
    from reelforge.pipeline import search_terms, themed
    assert search_terms("only night footage") == "night" and search_terms("sea") == "sea"
    assert search_terms("footage") == "footage"
    assert themed(["couple silhouette sunset", "rain window night", "sea"], ["only night footage"]) == \
        ["couple silhouette night", "rain window night", "sea night"]
    p = project(queries=["only night footage"])
    p.footage.items = []
    srcs, _ = assign_sources(p, parse_script(p.script), 3, hook_terrain=True)
    assert [s.query for s in srcs] == ["night"] * 3


def test_no_in_a_search_becomes_words_to_avoid():
    from reelforge.footage import Candidate, describes
    from reelforge.pipeline import split_search, themed
    q, avoid = split_search("moody rain dark nature no pepole no sun rize")
    assert q == "moody rain dark nature"
    assert {"people", "man", "woman", "couple", "sunrise", "sunset", "sun"} <= avoid
    assert themed(["lonely man walking", "ocean waves"], ["moody rain no pepole"]) == ["ocean waves moody rain"]

    def clip(slug):
        return Candidate("mixkit", "1", f"https://mixkit.co/free-stock-video/{slug}-1/", "", 1080, 1920, 10, "", None, "")
    assert describes(clip("woman-walking-in-the-rain"), avoid)
    assert describes(clip("sunset-over-the-sea"), avoid)
    assert not describes(clip("foggy-sky-during-a-starry-night-in-the-forest"), avoid)
    assert not describes(clip("rain-on-leaves"), set())


def test_hook_shot_gets_the_hook_query_when_stock():
    p = project(queries=None, hook_query="mountains")
    p.footage.items = []
    srcs, _ = assign_sources(p, parse_script(p.script), 5, hook_terrain=True)
    assert srcs[0].query == "mountains" and all(s.kind == "stock" for s in srcs)
    queries = [s.query for s in srcs]
    assert queries.count("mountains") == 1 and len(set(queries)) == 5  # no shot repeats the hook search


def test_without_stock_user_media_repeats_and_nothing_is_an_error():
    p = project(items=[FootageItem(path="a.mp4"), FootageItem(path="b.mp4")], stock=False)
    srcs, _ = assign_sources(p, parse_script(p.script), 5, hook_terrain=False)
    assert [s.item.path for s in srcs] == ["a.mp4", "b.mp4", "a.mp4", "b.mp4", "a.mp4"]
    with pytest.raises(ValueError, match="No footage"):
        assign_sources(project(items=[], stock=False), parse_script(p.script), 2, False)


def test_extra_uploads_and_bad_pins_are_reported():
    items = [FootageItem(path=f"{i}.mp4") for i in range(3)] + [FootageItem(path="x.mp4", shot=9)]
    p = project(items=items)
    srcs, warnings = assign_sources(p, parse_script(p.script), 2, hook_terrain=False)
    assert len(srcs) == 2 and any("shot 10" in w for w in warnings) and any("not used" in w for w in warnings)


def test_project_paths_are_relative_to_the_project(tmp_path):
    assert project_path("uploads/a.mp3", tmp_path) == str(tmp_path / "uploads/a.mp3")
    assert project_path("/abs/a.mp3", tmp_path) == "/abs/a.mp3"
    assert project_path("https://x/y.mp3", tmp_path) == "https://x/y.mp3"


def test_plan_cuts_the_hook_shot_when_the_second_sentence_starts(tmp_path):
    from reelforge.config import Settings
    from reelforge.pipeline import plan_project

    p = project(queries=["sea", "sky"])
    p.voice.source = "none"
    plan = plan_project(p, tmp_path, Settings(home_dir=tmp_path))
    assert plan["hook"][-1] == "trust?"
    assert plan["shots"][0]["text"].endswith("trust?")  # the hook shot holds the hook only
    assert plan["shots"][1]["text"].startswith("Not")


def test_slugs_keep_every_alphabet():
    assert slugify("Say it today. Just say it.") == "say-it-today-just-say-it"
    assert slugify("Кто ты, если забудешь всё?") == "кто-ты-если-забудешь-всё"
    assert slugify("Привет") != slugify("Пока") and slugify("?!") == "reel"


# --------------------------------------------------------------------------- stock review

class _Clips:
    """A stock site with clips named by id, the same for every search."""
    name = "fake"

    def __init__(self, tmp_path, ids):
        from reelforge import footage
        import requests
        self.session, self.thumb_dir = requests.Session(), tmp_path / "thumbs"
        self.cands = [footage.Candidate(provider="fake", id=str(i), page_url=f"https://x/{i}",
                                        download_url=f"u{i}", width=1080, height=1920, duration=20,
                                        author="", thumbnail=None, query="q") for i in ids]

    def search(self, query, per_page=15, allow_landscape=True, wide=False):
        import dataclasses
        return [dataclasses.replace(c, query=query) for c in self.cands]

    def resolve(self, url):
        return url


def _stock_project(**footage):
    p = project(items=[], queries=None, hook_query="mountains", **footage)
    p.style.hook.mode = "terrain"
    return p


def test_review_lists_opening_and_pool_clips(tmp_path, monkeypatch):
    from reelforge import pipeline
    from reelforge.config import Settings
    clips = _Clips(tmp_path, range(30))
    monkeypatch.setattr(pipeline, "_providers", lambda project, settings: [clips])
    p = _stock_project(banned=["fake_0"])
    settings = Settings(cache_dir=tmp_path / "cache")
    out = pipeline.review_footage(p, tmp_path, settings, tmp_path / "review", extra=4)
    keys = [c["key"] for c in out["opening"] + out["pool"]]
    assert 1 <= len(out["opening"]) <= 6 and "fake_0" not in keys and len(set(keys)) == len(keys)
    assert len(out["pool"]) == out["rest_shots"] + 4 == out["stock_shots"] - 1 + 4
    assert [c["tile"] for c in out["opening"] + out["pool"]] == list(range(len(keys)))
    assert (tmp_path / "review" / "review.jpg").is_file() and out["tile"]["w"] == 180


def test_render_uses_reviewed_picks_first_and_never_banned_clips(tmp_path, monkeypatch):
    from reelforge import footage, pipeline
    from reelforge.config import Settings
    clips = _Clips(tmp_path, range(30))
    monkeypatch.setattr(pipeline, "_providers", lambda project, settings: [clips])
    monkeypatch.setattr(footage, "download", lambda url, dst, **kw: dst)
    pick = lambda i, **kw: {**footage.pick_of(clips.cands[i]), **kw}
    p = _stock_project(picks=[pick(9, slot="opening"), pick(5), pick(4)], banned=["fake_4", "fake_1"])
    script = parse_script(p.script)
    sources, _ = assign_sources(p, script, 5, hook_terrain=True)
    paths, picks, warnings = pipeline.choose_stock(p, script, sources, True, Settings(cache_dir=tmp_path), [])
    used = [c.key for c, _ in picks]
    assert used[:2] == ["fake_9", "fake_5"]  # the opening pick, then the reviewed shot pick
    assert len(used) == 5 == len(set(used)) and not {"fake_4", "fake_1"} & set(used)
    assert sorted(paths) == [0, 1, 2, 3, 4] and paths[0].name == "fake_9.mp4" and not warnings
