"""Script ideas from Google Gemini.

The control panel's Ideas page asks for scripts (an "ideas" task); Claude
runs ``python -m reelforge.panelrun ideas`` here, which calls the Gemini
API. The key is a network secret of Claude's environment (the proxy adds an
``x-goog-api-key`` header to requests to generativelanguage.googleapis.com),
or, for local runs, ``GEMINI_API_KEY`` in the environment; it is never kept
in the panel. ``GEMINI_MODEL`` picks the model; otherwise the newest stable
Gemini Pro (or Flash) the key can use is chosen from the API's model list.
"""
from __future__ import annotations

import json
import os
import re

import requests

API = "https://generativelanguage.googleapis.com/v1beta"


class IdeasError(RuntimeError):
    pass


NO_KEY = ("Gemini has no API key from this Claude environment. Add it in the environment's settings: "
          "Edit → Network secrets, allowed website generativelanguage.googleapis.com, custom header "
          "x-goog-api-key with your key (no prefix); then start a new session.")


def _headers() -> dict:
    """The key header for local runs; in Claude's cloud environment the network adds it."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return {"x-goog-api-key": key} if key else {}


def _version(name: str) -> tuple[float, ...]:
    return tuple(float(x) for x in re.findall(r"\d+(?:\.\d+)?", name.split("/")[-1])[:2]) or (0.0,)


def pick_model(models: list[dict]) -> str:
    """The best text model in a ``models.list`` answer: generateContent,
    not a lite/preview/experimental/image/audio variant, Pro over Flash,
    the highest version first."""
    usable = [m for m in models if "generateContent" in (m.get("supportedGenerationMethods") or [])
              and "gemini" in m.get("name", "")]
    special = ("lite", "image", "tts", "audio", "live", "embedding", "vision", "thinking", "robotics", "computer")
    stable = [m for m in usable if not any(w in m["name"] for w in special + ("preview", "exp"))]
    pool = stable or [m for m in usable if not any(w in m["name"] for w in special)] or usable
    if not pool:
        raise IdeasError("Your Gemini key can't use any text model.")

    def rank(m: dict) -> tuple:
        name = m["name"]
        tier = 2 if "pro" in name else 1 if "flash" in name else 0
        return (_version(name), tier, "latest" not in name)

    return max(pool, key=rank)["name"].split("/", 1)[-1]


def _model(session: requests.Session, headers: dict) -> str:
    if os.environ.get("GEMINI_MODEL"):
        return os.environ["GEMINI_MODEL"]
    r = session.get(f"{API}/models", params={"pageSize": 200}, headers=headers, timeout=30)
    _raise(r)
    return pick_model(r.json().get("models", []))


def _raise(r: requests.Response) -> None:
    if r.status_code < 400:
        return
    try:
        msg = r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        msg = r.text[:300]
    if r.status_code in (401, 403) and ("unregistered callers" in msg or "API key" in msg and "missing" in msg):
        raise IdeasError(NO_KEY)
    raise IdeasError(f"Gemini said: {msg} (HTTP {r.status_code})")


SCHEMA = {
    "type": "OBJECT",
    "properties": {"ideas": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"title": {"type": "STRING"}, "script": {"type": "STRING"}, "why": {"type": "STRING"}},
        "required": ["title", "script", "why"]}}},
    "required": ["ideas"],
}


def prompt(topic: str = "", count: int = 5, tone: str = "", avoid: list[str] | None = None,
           examples: list[str] | None = None) -> str:
    lines = [
        f"Write {count} new scripts for vertical short videos (Instagram Reels, YouTube Shorts, TikTok) on an account "
        "of late-night philosophical thoughts about love, loss, self-worth and becoming who you are.",
        "Each script is read by a calm narrator over moody stock footage, with one word on screen at a time.",
        "Rules for every script:",
        "- 110 to 160 words, in the second person, plain spoken English, no lists, no emojis, no hashtags.",
        "- The first sentence is the hook: under 14 words, a question or a sharp claim that stops the scroll. "
        "It is shown along the skyline, so keep it short.",
        "- Build from the hook to an uncomfortable truth, then turn to something hopeful and end on a line "
        "that echoes the hook.",
        "- Mark the 2 to 4 strongest words with *asterisks* (one word each).",
        "- Every script takes a different angle; none may repeat an idea below.",
        "Also give each a short title (under 8 words) and one sentence on why it should do well.",
    ]
    if topic:
        lines.append(f"Topic or angle to write about: {topic}")
    if tone:
        lines.append(f"Tone: {tone}")
    if examples:
        lines.append("Scripts from this account that did well (match their voice, not their ideas):")
        lines += [f'"""{e.strip()[:1200]}"""' for e in examples[:3]]
    if avoid:
        lines.append("Ideas already used (do not repeat them):")
        lines += [f"- {a.strip()[:200]}" for a in avoid[:40] if a.strip()]
    return "\n".join(lines)


def generate(topic: str = "", count: int = 5, tone: str = "", avoid: list[str] | None = None,
             examples: list[str] | None = None, session: requests.Session | None = None) -> dict:
    """{model, ideas: [{title, script, why}]} from Gemini."""
    count = max(1, min(int(count or 5), 10))
    headers = _headers()
    session = session or requests.Session()
    model = _model(session, headers)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt(topic, count, tone, avoid, examples)}]}],
        "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }
    r = session.post(f"{API}/models/{model}:generateContent", headers=headers, json=body, timeout=180)
    _raise(r)
    data = r.json()
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        ideas = json.loads(text)["ideas"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or (data.get("candidates") or [{}])[0].get("finishReason")
        raise IdeasError(f"Gemini's answer couldn't be read ({reason or exc}). Try again.") from exc
    clean = []
    for i in ideas:
        script = re.sub(r"\s+", " ", str(i.get("script", ""))).strip()
        if script:
            clean.append({"title": str(i.get("title", "")).strip()[:80] or script.split(".")[0][:60],
                          "script": script, "why": str(i.get("why", "")).strip()[:300]})
    if not clean:
        raise IdeasError("Gemini returned no scripts. Try again.")
    return {"model": model, "ideas": clean[:count]}
