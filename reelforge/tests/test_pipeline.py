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
