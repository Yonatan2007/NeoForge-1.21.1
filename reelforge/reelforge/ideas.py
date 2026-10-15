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
import logging
import os
import re

import requests

from . import niches

log = logging.getLogger(__name__)

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


def ranked_models(models: list[dict]) -> list[str]:
    """The text models in a ``models.list`` answer, best first:
    generateContent, not a lite/preview/experimental/image/audio variant
    (previews only when nothing stable is left), the highest version first,
    Pro over Flash."""
    usable = [m for m in models if "generateContent" in (m.get("supportedGenerationMethods") or [])
              and "gemini" in m.get("name", "")]
    special = ("lite", "image", "tts", "audio", "live", "embedding", "vision", "thinking", "robotics", "computer",
               "transcribe", "banana")
    stable = [m for m in usable if not any(w in m["name"] for w in special + ("preview", "exp"))]
    pool = stable or [m for m in usable if not any(w in m["name"] for w in special)] or usable
    if not pool:
        raise IdeasError("Your Gemini key can't use any text model.")

    def rank(m: dict) -> tuple:
        name = m["name"]
        tier = 2 if "pro" in name else 1 if "flash" in name else 0
        return (_version(name), tier, "latest" not in name)

    return [m["name"].split("/", 1)[-1] for m in sorted(pool, key=rank, reverse=True)]


def pick_model(models: list[dict]) -> str:
    """The best text model in a ``models.list`` answer (``ranked_models``)."""
    return ranked_models(models)[0]


QUOTA_TRIES = 4  # models tried in turn while Gemini says their quota is used up (a new model may have none on a free key)
BUSY = (429, 503)  # ... or that they are overloaded


def _models(session: requests.Session, headers: dict) -> list[str]:
    if os.environ.get("GEMINI_MODEL"):
        return [os.environ["GEMINI_MODEL"]]
    r = session.get(f"{API}/models", params={"pageSize": 200}, headers=headers, timeout=30)
    _raise(r)
    return ranked_models(r.json().get("models", []))


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
POST_FIELDS = {"caption": {"type": "STRING"}, "youtube_title": {"type": "STRING"}}
TAGS_FIELD = {"hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}}
COMMENT_FIELD = {"first_comment": {"type": "STRING"}}
LINE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"ideas": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"line": {"type": "STRING"}, "title": {"type": "STRING"}, "caption": {"type": "STRING"},
                       "youtube_title": {"type": "STRING"}},
        "required": ["line", "title", "caption", "youtube_title"]}}},
    "required": ["ideas"],
}
SEARCH_FIELD = {"search_phrase": {"type": "STRING"}}
# words per script and how long that reads (the narrator speaks ~2.9 words a second)
LENGTHS = {"short": (60, 85, "about 20 to 30 seconds"), "medium": (85, 115, "about 30 to 40 seconds"),
           "long": (110, 160, "about 40 to 55 seconds")}


def schema(post: bool = False, comment: bool = False, search: bool = False, hashtags: bool = False) -> dict:
    """The answer's JSON schema; ``post`` adds each reel's caption and
    YouTube title, ``hashtags`` its hashtags, ``comment`` its first comment,
    ``search`` its search phrase."""
    if not post:
        return SCHEMA
    fields = {**POST_FIELDS, **(TAGS_FIELD if hashtags else {}), **(COMMENT_FIELD if comment else {}),
              **(SEARCH_FIELD if search else {})}
    item = SCHEMA["properties"]["ideas"]["items"]
    return {**SCHEMA, "properties": {"ideas": {"type": "ARRAY", "items": {
        **item, "properties": {**item["properties"], **fields},
        "required": [*item["required"], *fields]}}}}


