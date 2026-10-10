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
