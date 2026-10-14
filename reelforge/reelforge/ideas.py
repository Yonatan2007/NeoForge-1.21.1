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
POST_FIELDS = {"caption": {"type": "STRING"}, "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
               "youtube_title": {"type": "STRING"}}
COMMENT_FIELD = {"first_comment": {"type": "STRING"}}
SEARCH_FIELD = {"search_phrase": {"type": "STRING"}}
# words per script and how long that reads (the narrator speaks ~2.9 words a second)
LENGTHS = {"short": (60, 85, "about 20 to 30 seconds"), "medium": (85, 115, "about 30 to 40 seconds"),
           "long": (110, 160, "about 40 to 55 seconds")}


def schema(post: bool = False, comment: bool = False, search: bool = False) -> dict:
    """The answer's JSON schema; ``post`` adds each reel's caption, hashtags
    and YouTube title, ``comment`` its first comment, ``search`` its search phrase."""
    if not post:
        return SCHEMA
    fields = {**POST_FIELDS, **(COMMENT_FIELD if comment else {}), **(SEARCH_FIELD if search else {})}
    item = SCHEMA["properties"]["ideas"]["items"]
    return {**SCHEMA, "properties": {"ideas": {"type": "ARRAY", "items": {
        **item, "properties": {**item["properties"], **fields},
        "required": [*item["required"], *fields]}}}}


def prompt(topic: str = "", count: int = 5, tone: str = "", avoid: list[str] | None = None,
           examples: list[str] | None = None, post: bool = False, *, length: str = "long",
           comment: bool = False, search: bool = False, phrases: list[str] | None = None,
           learnings: list[str] | None = None, replies: list[dict] | None = None) -> str:
    """The brief. ``length``: a ``LENGTHS`` key. ``comment`` / ``search`` ask
    for a first comment / a search phrase per post (``phrases``: real searches
    to pick from). ``learnings``: how the account's reels did, best first.
    ``replies``: viewers' comments ({comment, platform, note}) to answer, one
    script each, first and in this order."""
    lo, hi, spoken = LENGTHS.get(length, LENGTHS["long"])
    lines = [
        f"Write {count} new scripts for vertical short videos (Instagram Reels, YouTube Shorts, TikTok) on an account "
        "of late-night philosophical thoughts about love, loss, self-worth and becoming who you are.",
        "Each script is read by a calm narrator over moody stock footage, with one word on screen at a time.",
        "Rules for every script:",
        f"- {lo} to {hi} words ({spoken} read aloud), in the second person, plain spoken English, no lists, "
        "no emojis, no hashtags.",
        "- The first sentence is the hook: under 14 words, a question or a sharp claim that stops the scroll. "
        "It is shown along the skyline, so keep it short. Its first five words must work on their own: most "
        "people decide in the first two seconds whether to keep watching.",
        "- Build from the hook to an uncomfortable truth, then turn to something hopeful and end on a line "
        "that echoes the hook.",
        "- Mark the 2 to 4 strongest words with *asterisks* (one word each).",
        "- Every script takes a different angle; none may repeat an idea below.",
        "Also give each a short title (under 8 words) and one sentence on why it should do well.",
    ]
    if post:
        lines += [
            "For each script also write the post that goes with it:",
            "- caption: 2 to 4 short lines with a blank line between them; the first line is a hook on its own; end "
            "with a question or a reason to save the post; no hashtags inside; at most 400 characters.",
            "- hashtags: 5 to 8 that fit the video, without the # sign, mixing broad and niche ones.",
            "- youtube_title: a hook title for the Short, at most 90 characters, no hashtags, no quotes.",
        ]
        if comment:
            lines.append("- first_comment: a question posted as the first comment, under 140 characters, that "
                         "viewers can answer from their own life in a few words (not a yes/no question, not "
                         "\"comment below\"), different from the caption's question.")
        if search:
            lines.append("- search_phrase: what someone would type into TikTok or YouTube search to find this "
                         "video, 2 to 6 words in lower case. Put it word for word in the caption's first or "
                         "second line and in youtube_title, and make its key words one of the hashtags.")
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
    return "\n".join(lines)


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


def generate(topic: str = "", count: int = 5, tone: str = "", avoid: list[str] | None = None,
             examples: list[str] | None = None, session: requests.Session | None = None,
             post: bool = False, *, length: str = "long", comment: bool = False, search: bool = False,
             phrases: list[str] | None = None, learnings: list[str] | None = None,
             replies: list[dict] | None = None) -> dict:
    """{model, ideas: [{title, script, why}]} from Gemini; with ``post`` each
    idea also has caption, hashtags and youtube_title (and first_comment /
    search_phrase when asked for, see ``prompt``)."""
    count = max(1, min(int(count or 5), 10))
    headers = _headers()
    session = session or requests.Session()
    model = _model(session, headers)
    brief = prompt(topic, count, tone, avoid, examples, post, length=length, comment=comment, search=search,
                   phrases=phrases, learnings=learnings, replies=replies)
    body = {
        "contents": [{"role": "user", "parts": [{"text": brief}]}],
        "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json",
                             "responseSchema": schema(post, comment and post, search and post)},
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
        if not script:
            continue
        idea = {"title": str(i.get("title", "")).strip()[:80] or script.split(".")[0][:60],
                "script": script, "why": str(i.get("why", "")).strip()[:300]}
        if post:
            title = str(i.get("youtube_title") or idea["title"]).strip().strip('"“”')
            idea.update(caption=caption_lines(i.get("caption"))[:2200], hashtags=clean_tags(i.get("hashtags")),
                        youtube_title=title if len(title) <= 100 else f"{title[:99].rstrip()}…")
            if comment:
                idea["first_comment"] = re.sub(r"\s+", " ", str(i.get("first_comment") or "")).strip().strip('"“”')
            if search:
                idea["search_phrase"] = re.sub(r"[^\w' -]", "", str(i.get("search_phrase") or "")).strip().lower()[:80]
        clean.append(idea)
    if not clean:
        raise IdeasError("Gemini returned no scripts. Try again.")
    return {"model": model, "ideas": clean[:count]}
