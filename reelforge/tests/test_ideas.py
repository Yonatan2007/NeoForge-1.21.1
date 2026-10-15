import json

import pytest

from reelforge import ideas, panelrun


class Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.text = code, body, json.dumps(body)

    def json(self):
        return self._body


class Gemini:
    def __init__(self, models, answer):
        self.models, self.answer, self.posts = models, answer, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.headers = headers
        return Resp(200, {"models": self.models})

    def post(self, url, params=None, headers=None, json=None, timeout=None):
        self.posts.append((url, json))
        self.headers = headers
        return self.answer


def model(name, methods=("generateContent",)):
    return {"name": f"models/{name}", "supportedGenerationMethods": list(methods)}


def test_pick_model_prefers_the_newest_stable_pro():
    models = [model("gemini-2.5-flash"), model("gemini-2.5-pro"), model("gemini-3.0-pro-preview"),
              model("gemini-3.0-flash"), model("gemini-3.0-flash-lite"), model("text-embedding-004", ["embedContent"]),
              model("gemini-3.0-flash-image")]
    assert ideas.pick_model(models) == "gemini-3.0-flash"
    assert ideas.pick_model(models + [model("gemini-3.0-pro")]) == "gemini-3.0-pro"
    assert ideas.pick_model([model("gemini-3.0-pro-preview")]) == "gemini-3.0-pro-preview"  # nothing stable


def test_a_model_without_quota_hands_over_to_the_next(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    text = json.dumps({"ideas": [{"title": "T", "script": "Hook? More.", "why": ""}]})
    ok = Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})
    spent = Resp(429, {"error": {"message": "You exceeded your current quota, limit: 0"}})
    g = Gemini([model("gemini-4-argon"), model("gemini-3.8-flash"), model("gemini-3.7-flash"),
                model("gemini-3.5-transcribe")], ok)
    answers = iter([spent, Resp(503, {"error": {"message": "This model is currently experiencing high demand."}}), ok])
    g.post = lambda url, params=None, headers=None, json=None, timeout=None: (g.posts.append((url, json)), next(answers))[1]
    assert ideas.generate(count=1, session=g)["model"] == "gemini-3.7-flash"  # no quota, then busy
    assert [u.rsplit("/", 1)[-1].split(":")[0] for u, _ in g.posts] == ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.7-flash"]
    assert ideas.ranked_models([model("gemini-3.5-transcribe"), model("gemini-3.5-flash")]) == ["gemini-3.5-flash"]
    with pytest.raises(ideas.IdeasError, match="quota"):  # every model spent: Gemini's own words
        ideas.generate(count=1, session=Gemini([model("gemini-3.8-flash")], spent))


def test_generate_sends_the_brief_and_reads_the_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    text = json.dumps({"ideas": [{"title": "The stranger", "script": "Who are you   when nobody\nwatches?  More.", "why": "Hook."},
                                 {"title": "", "script": "", "why": ""}]})
    g = Gemini([model("gemini-2.5-pro")], Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}))
    out = ideas.generate(topic="letting go", count=3, avoid=["Most people spend their lives"], session=g)
    assert out == {"model": "gemini-2.5-pro", "ideas": [{"title": "The stranger", "script": "Who are you when nobody watches? More.", "why": "Hook."}]}
    url, body = g.posts[0]
    assert g.headers == {"x-goog-api-key": "k"}
    brief = body["contents"][0]["parts"][0]["text"]
    assert url.endswith("models/gemini-2.5-pro:generateContent") and body["generationConfig"]["responseMimeType"] == "application/json"
    assert "Write 3 new scripts" in brief and "letting go" in brief and "Most people spend their lives" in brief


def test_post_copy_comes_with_each_script(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    text = json.dumps({"ideas": [{"title": "T", "script": "Hook? More.", "why": "",
                                  "caption": "Their silence was the answer. You don't need theirs. Save this.",
                                  "hashtags": ["#letting go", "closure", "closure", "self-worth"],
                                  "youtube_title": '"' + "w" * 120 + '"'}]})
    g = Gemini([], Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}))
    idea = ideas.generate(count=1, session=g, post=True)["ideas"][0]
    assert idea["caption"] == "Their silence was the answer.\n\nYou don't need theirs.\n\nSave this."
    assert idea["hashtags"] == []  # not asked for: the user doesn't want hashtags
    assert len(idea["youtube_title"]) == 100 and idea["youtube_title"].endswith("…")
    body = g.posts[0][1]
    item = body["generationConfig"]["responseSchema"]["properties"]["ideas"]["items"]
    assert {"caption", "youtube_title"} <= set(item["required"]) and "hashtags" not in item["properties"]
    assert "- hashtags:" not in body["contents"][0]["parts"][0]["text"]
    g = Gemini([], Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}))
    tagged = ideas.generate(count=1, session=g, post=True, hashtags=True)["ideas"][0]
    assert tagged["hashtags"] == ["lettinggo", "closure", "selfworth"]  # one tag per item, no spaces
    assert "hashtags" in g.posts[0][1]["generationConfig"]["responseSchema"]["properties"]["ideas"]["items"]["required"]
    assert ideas.caption_lines("Line one.\n\n\n\nLine two.") == "Line one.\n\nLine two."