def prompt(topic: str = "", count: int = 5, tone: str = "", avoid: list[str] | None = None,
           examples: list[str] | None = None, post: bool = False, *, length: str = "long",
           comment: bool = False, search: bool = False, phrases: list[str] | None = None,
           learnings: list[str] | None = None, replies: list[dict] | None = None, hashtags: bool = False,
           niche: str | None = None) -> str:
    """The brief. ``length``: a ``LENGTHS`` key. ``comment`` / ``search`` /
    ``hashtags`` ask for a first comment / a search phrase / hashtags per post
    (``phrases``: real searches to pick from). ``learnings``: how the account's reels did, best first.
    ``replies``: viewers' comments ({comment, platform, note}) to answer, one
    script each, first and in this order. ``niche``: what the account is
    about and how its scripts are shaped (``niches.NICHES``)."""
    lo, hi, spoken = LENGTHS.get(length, LENGTHS["long"])
    n = niches.get(niche)
    lines = [
        f"Write {count} new scripts for vertical short videos (Instagram Reels, YouTube Shorts, TikTok) on an account "
        f"of {n.about}.",
        "Each script is read by a calm narrator over stock footage, with one word on screen at a time.",
        "Rules for every script:",
        f"- {lo} to {hi} words ({spoken} read aloud), in the second person, plain spoken English, no lists, "
        "no emojis, no hashtags.",
        "- The first sentence is the hook: under 14 words, a question or a sharp claim that stops the scroll. "
        "It is shown along the skyline, so keep it short. Its first five words must work on their own: most "
        "people decide in the first two seconds whether to keep watching.",
        *(f"- {rule}" for rule in n.arc),
        "- Mark the 2 to 4 strongest words with *asterisks* (one word each).",
        "- Every script takes a different angle; none may repeat an idea below.",
        "Also give each a short title (under 8 words) and one sentence on why it should do well.",
    ]
    if post:
        lines += [
            "For each script also write the post that goes with it:",
            "- caption: 2 to 4 short lines with a blank line between them; the first line is a hook on its own; end "
            "with a question or a reason to save the post; no hashtags inside; at most 400 characters.",
            "- youtube_title: a hook title for the Short, at most 90 characters, no hashtags, no quotes.",
        ]
        if hashtags:
            lines.append("- hashtags: 5 to 8 that fit the video, without the # sign, mixing broad and niche ones.")
        if comment:
            lines.append("- first_comment: a question posted as the first comment, under 140 characters, that "
                         "viewers can answer from their own life in a few words (not a yes/no question, not "
                         "\"comment below\"), different from the caption's question.")
        if search:
            lines.append("- search_phrase: what someone would type into TikTok or YouTube search to find this "
                         "video, 2 to 6 words in lower case. Put it word for word in the caption's first or "
                         "second line and in youtube_title" + (", and make its key words one of the hashtags."
                                                                if hashtags else "."))
            if phrases:
                lines.append("Real searches people make on YouTube (use one word for word when it fits the script, "
                             "and let it shape the hook):")
                lines += [f"- {p}" for p in phrases[:60]]
    if replies:
        lines.append(f"The first {len(replies)} scripts answer real comments from viewers, one each, in this order. "
                     "The hook speaks to the comment (quote a few of its words), the script answers it honestly "
                     "and kindly, and the caption starts with \"Replying to a comment:\".")
        for r in replies:
            note = f" (idea: {r['note'].strip()[:300]})" if (r.get("note") or "").strip() else ""
            lines.append(f'- {r.get("platform") or "a"} viewer wrote: """{str(r.get("comment", "")).strip()[:500]}"""{note}')
    return "\n".join(lines + _context(learnings, topic, tone, examples, avoid))


def _context(learnings, topic, tone, examples, avoid) -> list[str]:
    """The brief's closing lines: what did well, the user's topic and tone, ideas already used."""
    lines = []
    if learnings:
        lines.append("How this account's recent reels did, best first. Write more like the top ones (their kind of "
                     "hook, angle and feeling, never their exact ideas) and less like the bottom ones:")
        lines += [f"- {x.strip()[:300]}" for x in learnings[:12] if x.strip()]
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
    return lines


