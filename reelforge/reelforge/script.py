"""Script parsing: words, sentences, emphasis words and footage search queries.

Markup (optional) in the script overrides auto-detection:
    *word* or *several words*   -> highlight colour (yellow)
    **word**                    -> alert colour (red)
The asterisks are stripped before the text is sent to the voice model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import lexicon

_MARK = re.compile(r"(\*\*|\*)(.+?)\1", re.S)
_TRAIL = r"[\"'”’)\]]*$"
_SENTENCE_END = re.compile(r"[.!?…]+" + _TRAIL)
_CLAUSE_END = re.compile(r"([,;:]|—|–|-{2}|\.\.\.)" + _TRAIL)
_DASHES = {"—", "–", "-", "--"}


def normalize(token: str) -> str:
    """Lower-case, drop punctuation and apostrophes: "I'm," -> "im"."""
    return re.sub(r"[^a-z0-9]", "", token.lower())


@dataclass
class Word:
    text: str                 # as written in the script (punctuation kept, markup removed)
    norm: str                 # normalised form used for matching
    index: int
    sentence: int
    ends_sentence: bool = False
    ends_clause: bool = False
    emphasis: int = 0         # 0 none, 1 highlight, 2 alert
    priority: float = 0.0     # highest-priority group wins when a caption has several
    group: int | None = None  # emphasis group id; a group is kept on one caption
    forced: bool = False      # chosen by markup, never demoted
    start: float = 0.0
    end: float = 0.0


@dataclass
class Script:
    raw: str
    text: str                 # clean text for the voice model
    words: list[Word]

    @property
    def sentence_count(self) -> int:
        return self.words[-1].sentence + 1 if self.words else 0

    def keywords(self) -> list[str]:
        seen: list[str] = []
        for w in self.words:
            if w.norm and w.norm not in _STOP and w.norm not in seen:
                seen.append(w.norm)
        return seen


_STOP = {normalize(w) for w in lexicon.STOPWORDS}


def _strip_markup(raw: str) -> tuple[str, list[tuple[int, int, int]]]:
    out: list[str] = []
    spans: list[tuple[int, int, int]] = []
    pos = 0
    length = 0
    for m in _MARK.finditer(raw):
        before = raw[pos:m.start()]
        out.append(before)
        length += len(before)
        inner = m.group(2)
        spans.append((length, length + len(inner), 2 if m.group(1) == "**" else 1))
        out.append(inner)
        length += len(inner)
        pos = m.end()
    out.append(raw[pos:])
    return "".join(out), spans


def _phrase_pattern(phrase: str) -> list[tuple[str, bool]]:
    parts = phrase.split()
    starred = [p.startswith("*") and p.endswith("*") for p in parts]
    if not any(starred):
        starred = [True] * len(parts)
    return [(normalize(p), s) for p, s in zip(parts, starred)]


_PHRASES = sorted(
    ((_phrase_pattern(p), lvl) for p, lvl in lexicon.EMPHASIS_PHRASES.items()),
    key=lambda item: -len(item[0]),
)
_EMPHASIS_WORDS = {normalize(w): lvl for w, lvl in lexicon.EMPHASIS_WORDS.items()}


def parse_script(raw: str, auto_emphasis: bool = True) -> Script:
    text, spans = _strip_markup(re.sub(r"\s+", " ", raw.strip()))

    words: list[Word] = []
    sentence = 0
    for m in re.finditer(r"\S+", text):
        tok = m.group()
        if tok in _DASHES or not normalize(tok):
            if words:  # a free-standing dash or ellipsis is a pause after the previous word
                words[-1].ends_clause = True
            continue
        if words and words[-1].ends_sentence:
            sentence += 1
        w = Word(text=tok, norm=normalize(tok), index=len(words), sentence=sentence,
                 ends_sentence=bool(_SENTENCE_END.search(tok)),
                 ends_clause=bool(_CLAUSE_END.search(tok)))
        for span_id, (start, end, level) in enumerate(spans):
            if m.start() < end and m.end() > start:
                w.emphasis, w.forced, w.priority = level, True, 100.0
                w.group = -1 - span_id
        words.append(w)

    script = Script(raw=raw, text=text, words=words)
    if auto_emphasis:
        _auto_emphasis(script)
    return script


def _auto_emphasis(script: Script) -> None:
    words = script.words
    next_group = 0
    for pattern, level in _PHRASES:
        n = len(pattern)
        for i in range(len(words) - n + 1):
            window = words[i:i + n]
            if any(w.group is not None for w in window):
                continue
            if any(w.ends_sentence for w in window[:-1]):
                continue  # never match across a sentence boundary
            if all(w.norm == p for w, (p, _) in zip(window, pattern)):
                for w, (_, coloured) in zip(window, pattern):
                    w.group = next_group
                    w.priority = 10.0 + 2 * level + n
                    if coloured:
                        w.emphasis = level
                next_group += 1
    for w in words:
        if w.group is None and w.norm in _EMPHASIS_WORDS:
            level = _EMPHASIS_WORDS[w.norm]
            w.group, w.emphasis = next_group, level
            w.priority = 2.0 * level + (1.0 if w.ends_sentence else 0.0)
            next_group += 1


def footage_queries(script: Script, count: int, palette: str = "bright") -> list[str]:
    """Stock-footage searches: the palette's script concepts (in story order)
    interleaved with its atmosphere searches, de-duplicated, ``count`` long
    (shorter only if the palette has fewer distinct searches). Palettes live
    in ``lexicon.PALETTES``: "bright" (sunny nature, the reference look) and
    "moody" (night, rain)."""
    try:
        concept_map, fill = lexicon.PALETTES[palette]
    except KeyError:
        raise ValueError(f"unknown footage palette {palette!r}: "
                         f"use one of {', '.join(lexicon.PALETTES)}") from None
    concepts: list[list[str]] = []
    used: set[int] = set()
    for w in script.words:
        for i, (triggers, queries) in enumerate(concept_map):
            if i not in used and w.norm in triggers:
                used.add(i)
                concepts.append(queries)

    ordered: list[str] = []
    atmosphere = iter(fill)
    for queries in concepts:
        ordered.append(queries[0])
        ordered.append(next(atmosphere, queries[-1]))
    for queries in concepts:
        ordered.extend(queries[1:])
    ordered.extend(atmosphere)
    ordered.extend(fill)  # wrap around if still short

    result: list[str] = []
    for q in ordered:
        if q not in result:
            result.append(q)
        if len(result) >= count:
            break
    return result
