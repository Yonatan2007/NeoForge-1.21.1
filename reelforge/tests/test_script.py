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


def test_footage_queries_follow_story_and_are_unique():
    s = parse_script(EXAMPLE)
    q = footage_queries(s, 8)
    assert len(q) == 8 and len(set(q)) == 8
    assert q[0] == "clock ticking dark"  # "last" is the first visual concept
    assert footage_queries(parse_script("Hello."), 3)  # falls back to atmosphere
