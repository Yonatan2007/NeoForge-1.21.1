"""Word timing: when is each script word spoken in the voiceover?

Primary path: faster-whisper word timestamps, then the *script* is aligned to
the transcript. Whisper mishears stylised lines ("The I'm sorry" -> "Dan,
sorry"), so captions always show the script's words and only borrow Whisper's
clock.

Fallback path (no Whisper installed): energy-based voice activity detection
plus a dynamic-programming fit of sentences to speech regions.
"""
from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass

import numpy as np

from .script import Script, Word, normalize

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
MIN_WORD = 0.08  # seconds


@dataclass
class AsrWord:
    text: str
    norm: str
    start: float
    end: float


def _weight(w: Word) -> float:
    """Relative speaking time of a word: roughly its length, plus a constant
    so that "a" and "I" still take some time."""
    return len(w.norm) + 2.0


# --------------------------------------------------------------------------- whisper

def transcribe(samples: np.ndarray, model_name: str = "base.en", device: str = "cpu") -> list[AsrWord]:
    from faster_whisper import WhisperModel  # optional dependency

    model = WhisperModel(model_name, device=device, compute_type="int8")
    # No initial_prompt: feeding the script as a prompt makes Whisper treat it
    # as already-heard text and skip or hallucinate. Alignment fixes wording.
    segments, _ = model.transcribe(samples, word_timestamps=True,
                                   language="en" if model_name.endswith(".en") else None)
    words = []
    for seg in segments:
        for w in seg.words or []:
            norm = normalize(w.word)
            if norm:
                words.append(AsrWord(w.word.strip(), norm, float(w.start), float(w.end)))
    return words


def align(words: list[Word], asr: list[AsrWord], duration: float) -> float:
    """Copy ASR timings onto script words. Returns the fraction of script
    words matched exactly (a confidence score)."""
    times: list[tuple[float, float] | None] = [None] * len(words)
    matcher = difflib.SequenceMatcher(None, [w.norm for w in words], [a.norm for a in asr],
                                      autojunk=False)
    matched = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (asr[j1 + k].start, asr[j1 + k].end)
            matched += i2 - i1
        elif tag == "replace" and i2 - i1 == j2 - j1:
            # Same number of words, different spelling ("I'm" heard as "aim").
            for k in range(i2 - i1):
                times[i1 + k] = (asr[j1 + k].start, asr[j1 + k].end)
        elif tag == "replace":
            # Different counts ("The I'm" heard as "Dan,"): share the heard
            # span between the script words in proportion to their length.
            _spread(words[i1:i2], times, i1, asr[j1].start, asr[j2 - 1].end)
        # "delete" (never heard) is interpolated below; "insert" is ignored.

    # Whisper sometimes returns zero-length words right after a pause (start
    # == end == next word's start). Re-estimate those like unheard words.
    for k, t in enumerate(times):
        if t is not None and t[1] - t[0] < 0.02:
            times[k] = None
    _fill_gaps(words, times, duration)
    _finalise(words, times, duration)
    return matched / max(1, len(words))


def _spread(chunk: list[Word], times: list, offset: int, lo: float, hi: float) -> None:
    weights = np.array([_weight(w) for w in chunk])
    edges = lo + (hi - lo) * np.concatenate([[0.0], np.cumsum(weights) / weights.sum()])
    for k in range(len(chunk)):
        times[offset + k] = (float(edges[k]), float(edges[k + 1]))


def _fill_gaps(words: list[Word], times: list, duration: float) -> None:
    known = [(t[1] - t[0]) / _weight(w) for w, t in zip(words, times) if t and t[1] > t[0]]
    sec_per_unit = float(np.median(known)) if known else 0.06
    i = 0
    while i < len(words):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(words) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else 0.0
        hi = times[j][0] if j < len(words) else duration
        need = min(max(0.0, hi - lo), sec_per_unit * sum(_weight(w) for w in words[i:j]))
        # Words that open a sentence sit just before the next heard word (the
        # gap is the pause); words that close one hug the previous word.
        starts_sentence = i == 0 or words[i - 1].ends_sentence
        a, b = (hi - need, hi) if starts_sentence else (lo, lo + need)
        _spread(words[i:j], times, i, a, b)
        i = j


def _finalise(words: list[Word], times: list, duration: float) -> None:
    prev_start = 0.0
    for k, (w, (s, e)) in enumerate(zip(words, times)):
        s = min(max(s, prev_start), duration)  # starts never go backwards
        nxt = times[k + 1][0] if k + 1 < len(words) else duration
        e = min(max(e, s + MIN_WORD), max(nxt, s + MIN_WORD), duration)
        w.start, w.end = s, max(e, s)
        prev_start = s


# --------------------------------------------------------------------------- fallback

