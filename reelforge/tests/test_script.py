import pytest

from reelforge import lexicon
from reelforge.script import footage_queries, normalize, parse_script

EXAMPLE = ("You've already had your last conversation with someone. You just don't know which one. "
           "So stop saving it. The thank you. The I'm proud of you. The I'm sorry. Nobody gets a "
           "warning before the last time. Say it today. Say it badly, if you have to. Just say it.")


def test_normalize():
    assert normalize("I'm,") == "im"
    assert normalize("You’ve") == "youve"
    assert normalize("—") == ""


def test_words_sentences_and_clauses():
    s = parse_script(EXAMPLE)
    assert len(s.words) == 50
    assert s.sentence_count == 10
    badly = next(w for w in s.words if w.norm == "badly")
    assert badly.ends_clause and not badly.ends_sentence
    assert s.words[-1].ends_sentence


def test_auto_emphasis_phrases_and_words():
    s = parse_script(EXAMPLE)
    coloured = {(w.text, w.emphasis) for w in s.words if w.emphasis}
    assert ("last", 2) in coloured and ("conversation", 2) in coloured
    assert ("thank", 1) in coloured and ("you.", 1) in coloured
    assert ("proud", 1) in coloured
    # only "proud" is coloured inside "I'm proud of you", but the phrase is one group
    proud = next(w for w in s.words if w.norm == "proud")
    phrase = [w for w in s.words if w.group == proud.group]
    assert [w.norm for w in phrase] == ["im", "proud", "of", "you"]
    assert [w.emphasis for w in phrase] == [0, 1, 0, 0]
    assert ("Nobody", 2) in coloured


def test_markup_overrides_and_is_stripped():
    s = parse_script("Say it *today*. **Never** wait.", auto_emphasis=False)
    assert s.text == "Say it today. Never wait."
    today, never = s.words[2], s.words[3]
    assert (today.emphasis, today.forced) == (1, True)
    assert (never.emphasis, never.forced) == (2, True)
    assert all(w.emphasis == 0 for w in (s.words[0], s.words[1], s.words[4]))


def test_multiword_markup_and_free_dash():
    s = parse_script("I waited — *far too long* for this.")
    assert [w.text for w in s.words][:2] == ["I", "waited"]
    assert s.words[1].ends_clause  # the free-standing dash marks a pause
    marked = [w.text for w in s.words if w.forced]
    assert marked == ["far", "too", "long"]


# Searches whose vertical Mixkit results were read (count on the first page,
# 24 max, and what the clips show) when the bright palette was written,
# 2026-10-08. Mixkit matches loosely, so a new bright search must be checked
# the same way and added here: e.g. "lake" (23 clips) shows city reflections,
# "field" (12) soccer, "sunset mountains" (3) a DJ in a desert.
MIXKIT_ON_LOOK = {
    "mountains": 24, "forest": 24, "hills": 11, "sky clouds": 24, "clouds": 24, "trees": 24,
    "beach": 20, "sea": 17, "sunlight": 16, "sunset": 24, "woman nature": 21, "road trip": 10,
    "hiking": 5, "waterfall": 4, "camping": 4,
}


def test_moody_footage_queries_follow_story_and_are_unique():
    s = parse_script(EXAMPLE)
    q = footage_queries(s, 8, palette="moody")
    assert len(q) == 8 and len(set(q)) == 8
    assert q[:4] == ["clock ticking dark", "night city drive",  # "last" is the first visual concept,
                     "phone screen dark night", "rain window night"]  # then the atmosphere interleaves
    assert footage_queries(parse_script("Hello."), 3, palette="moody") == lexicon.ATMOSPHERE[:3]


def test_bright_footage_queries_are_the_default_and_follow_story():
    s = parse_script(EXAMPLE)
    q = footage_queries(s, 8)
    assert q == footage_queries(s, 8, palette="bright")
    assert len(q) == 8 and len(set(q)) == 8
    assert q[:4] == ["clouds", "mountains", "beach", "forest"]  # last, (fill), conversation, (fill)
    hook = parse_script("If you lost your memory, who would you trust? Not who would show up.")
    assert footage_queries(hook, 3) == ["sky clouds", "mountains", "beach"]  # lost, (fill), memory
    assert footage_queries(parse_script("Hello."), 3) == lexicon.BRIGHT_ATMOSPHERE[:3]
    everything = footage_queries(s, 100)
    assert len(everything) == len(set(everything)) and set(lexicon.BRIGHT_ATMOSPHERE) <= set(everything)


def test_unknown_palette_is_explained():
    with pytest.raises(ValueError, match="bright, moody"):
        footage_queries(parse_script("Hello."), 3, palette="neon")


def test_bright_lexicon_only_uses_short_checked_searches():
    queries = set(lexicon.BRIGHT_ATMOSPHERE)
    for triggers, searches in lexicon.BRIGHT_CONCEPTS:
        queries.update(searches)
        assert triggers == {normalize(t) for t in triggers}  # matched against normalised words
    assert queries <= set(MIXKIT_ON_LOOK)
    assert all(len(q.split()) <= 2 for q in queries)
    words = [w for triggers, _ in lexicon.BRIGHT_CONCEPTS for w in triggers]
    assert len(words) == len(set(words))  # each word picks one concept
