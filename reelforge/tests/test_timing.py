import pytest

from reelforge.config import DurationSettings
from reelforge.script import parse_script
from reelforge.timing import fit_duration, reading_seconds, synthetic_timings


def test_no_target_keeps_natural_length():
    p = fit_duration(20.0, DurationSettings(), voice_delay=0.1, tail=0.8)
    assert (p.tempo, p.intro, p.outro, p.total) == (1.0, 0.1, 0.8, pytest.approx(20.9))


def test_long_voice_is_sped_up_within_limits():
    p = fit_duration(30.0, DurationSettings(target=28.0), voice_delay=0.1, tail=0.8)
    assert 1.0 < p.tempo <= 1.12 and p.total == pytest.approx(28.0)
    assert p.intro + 30.0 / p.tempo + p.outro == pytest.approx(28.0)
    assert not p.warnings


def test_too_long_even_at_max_tempo_warns_and_never_cuts_speech():
    p = fit_duration(40.0, DurationSettings(target=20.0), voice_delay=0.1, tail=0.8)
    assert p.tempo == pytest.approx(1.12) and p.total > 20.0 and p.warnings
    assert p.intro + 40.0 / p.tempo + p.outro == pytest.approx(p.total)


def test_short_voice_gets_intro_then_outro():
    p = fit_duration(10.0, DurationSettings(target=15.0, max_intro=2.0), voice_delay=0.1, tail=0.8)
    assert p.tempo == 1.0 and p.total == 15.0
    assert 0.1 < p.intro <= 2.0 and p.outro > 0.8
    assert p.intro + 10.0 + p.outro == pytest.approx(15.0)


def test_slightly_long_fits_by_trimming_the_ending():
    p = fit_duration(19.2, DurationSettings(target=20.0), voice_delay=0.1, tail=0.8)
    assert p.tempo == 1.0 and p.outro == pytest.approx(0.7) and p.total == 20.0


def test_synthetic_timings_fill_span_in_order():
    s = parse_script("No voice here. Just music and words, nothing else.")
    synthetic_timings(s.words, 1.0, 9.0)
    assert s.words[0].start == pytest.approx(1.0)
    assert s.words[-1].end == pytest.approx(9.0)
    for a, b in zip(s.words, s.words[1:]):
        assert a.end <= b.start + 1e-9
    gap = s.words[3].start - s.words[2].end  # sentence pause after "here."
    assert gap > 0.2
    assert reading_seconds(s.words, 2.6) > len(s.words) / 2.6