def speech_regions(samples: np.ndarray, sr: int = SAMPLE_RATE, frame: float = 0.02,
                   min_silence: float = 0.12, min_speech: float = 0.08) -> list[tuple[float, float]]:
    """Energy VAD. A frame is speech when its RMS level is above an adaptive
    threshold: 35 dB below the loud (95th percentile) frames, but at least
    6 dB above the noise floor (10th percentile)."""
    hop = max(1, int(sr * frame))
    n = len(samples) // hop
    if n == 0:
        return []
    rms = np.sqrt(np.mean(samples[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    threshold = max(np.percentile(db, 95) - 35, np.percentile(db, 10) + 6)
    voiced = db > threshold

    regions: list[list[float]] = []
    for idx in np.flatnonzero(np.diff(np.concatenate([[0], voiced.astype(int), [0]]))):
        if not regions or len(regions[-1]) == 2:
            regions.append([idx * frame])
        else:
            regions[-1].append(idx * frame)
    merged: list[list[float]] = []
    for s, e in regions:
        if merged and s - merged[-1][1] < min_silence:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s >= min_speech]


def _place(words: list[Word], regions: list[tuple[float, float]]) -> None:
    """Lay words end to end along the *voiced* timeline of ``regions``
    (silences skipped), each taking time proportional to its weight."""
    lengths = np.array([e - s for s, e in regions])
    weights = np.array([_weight(w) for w in words])
    edges = np.concatenate([[0.0], np.cumsum(weights)]) / weights.sum() * lengths.sum()
    starts = np.array([s for s, _ in regions])
    offsets = np.concatenate([[0.0], np.cumsum(lengths)])

    def to_real(u: float) -> float:
        r = min(int(np.searchsorted(offsets, u, side="right")) - 1, len(regions) - 1)
        return float(starts[r] + (u - offsets[r]))

    for k, w in enumerate(words):
        w.start, w.end = to_real(edges[k]), to_real(edges[k + 1] - 1e-6)


INLINE_BREAK_COST = 2.0  # a sentence break with no audible pause under it


def estimate(words: list[Word], regions: list[tuple[float, float]], duration: float,
             max_sentences: int = 4, max_regions: int = 12) -> None:
    """Fit sentences to speech regions by dynamic programming.

    Sentences and regions are both cut into the same number of consecutive
    groups, and group g of sentences is laid onto group g of regions. A
    pairing costs

        ((V - E) / sigma)^2,   with sigma = 0.3 s + 0.25 * E

    V is the voiced length of the regions. E = T * W_g / W is the time the
    sentences should take at the average speaking rate, where T is the total
    voiced time and W_g / W their share of word weight. sigma lets longer
    stretches drift more. Each sentence break that falls inside a region,
    with no pause under it, adds INLINE_BREAK_COST. Each group cut subtracts
    gap / longest_gap, so breaks prefer the longest pauses. A region can hold
    several sentences and a sentence can span several regions (mid-sentence
    pauses)."""
    if not regions:
        regions = [(0.0, duration)]
    sentences: list[list[Word]] = []
    for w in words:
        if not sentences or w.sentence != sentences[-1][0].sentence:
            sentences.append([])
        sentences[-1].append(w)
    n, m = len(sentences), len(regions)

    cum = np.concatenate([[0.0], np.cumsum([e - s for s, e in regions])])
    total = cum[-1]
    gaps = np.array([0.0] + [regions[k][0] - regions[k - 1][1] for k in range(1, m)])
    gap_norm = gaps / max(gaps.max(), 1e-9)
    csw = np.concatenate([[0.0], np.cumsum([sum(_weight(w) for w in s) for s in sentences])])

    inf = float("inf")
    dp = np.full((n + 1, m + 1), inf)
    back: dict[tuple[int, int], tuple[int, int]] = {}
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        for k in range(1, m + 1):
            for i0 in range(max(0, i - max_sentences), i):
                for k0 in range(max(0, k - max_regions), k):
                    if dp[i0][k0] == inf:
                        continue
                    expected = total * (csw[i] - csw[i0]) / csw[-1]
                    sigma = 0.3 + 0.25 * expected
                    c = ((cum[k] - cum[k0] - expected) / sigma) ** 2
                    c += INLINE_BREAK_COST * (i - i0 - 1) - (gap_norm[k0] if k0 else 0.0)
                    if dp[i0][k0] + c < dp[i][k]:
                        dp[i][k], back[(i, k)] = dp[i0][k0] + c, (i0, k0)
    if dp[n][m] == inf:  # more sentences/regions per group than allowed
        _place(words, regions)
        return
    i, k = n, m
    while i:
        i0, k0 = back[(i, k)]
        _place([w for s in sentences[i0:i] for w in s], regions[k0:k])
        i, k = i0, k0


# --------------------------------------------------------------------------- entry point

def time_words(script: Script, samples: np.ndarray, method: str = "auto",
               model_name: str = "base.en", device: str = "cpu") -> str:
    """Fill ``start``/``end`` on every script word. Returns the method used."""
    duration = len(samples) / SAMPLE_RATE
    if method in ("auto", "whisper"):
        try:
            asr = transcribe(samples, model_name, device)
        except ImportError:
            if method == "whisper":
                raise
            log.warning("faster-whisper not installed; estimating word timings from audio energy")
        else:
            score = align(script.words, asr, duration)
            log.info("whisper alignment: %.0f%% of script words matched exactly", score * 100)
            if score >= 0.5 or method == "whisper":
                return "whisper"
            log.warning("transcript disagrees with the script; falling back to estimation")
    estimate(script.words, speech_regions(samples), duration)
    return "estimate"
