"""Timeline assembly with MoviePy: shot plan, crossfades, loops, overlays
(hook + captions), final audio, H.264/AAC export."""
from __future__ import annotations

import bisect
import math
import os
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
from moviepy import AudioFileClip, VideoClip, VideoFileClip, vfx
from proglog import ProgressBarLogger

from .config import VideoStyle
from .script import Word


class Overlay(Protocol):
    def overlay(self, frame: np.ndarray, t: float) -> np.ndarray: ...


def plan_shots(words: list[Word], total: float, vs: VideoStyle,
               keep_until: float = 0.0) -> list[tuple[float, float]]:
    """Cut on the voice: each new sentence is a candidate cut (slightly
    before it is spoken). Cuts closer than ``min_shot`` are skipped and shots
    longer than ``max_shot`` are split evenly: a shot of length L becomes
    ceil(L / max_shot) equal parts.

    ``keep_until`` protects the opening: no cut happens before it and the
    first shot is never split, so the hook text stays on one picture."""
    cuts = [0.0]
    for i, w in enumerate(words):
        if i > 0 and words[i - 1].ends_sentence:
            c = w.start - vs.cut_preroll
            if c < keep_until:
                continue
            if c - cuts[-1] >= vs.min_shot and total - c >= vs.min_shot:
                cuts.append(c)
    cuts.append(total)
    bounds = [0.0]
    for a, b in zip(cuts, cuts[1:]):
        parts = 1 if (a == 0.0 and keep_until > 0) else max(1, math.ceil((b - a) / vs.max_shot - 1e-9))
        bounds += [a + (b - a) * k / parts for k in range(1, parts + 1)]
    return list(zip(bounds, bounds[1:]))


def _segment(src: VideoFileClip, length: float, reuse: int):
    """``length`` seconds of ``src``. Clips that are too short are looped;
    clips reused later in the timeline start from a different offset."""
    if src.duration >= length + 0.05:
        room = src.duration - length
        offset = (reuse * 0.37 % 1.0) * room
        return src.subclipped(offset, offset + length)
    return src.with_effects([vfx.Loop(duration=length)])


class Timeline:
    """Frame source for the whole video.

    Segment i starts at shot i's start and runs ``crossfade`` seconds into the
    next shot. Inside that overlap the frame is a linear blend
    (1 - a) * previous + a * current with a = (t - start_i) / crossfade.
    Everywhere else a frame is read straight from one clip, which is about 7x
    cheaper than MoviePy's mask-based compose concatenation. Overlays (hook
    text, captions) are drawn on top in order."""

    def __init__(self, segments: list, starts: list[float], vs: VideoStyle,
                 overlays: list[Overlay] | None = None):
        self.segments, self.starts, self.vs = segments, starts, vs
        self.overlays = [o for o in (overlays or []) if o is not None]
        self.total = starts[-1] + segments[-1].duration

    def frame(self, t: float) -> np.ndarray:
        vs = self.vs
        i = max(0, bisect.bisect_right(self.starts, t) - 1)
        local = t - self.starts[i]
        f = self.segments[i].get_frame(min(local, self.segments[i].duration - 1e-3))
        if i > 0 and local < vs.crossfade:
            prev = self.segments[i - 1].get_frame(
                min(t - self.starts[i - 1], self.segments[i - 1].duration - 1e-3))
            a = local / vs.crossfade
            f = prev * (1.0 - a) + f * a
        fade = min(1.0, t / vs.fade_in if vs.fade_in else 1.0,
                   (self.total - t) / vs.fade_out if vs.fade_out else 1.0)
        if fade < 1.0:
            f = f * max(0.0, fade)
        f = np.asarray(f).astype(np.uint8, copy=False)
        for o in self.overlays:
            f = o.overlay(f, t)
        return f


class _FrameProgress(ProgressBarLogger):
    """Forwards MoviePy's frame counter to a callback (fraction 0..1); the
    callback may raise to abort the export (used for cancel)."""

    def __init__(self, on_fraction: Callable[[float], None]):
        super().__init__()
        # not "callback": proglog's ProgressLogger already has a callback() method
        self.on_fraction = on_fraction

    def bars_callback(self, bar, attr, value, old_value=None):
        if bar == "frame_index" and attr == "index":
            total = self.bars[bar].get("total") or 0
            if total:
                self.on_fraction(min(1.0, value / total))


def open_segments(clips: list[Path], shots: list[tuple[float, float]], vs: VideoStyle):
    """(sources, segments): clip i % n for shot i, each segment long enough to
    cover its shot plus the crossfade into the next one. A file used for
    several shots is opened once and each reuse starts at a new offset."""
    opened: dict[str, VideoFileClip] = {}
    uses: dict[str, int] = {}
    segments = []
    for i, (a, b) in enumerate(shots):
        key = str(clips[i % len(clips)])
        if key not in opened:
            opened[key] = VideoFileClip(key, audio=False)
        length = (b - a) + (vs.crossfade if i < len(shots) - 1 else 0.0)
        segments.append(_segment(opened[key], length, uses.get(key, 0)))
        uses[key] = uses.get(key, 0) + 1
    return list(opened.values()), segments


def assemble(clips: list[Path], shots: list[tuple[float, float]], audio: Path | None,
             overlays: list[Overlay], out_path: Path, vs: VideoStyle,
             logger: str | None = "bar", on_progress: Callable[[float], None] | None = None) -> Path:
    """Render the final MP4. ``clips`` are prepared (graded, output-sized)
    files, one per shot or reused cyclically; ``audio`` is the final mix."""
    sources, segments = open_segments(clips, shots, vs)
    timeline = Timeline(segments, [a for a, _ in shots], vs, overlays)
    total = shots[-1][1]
    video = VideoClip(frame_function=timeline.frame, duration=total)
    if audio is not None:
        video = video.with_audio(AudioFileClip(str(audio)).with_duration(total))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        video.write_videofile(
            str(out_path), fps=vs.fps, codec="libx264", audio=audio is not None, audio_codec="aac",
            audio_bitrate=vs.audio_bitrate, preset=vs.preset, pixel_format="yuv420p",
            ffmpeg_params=["-crf", str(vs.crf), "-maxrate", vs.maxrate, "-bufsize", vs.bufsize,
                           "-movflags", "+faststart", "-profile:v", "high"],
            threads=os.cpu_count(),
            logger=_FrameProgress(on_progress) if on_progress else logger)
    finally:
        video.close()
        for s in sources:
            s.close()
    return out_path
