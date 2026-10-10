"""Fitting a reel to a target length, and caption timing without a voice.

Speech is never cut. A target length is reached, in this order, by
1. changing the voice tempo within [min_tempo, max_tempo] (only when the
   voice is too LONG: slowing a narrator down sounds wrong sooner than
   speeding up does),
2. a longer opening (music and picture before the first word) up to
   ``max_intro`` seconds,
3. a longer ending (picture and music after the last word).
If the voice still does not fit at the fastest allowed tempo, the video
runs longer than the target and a warning says by how much.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import DurationSettings
from .script import Word

MIN_OUTRO = 0.5  # always leave the last word a moment before the video ends


@dataclass
class TimingPlan:
    tempo: float          # voice speed factor (1 = unchanged, 1.1 = 10 % faster)
    intro: float          # seconds before the first word
    outro: float          # seconds after the last word
    total: float          # video length
    warnings: list[str] = field(default_factory=list)


def fit_duration(speech: float, ds: DurationSettings, voice_delay: float, tail: float) -> TimingPlan:
    """Plan tempo/intro/outro so ``intro + speech / tempo + outro == target``.

    ``speech`` is the voiced span in seconds (first word start to last word
    end) at the original tempo."""
    natural = voice_delay + speech + tail
    if not ds.target or ds.target <= 0:
        return TimingPlan(1.0, voice_delay, tail, natural)

    target = float(ds.target)
    warnings: list[str] = []
    outro_min = min(tail, MIN_OUTRO) if tail > 0 else 0.0
    room = target - voice_delay - outro_min          # time available for speech
    if speech > room:                                  # too long: speed the voice up
        tempo = min(ds.max_tempo, speech / max(room, 1e-6))
        spoken = speech / tempo
        total = voice_delay + spoken + outro_min
        if total > target + 0.05:
            warnings.append(
                f"The voiceover needs {total:.1f} s even at {tempo:.2f}x speed, "
                f"{total - target:.1f} s more than the {target:.0f} s target. "
                "Shorten the script or allow a faster tempo.")
            return TimingPlan(tempo, voice_delay, outro_min, total, warnings)
        return TimingPlan(tempo, voice_delay, target - voice_delay - spoken, target, warnings)

    extra = target - natural                           # too short: pad the opening, then the end
    if extra < 0:                                      # fits once the ending is shortened
        return TimingPlan(1.0, voice_delay, target - voice_delay - speech, target)
    intro = voice_delay + min(extra * 0.4, max(0.0, ds.max_intro - voice_delay))
    outro = target - intro - speech
    if outro - tail > 6.0:
        warnings.append(
            f"The voiceover is {speech:.1f} s, so the last {outro:.0f} s are picture and music "
            "only. Add script or choose a shorter length if that is not intended.")
    return TimingPlan(1.0, intro, outro, target, warnings)


def synthetic_timings(words: list[Word], start: float, end: float,
                      sentence_pause: float = 0.6, clause_pause: float = 0.25) -> None:
    """Spread ``words`` over [start, end] for a reel without a voiceover.

    Each word gets time proportional to its length (+2 so short words still
    register); sentence and clause ends add a reading pause. The whole layout
    is then scaled to fill the span exactly."""
    if not words:
        return
    weights = np.array([len(w.norm) + 2.0 for w in words])
    pauses = np.array([sentence_pause if w.ends_sentence else clause_pause if w.ends_clause else 0.0
                       for w in words])
    pauses[-1] = 0.0
    span = max(end - start, 0.1)
    # word time per weight unit k so that k*sum(weights) + sum(pauses) = span
    k = max(span - pauses.sum(), span * 0.5) / weights.sum()
    scale = span / (k * weights.sum() + pauses.sum())
    t = start
    for w, wt, p in zip(words, weights, pauses):
        dur = k * wt * scale
        w.start, w.end = t, t + dur
        t += dur + p * scale


def reading_seconds(words: list[Word], words_per_second: float) -> float:
    """Natural on-screen time for a reel with no voiceover."""
    sentences = sum(1 for w in words if w.ends_sentence)
    return len(words) / max(words_per_second, 0.5) + 0.6 * sentences
