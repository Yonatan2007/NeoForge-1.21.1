import numpy as np

from reelforge import alignment
from reelforge.alignment import AsrWord, align, estimate, speech_regions
from reelforge.script import normalize, parse_script


def asr(*items):
    return [AsrWord(t, normalize(t), s, e) for t, s, e in items]


def assert_monotonic(words, duration):
    for a, b in zip(words, words[1:]):
        assert a.start <= b.start
    for w in words:
        assert 0 <= w.start <= w.end <= duration


def test_exact_and_misheard_words():
    s = parse_script("The I'm proud of you. The I'm sorry.")
    heard = asr(("The", 0.0, 0.1), ("aim", 0.1, 0.3), ("proud", 0.3, 0.6), ("of", 0.6, 0.7),
                ("you.", 0.7, 1.0), ("Dan,", 1.6, 1.7), ("sorry.", 1.9, 2.3))
    score = align(s.words, heard, duration=2.5)
    assert score == 5 / 8  # the, proud, of, you, sorry
    by = {i: w for i, w in enumerate(s.words)}
    assert (by[1].start, by[2].start) == (0.1, 0.3)  # "I'm" took "aim"'s timing
    # "The I'm" (2 words) <- "Dan," (1 word): spread over Dan's span by length
    assert 1.6 <= by[5].start < by[6].start < 1.7
    assert by[7].start == 1.9
    assert_monotonic(s.words, 2.5)


def test_unheard_sentence_start_hugs_next_word():
    s = parse_script("Stop. Say it today.")
    heard = asr(("Stop.", 0.0, 0.4), ("it", 2.1, 2.2), ("today.", 2.2, 2.6))
    align(s.words, heard, duration=3.0)
    say = s.words[1]
    # "Say" starts a sentence, so it sits right before "it", not after "Stop."
    assert 1.6 < say.start < 2.1 and say.end <= 2.1 + 1e-9
    assert_monotonic(s.words, 3.0)


def test_speech_regions_finds_bursts():
    sr = 16000
    t = np.arange(int(sr * 3.0)) / sr
    x = 0.001 * np.random.default_rng(0).standard_normal(t.size)
    for a, b in [(0.2, 0.9), (1.5, 2.4)]:
        m = (t >= a) & (t < b)
        x[m] += 0.3 * np.sin(2 * np.pi * 220 * t[m])
    regions = speech_regions(x.astype(np.float32), sr)
    assert len(regions) == 2
    (a0, b0), (a1, b1) = regions
    assert abs(a0 - 0.2) < 0.05 and abs(b0 - 0.9) < 0.05
    assert abs(a1 - 1.5) < 0.05 and abs(b1 - 2.4) < 0.05


def test_estimate_maps_sentences_to_regions():
    s = parse_script("A short one. Then a much longer second sentence follows here.")
    regions = [(0.0, 0.8), (1.2, 1.5), (1.6, 4.0)]
    estimate(s.words, regions, duration=4.2)
    first = [w for w in s.words if w.sentence == 0]
    second = [w for w in s.words if w.sentence == 1]
    assert first[-1].end <= 0.8 + 1e-6
    assert second[0].start >= 1.2 - 1e-6
    assert_monotonic(s.words, 4.2)


def test_estimate_allows_two_sentences_in_one_region():
    # "Go. Now." is read without a pause; the long sentence follows a pause.
    s = parse_script("Go. Now. Run away from here today.")
    estimate(s.words, [(0.0, 1.0), (1.5, 4.0)], duration=4.2)
    assert s.words[1].end <= 1.0 + 1e-6      # "Now." stays in the first region
    assert s.words[2].start >= 1.5 - 1e-6    # "Run" starts after the pause
    assert_monotonic(s.words, 4.2)


def test_zero_length_word_after_pause_is_re_estimated():
    s = parse_script("I'm sorry. Nobody gets a warning.")
    heard = asr(("I'm", 0.0, 0.2), ("sorry.", 0.2, 0.6), ("Nobody", 2.0, 2.0), ("gets", 2.0, 2.3),
                ("a", 2.3, 2.4), ("warning.", 2.4, 2.9))
    align(s.words, heard, duration=3.0)
    nobody, gets = s.words[2], s.words[3]
    assert 0.6 <= nobody.start < 2.0 - 0.1   # pulled back into the pause
    assert nobody.end <= gets.start + 1e-9 and gets.start == 2.0


def test_auto_falls_back_to_estimation_when_whisper_cannot_load(monkeypatch):
    def offline(*_args, **_kw):
        raise OSError("model not cached and the machine is offline")

    monkeypatch.setattr(alignment, "transcribe", offline)
    s = parse_script("Say it today. Just say it.")
    sr = alignment.SAMPLE_RATE
    samples = np.zeros(sr * 3, np.float32)
    samples[int(0.2 * sr):int(1.2 * sr)] = 0.3
    samples[int(1.6 * sr):int(2.6 * sr)] = 0.3
    assert alignment.time_words(s, samples, "auto") == "estimate"
    assert all(w.end > w.start for w in s.words)
    import pytest
    with pytest.raises(OSError):
        alignment.time_words(s, samples, "whisper")
