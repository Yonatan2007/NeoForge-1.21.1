"""Phrases people really search for, from YouTube's search suggestions.

TikTok and YouTube are search engines too: a reel whose caption and title
carry the words someone types ("how to stop overthinking") is found long
after it leaves the feed. Autopilot asks YouTube's autocomplete (the public
endpoint behind its search box) about a few openings that fit the account,
and Gemini builds each post around one of the answers (``ideas.prompt``).
"""
from __future__ import annotations

import re

import requests

SUGGEST = "https://suggestqueries.google.com/complete/search"
# openings of searches this account's audience makes
SEEDS = ("how to stop overthinking", "how to let go of", "how to move on from", "why do i feel",
         "how to stop people pleasing", "how to heal from", "how to stop waiting", "how to love yourself",
         "why do i push people away", "how to deal with loneliness", "signs you are", "how to stop caring what")
# answers about other worlds than this account's (sports, faith, games, books, songs)
OFF_TOPIC = re.compile(r"\b(basketball|football|soccer|golf|tennis|baseball|chess|poker|islam|muslim|christian|"
                       r"bible|quran|god|jesus|book|pdf|minecraft|roblox|fortnite|lyrics|song|chords|karaoke|"
                       r"meme|reddit|quiz|test|ted talk|hindi|urdu|tamil|telugu|spanish|in arabic)\b")


def suggestions(query: str, session: requests.Session | None = None, timeout: float = 10) -> list[str]:
    """YouTube's search suggestions for ``query`` ([] when it can't be reached)."""
    s = session or requests.Session()
    try:
        r = s.get(SUGGEST, params={"client": "firefox", "ds": "yt", "hl": "en", "q": query}, timeout=timeout)
        if r.status_code >= 400:
            return []
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    return [str(x).strip() for x in (data[1] if isinstance(data, list) and len(data) > 1 else []) if str(x).strip()]


def seeds_for(topic: str = "") -> list[str]:
    """The searches to ask about: the user's topics first ("letting go, self-worth"), then the defaults."""
    own = [t.strip().lower() for t in re.split(r"[,;\n]+", topic or "") if 2 < len(t.strip()) < 60]
    return list(dict.fromkeys([*own, *SEEDS]))


def phrases(topic: str = "", session: requests.Session | None = None, limit: int = 60,
            seeds: list[str] | None = None) -> list[str]:
    """Real searches that fit the account, interleaved across seeds so every
    angle is represented, without the off-topic ones and duplicates."""
    s = session or requests.Session()
    lists = [suggestions(q, s) for q in (seeds or seeds_for(topic))]
    out: list[str] = []
    for row in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if row < len(x):
                p = re.sub(r"\s+", " ", x[row].lower())
                if 2 <= len(p.split()) <= 9 and not OFF_TOPIC.search(p) and p not in out:
                    out.append(p)
    return out[:limit]
