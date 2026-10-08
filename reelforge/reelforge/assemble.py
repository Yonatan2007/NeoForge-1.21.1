"""Timeline assembly with MoviePy: shot plan, crossfades, loops, caption
overlay, voice + optional music bed, H.264/AAC export."""
from __future__ import annotations

import bisect
import math
import os
from pathlib import Path

import numpy as np
from moviepy import AudioFileClip, CompositeAudioClip, VideoClip, VideoFileClip, afx, vfx

from .captions import CaptionRenderer
from .config import VideoStyle
from .script import Word


def plan_shots(words: list[Word], total: float, vs: VideoStyle) -> list[tuple[float, float]]:
    """Cut on the voice: each new sentence is a candidate cut (slightly
    before it is spoken). Cuts closer than ``min_shot`` are skipped and shots
    longer than ``max_shot`` are split evenly: a shot of length L becomes
    ceil(L / max_shot) equal parts."""
    cuts = [0.0]
    for i, w in enumerate(words):
        if i > 0 and words[i - 1].ends_sentence:
            c = w.start - vs.cut_preroll
            if c - cuts[-1] >= vs.min_shot and total - c >= vs.min_shot:
                cuts.append(c)
    cuts.append(total)
    bounds = [0.0]
    for a, b in zip(cuts, cuts[1:]):
        parts = max(1, math.ceil((b - a) / vs.max_shot - 1e-9))
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
    """Frame source for the whole background.

    Segment i starts at shot i's start and runs ``crossfade`` seconds into the
    next shot. Inside that overlap the frame is a linear blend
    (1 - a) * previous + a * current with a = (t - start_i) / crossfade.
    Everywhere else a frame is read straight from one clip, which is about 7x
    cheaper than MoviePy's mask-based compose concatenation."""

    def __init__(self, segments: list, starts: list[float], vs: VideoStyle,
                 renderer: CaptionRenderer | None):
        self.segments, self.starts, self.vs, self.renderer = segments, starts, vs, renderer
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
        return self.renderer.overlay(f, t) if self.renderer else f


def assemble(clips: list[Path], shots: list[tuple[float, float]], voice: Path,
             renderer: CaptionRenderer | None, out_path: Path, vs: VideoStyle,
             music: Path | None = None, logger: str | None = "bar") -> Path:
    sources = [VideoFileClip(str(p), audio=False) for p in clips]
    segments = []
    for i, (a, b) in enumerate(shots):
        length = (b - a) + (vs.crossfade if i < len(shots) - 1 else 0.0)
        segments.append(_segment(sources[i % len(sources)], length, i // len(sources)))
    timeline = Timeline(segments, [a for a, _ in shots], vs, renderer)
    total = shots[-1][1]
    video = VideoClip(frame_function=timeline.frame, duration=total)

    layers = [AudioFileClip(str(voice)).with_start(vs.voice_delay)]
    if music is not None:
        bed = AudioFileClip(str(music)).with_effects([
            afx.AudioLoop(duration=total), afx.MultiplyVolume(vs.music_volume),
            afx.AudioFadeIn(1.5), afx.AudioFadeOut(min(2.0, total / 4))])
        layers.insert(0, bed)
    video = video.with_audio(CompositeAudioClip(layers).with_duration(total))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        video.write_videofile(
            str(out_path), fps=vs.fps, codec="libx264", audio_codec="aac",
            audio_bitrate=vs.audio_bitrate, preset=vs.preset, pixel_format="yuv420p",
            ffmpeg_params=["-crf", str(vs.crf), "-maxrate", vs.maxrate, "-bufsize", vs.bufsize,
                           "-movflags", "+faststart", "-profile:v", "high"],
            threads=os.cpu_count(), logger=logger)
    finally:
        video.close()
        for s in sources:
            s.close()
    return out_path