def line_prompt(count: int = 5, *, niche: str | None = None, topic: str = "", tone: str = "",
                avoid: list[str] | None = None, comment: bool = False, search: bool = False,
                phrases: list[str] | None = None, learnings: list[str] | None = None, hashtags: bool = False) -> str:
    """The brief for comment videos: one line on screen for 7-10 s, no voice,
    whose only job is to get answers in the comments (``niches``)."""
    n = niches.get(niche)
    lines = [
        f"Write {count} new lines for 8-second vertical videos (Instagram Reels, YouTube Shorts, TikTok) on an account "
        f"of {n.about}.",
        "Each video is one stock clip with the line on screen, music and no voice. Its only job is to make people "
        "answer in the comments; the short video loops while they read the answers.",
        "Rules for every line:",
        "- Under 14 words, plain spoken English, no emojis, no hashtags.",
        "- An unfinished sentence that ends with a colon, or a direct question, that anyone can answer from their "
        "own life or opinion in a few words. Prefer what people answer in their own words over a plain yes or no.",
        "- Never ask for comments, likes or tags (\"comment below\", \"like if\", \"tag someone\").",
        "- Nothing hateful, sexual or political, and never a group of people as the target.",
        *(f"- {rule}" for rule in n.lines),
        "- Every line is different; none may repeat an idea below.",
        "Lines of the kind wanted (never reuse them):",
        *(f"- {x}" for x in n.examples),
        "For each line also write:",
        "- title: under 8 words.",
        "- caption: one short line (at most 150 characters) that nudges people to answer, like \"Be honest.\"; "
        "no hashtags.",
        "- youtube_title: the line itself, or close to it, at most 90 characters, no hashtags.",
    ]
    if comment:
        lines.append("- first_comment: the account's own short answer to the line, posted as the first comment to "
                     "start the conversation, under 140 characters.")
    if search:
        lines.append("- search_phrase: what someone would type into TikTok or YouTube search to find this video, "
                     "2 to 6 words in lower case. Put it word for word in the caption.")
        if phrases:
            lines.append("Real searches people make on YouTube (use one word for word when it fits):")
            lines += [f"- {p}" for p in phrases[:60]]
    if hashtags:
        lines.append("- hashtags: 5 to 8 that fit the video, without the # sign, mixing broad and niche ones.")
    return "\n".join(lines + _context(learnings, topic, tone, None, avoid))


def caption_lines(text: str) -> str:
    """A caption Gemini wrote as one paragraph gets a line per sentence, a blank line between them."""
    text = str(text or "").strip()
    if "\n" in text:
        return re.sub(r"\n{3,}", "\n\n", text)
    return "\n\n".join(p for p in re.split(r"(?<=[.?!…])\s+", text) if p)


def clean_tags(raw) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[\s,]+", str(raw or ""))
    tags = [re.sub(r"[^\w]", "", str(t).lstrip("#")) for t in items]
    return list(dict.fromkeys(t for t in tags if t))[:30]