def test_growth_brief_length_comment_search_learnings_and_replies(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    text = json.dumps({"ideas": [{"title": "T", "script": "Hook? More.", "why": "", "caption": "C", "hashtags": ["a"],
                                  "youtube_title": "Y", "first_comment": ' "What are you waiting for?" ',
                                  "search_phrase": "How To Stop Overthinking!"}]})
    g = Gemini([], Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}))
    idea = ideas.generate(count=1, session=g, post=True, length="short", comment=True, search=True,
                          phrases=["how to stop overthinking"], learnings=['Did best: "X" — TikTok 9 views'],
                          replies=[{"id": "r1", "comment": "How do I let go?", "platform": "tiktok", "note": "be gentle"}])["ideas"][0]
    assert idea["first_comment"] == "What are you waiting for?" and idea["search_phrase"] == "how to stop overthinking"
    body = g.posts[0][1]
    brief = body["contents"][0]["parts"][0]["text"]
    assert "60 to 85 words" in brief and "first two seconds" in brief
    assert "first_comment" in brief and "search_phrase" in brief and "- how to stop overthinking" in brief
    assert "- hashtags:" not in brief and "one of the hashtags" not in brief
    assert 'Did best: "X"' in brief and "How do I let go?" in brief and "be gentle" in brief
    assert brief.index("How do I let go?") < brief.index('Did best: "X"')
    item = body["generationConfig"]["responseSchema"]["properties"]["ideas"]["items"]
    assert {"first_comment", "search_phrase"} <= set(item["required"])
    # the Ideas page keeps its long scripts and asks for neither
    plain = ideas.prompt(count=2)
    assert "110 to 160 words" in plain and "first_comment" not in plain and "search_phrase" not in plain
    assert "first_comment" not in ideas.schema(post=True)["properties"]["ideas"]["items"]["properties"]


def test_errors_are_readable(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    # no key anywhere: Google refuses, and the error says how to add one
    unregistered = Resp(403, {"error": {"message": "Method doesn't allow unregistered callers (callers without established identity)."}})
    g = Gemini([], unregistered)
    with pytest.raises(ideas.IdeasError, match="Network secrets"):
        ideas.generate(session=g)
    assert g.headers == {}  # in the cloud the network adds the key header
    monkeypatch.setattr(ideas.requests, "Session", lambda: Gemini([], unregistered))
    task = tmp_path / "t.json"
    task.write_text(json.dumps({"data": {"kind": "ideas", "topic": "x"}}))
    out = panelrun.ideas(task)
    assert out["ok"] is False and "x-goog-api-key" in out["error"]
    monkeypatch.setenv("GEMINI_API_KEY", "bad")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    g = Gemini([], Resp(400, {"error": {"message": "API key not valid."}}))
    with pytest.raises(ideas.IdeasError, match="API key not valid"):
        ideas.generate(session=g)


def test_a_niche_shapes_the_brief_and_comment_lines(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    space = ideas.prompt(count=2, post=True, niche="space")
    assert "space and science" in space and "keep the most surprising part for the end" in space
    assert "late-night philosophical" not in space
    main = ideas.prompt(count=2)
    assert "late-night philosophical" in main and "uncomfortable truth" in main
    text = json.dumps({"ideas": [{"line": ' "Would you rather know the date you die, or how?" ', "title": "Date or how",
                                  "caption": "Be honest.", "youtube_title": "Would you rather know when or how?",
                                  "first_comment": "How. Knowing the date would ruin every birthday.",
                                  "search_phrase": "Would You Rather!"},
                                 {"line": "  ", "title": "x", "caption": "", "youtube_title": ""}]})
    g = Gemini([], Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}))
    out = ideas.generate_lines(count=2, niche="dilemmas", session=g, comment=True, search=True,
                               phrases=["would you rather questions"])
    assert out["ideas"] == [{"kind": "comment", "title": "Date or how", "script": "Would you rather know the date you die, or how?",
                             "why": "", "caption": "Be honest.", "youtube_title": "Would you rather know when or how?",
                             "hashtags": [], "first_comment": "How. Knowing the date would ruin every birthday.",
                             "search_phrase": "would you rather"}]
    body = g.posts[0][1]
    brief = body["contents"][0]["parts"][0]["text"]
    assert "moral dilemmas" in brief and "no voice" in brief and "Never ask for comments" in brief
    assert "first_comment" in brief and "- would you rather questions" in brief and "- hashtags:" not in brief
    item = body["generationConfig"]["responseSchema"]["properties"]["ideas"]["items"]
    assert {"line", "caption", "first_comment", "search_phrase"} <= set(item["required"]) and "hashtags" not in item["properties"]