def _ask(brief: str, response_schema: dict, session: requests.Session | None) -> tuple[str, list[dict]]:
    """(model, the answer's "ideas") for ``brief``: from the best model, or
    the next ones while Gemini says a model's quota is used up (HTTP 429) or
    it is overloaded (503)."""
    headers = _headers()
    session = session or requests.Session()
    body = {
        "contents": [{"role": "user", "parts": [{"text": brief}]}],
        "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json",
                             "responseSchema": response_schema},
    }
    models = _models(session, headers)[:QUOTA_TRIES]
    for i, model in enumerate(models):
        r = session.post(f"{API}/models/{model}:generateContent", headers=headers, json=body, timeout=180)
        if r.status_code not in BUSY or i + 1 == len(models):
            break
        log.info("Gemini %s is out of quota or busy (HTTP %s); trying %s", model, r.status_code, models[i + 1])
    _raise(r)
    data = r.json()
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        return model, json.loads(text)["ideas"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or (data.get("candidates") or [{}])[0].get("finishReason")
        raise IdeasError(f"Gemini's answer couldn't be read ({reason or exc}). Try again.") from exc


def _extras(item: dict, raw: dict, *, comment: bool, search: bool, hashtags: bool) -> None:
    """The post's first comment, search phrase and hashtags, cleaned, into ``item``."""
    item["hashtags"] = clean_tags(raw.get("hashtags")) if hashtags else []
    if comment:
        item["first_comment"] = re.sub(r"\s+", " ", str(raw.get("first_comment") or "")).strip().strip('"“”')
    if search:
        item["search_phrase"] = re.sub(r"[^\w' -]", "", str(raw.get("search_phrase") or "")).strip().lower()[:80]


def generate(topic: str = "", count: int = 5, tone: str = "", avoid: list[str] | None = None,
             examples: list[str] | None = None, session: requests.Session | None = None,
             post: bool = False, *, length: str = "long", comment: bool = False, search: bool = False,
             phrases: list[str] | None = None, learnings: list[str] | None = None,
             replies: list[dict] | None = None, hashtags: bool = False, niche: str | None = None) -> dict:
    """{model, ideas: [{title, script, why}]} from Gemini; with ``post`` each
    idea also has caption, hashtags (empty unless asked for) and youtube_title
    (and first_comment / search_phrase when asked for, see ``prompt``)."""
    count = max(1, min(int(count or 5), 10))
    brief = prompt(topic, count, tone, avoid, examples, post, length=length, comment=comment, search=search,
                   phrases=phrases, learnings=learnings, replies=replies, hashtags=hashtags, niche=niche)
    model, ideas = _ask(brief, schema(post, comment and post, search and post, hashtags and post), session)
    clean = []
    for i in ideas:
        script = re.sub(r"\s+", " ", str(i.get("script", ""))).strip()
        if not script:
            continue
        idea = {"title": str(i.get("title", "")).strip()[:80] or script.split(".")[0][:60],
                "script": script, "why": str(i.get("why", "")).strip()[:300]}
        if post:
            idea.update(caption=caption_lines(i.get("caption"))[:2200],
                        youtube_title=_title(i.get("youtube_title") or idea["title"]))
            _extras(idea, i, comment=comment, search=search, hashtags=hashtags)
        clean.append(idea)
    if not clean:
        raise IdeasError("Gemini returned no scripts. Try again.")
    return {"model": model, "ideas": clean[:count]}


def _title(raw) -> str:
    title = str(raw or "").strip().strip('"“”')
    return title if len(title) <= 100 else f"{title[:99].rstrip()}…"


def generate_lines(count: int = 5, *, niche: str | None = None, topic: str = "", tone: str = "",
                   avoid: list[str] | None = None, session: requests.Session | None = None, comment: bool = False,
                   search: bool = False, phrases: list[str] | None = None, learnings: list[str] | None = None,
                   hashtags: bool = False, **_ignored) -> dict:
    """{model, ideas: [{kind: "comment", title, script (the line), caption,
    youtube_title, hashtags, ...}]} for comment videos (``line_prompt``)."""
    count = max(1, min(int(count or 5), 10))
    brief = line_prompt(count, niche=niche, topic=topic, tone=tone, avoid=avoid, comment=comment, search=search,
                        phrases=phrases, learnings=learnings, hashtags=hashtags)
    fields = {**(TAGS_FIELD if hashtags else {}), **(COMMENT_FIELD if comment else {}),
              **(SEARCH_FIELD if search else {})}
    item = LINE_SCHEMA["properties"]["ideas"]["items"]
    response_schema = {**LINE_SCHEMA, "properties": {"ideas": {"type": "ARRAY", "items": {
        **item, "properties": {**item["properties"], **fields}, "required": [*item["required"], *fields]}}}}
    model, ideas = _ask(brief, response_schema, session)
    clean = []
    for i in ideas:
        line = re.sub(r"\s+", " ", str(i.get("line", ""))).strip().strip('"“”')
        if not line:
            continue
        idea = {"kind": "comment", "title": str(i.get("title", "")).strip()[:80] or line[:60], "script": line, "why": "",
                "caption": re.sub(r"\s+", " ", str(i.get("caption") or "")).strip()[:300] or line,
                "youtube_title": _title(i.get("youtube_title") or line)}
        _extras(idea, i, comment=comment, search=search, hashtags=hashtags)
        clean.append(idea)
    if not clean:
        raise IdeasError("Gemini returned no lines. Try again.")
    return {"model": model, "ideas": clean[:count]}
