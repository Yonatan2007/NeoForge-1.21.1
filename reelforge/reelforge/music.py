"""Music: waveform data for the UI, the music bed of a reel and the final mix.

* ``audio_info`` / ``waveform_peaks`` - what the UI shows for an uploaded track.
* ``prepare_music`` - the music bed: the chosen part of the track, placed on
  the video timeline, looped or not, faded, loudness-levelled and ducked under
  the voice, as a 48 kHz stereo WAV exactly as long as the video.
* ``mix`` - voice + music bed -> the reel's soundtrack (~-14 LUFS, limited).

Everything runs as ffmpeg filter graphs. Positions are converted to whole
samples at 48 kHz once and the graphs cut, pad and fade by sample count
(``atrim=end_sample``, ``apad=whole_len``, ``afade=ss/ns``), so every output is
exactly the requested number of samples and lines up with the video.

Levels use two-pass loudness normalisation: the ``ebur128`` filter measures
the integrated loudness and a plain ``volume`` gain corrects it. A linear gain
keeps the music's and the voice's dynamics intact, unlike one-pass
``loudnorm``, which rides the gain continuously.
"""
from __future__ import annotations

import functools
import logging
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import MusicSettings
from .media import MediaError, probe, run

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
_FRAME_BYTES = 8                 # one stereo float32 sample frame
_STEREO = f"aresample={SAMPLE_RATE},aformat=sample_fmts=flt:channel_layouts=stereo"

MUSIC_LUFS = -16.0               # music is normalised to this before ``volume_db`` is applied
MIX_LUFS = -14.0                 # final soundtrack (what Reels/TikTok/Shorts play at)
MIX_LIMIT = 0.89                 # limiter ceiling, about -1 dBFS
MAX_NORM_GAIN = 30.0             # never boost/cut by more than this to reach a loudness target
SILENT_LUFS = -69.0              # ebur128 reports -70 LUFS for silence / clips too short to gate
MIN_SELECTION = 0.1              # seconds; shorter music selections are rejected
LOOP_XFADE = 0.05                # seconds of equal-power crossfade at each loop seam

# Ducking. The voice is turned into a 0/1 "speaking" signal (independent of
# how loud it is), held over short pauses and fed as the sidechain of a hard
# knee compressor: a constant full-scale sidechain gives a constant gain
# reduction of exactly ``duck`` dB, and the compressor's attack/release make
# the dips smooth instead of chopped.
DUCK_MAX = 40.0                  # dB
DUCK_LOOKAHEAD = 0.18            # s: the music starts dipping just before a word
DUCK_HOLD = 0.30                 # s: pauses shorter than this don't let the music swell
DUCK_ATTACK_MS = 400.0           # ~0.2 s from full level to the ducked level
DUCK_RELEASE_MS = 2500.0         # ~0.6 s back to full level after an 8 dB duck
DUCK_RATIO = 20.0                # the compressor's maximum ratio
_VOICE_REF_LUFS = -20.0          # sidechain voice is normalised to this ...
_SPEECH_GATE_DB = -25.0          # ... and counts as speaking above this (relative) level

PEAK_RATE = 11025                # waveform peaks are computed at this sample rate
MAX_BUCKETS = 20000


# --------------------------------------------------------------------------- inspection

def audio_info(path: Path) -> dict:
    """``{"duration", "sample_rate", "channels"}`` of the first audio stream.

    Raises ``MediaError`` when the file can't be read or has no audio."""
    data = probe(path)
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if stream is None:
        raise MediaError(f"{Path(path).name} has no audio track")
    duration = _number(stream.get("duration")) or _number(data.get("format", {}).get("duration"))
    return {"duration": duration or 0.0,
            "sample_rate": int(stream.get("sample_rate") or 0),
            "channels": int(stream.get("channels") or 0)}


def _number(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def waveform_peaks(path: Path, buckets: int = 1000) -> dict:
    """Peak envelope for drawing a waveform: ``{"duration", "peaks"}``.

    ``peaks[i]`` is the largest absolute sample (over both channels) in the
    i-th of ``buckets`` equal slices of the track, scaled so the loudest
    slice is 1.0 (a silent track gives all zeros). ``duration`` is the
    decoded length, which is what the selection seconds refer to. Results are
    cached per file version, so the UI can ask again for free."""
    path = Path(path)
    st = path.stat()
    buckets = max(1, min(int(buckets), MAX_BUCKETS))
    peaks, duration = _peaks(str(path.resolve()), st.st_size, st.st_mtime_ns, buckets)
    return {"duration": duration, "peaks": list(peaks)}


@functools.lru_cache(maxsize=16)
def _peaks(path: str, size: int, mtime_ns: int, buckets: int) -> tuple[tuple[float, ...], float]:
    # Decoding dominates (a 5-minute mp3 takes ~1 s); a low output rate keeps
    # the pipe and numpy work small while the envelope shape stays the same.
    raw = run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "2",
               "-ar", str(PEAK_RATE), "-f", "s16le", "-"]).stdout
    pcm = np.frombuffer(raw, dtype=np.int16)
    frames = len(pcm) // 2
    if not frames:
        return (0.0,) * buckets, 0.0
    # Reduce the interleaved samples directly: a bucket of frames is a run of
    # 2x as many samples, so its max/min already covers both channels.
    starts = np.arange(buckets, dtype=np.int64) * frames // buckets * 2
    peaks = np.maximum(np.maximum.reduceat(pcm, starts).astype(np.float64),
                       -np.minimum.reduceat(pcm, starts).astype(np.float64))
    top = peaks.max()
    if top > 0:
        peaks /= top
    return tuple(np.round(peaks, 4).tolist()), frames / PEAK_RATE


# --------------------------------------------------------------------------- music bed

@dataclass(frozen=True)
class _Plan:
    """Validated music settings in samples on the video timeline."""
    total: int                   # video length
    start: int                   # first sample of the music span
    span: int                    # music span length (start_at .. end_at)
    source_in: float             # seconds into the file
    source_out: float | None     # None = to the end of the file


def _samples(seconds: float) -> int:
    return int(round(seconds * SAMPLE_RATE))


def _plan(ms: MusicSettings, video_duration: float, file_duration: float) -> _Plan:
    """Clamp what has an obvious meaning (negative times, ends past the file
    or the video) and reject settings that contradict each other with a
    message the user can act on."""
    total = _samples(video_duration)
    if total <= 0:
        raise ValueError(f"The video length must be positive (got {video_duration} s).")
    source_in = max(0.0, float(ms.source_in or 0.0))
    if file_duration and source_in > file_duration - MIN_SELECTION:
        raise ValueError(f"The music selection starts at {source_in:.1f} s, at the end of the "
                         f"{file_duration:.1f} s track. Choose an earlier start.")
    source_out = ms.source_out
    if source_out is not None and file_duration and source_out >= file_duration:
        source_out = None  # "to the end": ffprobe's estimate for an mp3 may be a little short
    if source_out is not None and source_out - source_in < MIN_SELECTION:
        raise ValueError(f"The music selection {source_in:.2f}-{source_out:.2f} s is empty: its end "
                         f"must come at least {MIN_SELECTION} s after its start.")
    start_at = max(0.0, float(ms.start_at or 0.0))
    start = _samples(start_at)
    if start >= total:
        raise ValueError(f"The music starts at {start_at:.1f} s, but the video is only "
                         f"{video_duration:.1f} s long. Move the music start earlier.")
    end = total if ms.end_at is None else min(total, _samples(ms.end_at))
    if end <= start:
        raise ValueError(f"The music ends at {ms.end_at:.1f} s, which is not after its start at "
                         f"{start_at:.1f} s.")
    return _Plan(total, start, end - start, source_in, source_out)


def prepare_music(ms: MusicSettings, video_duration: float, out_wav: Path,
                  voice_wav: Path | None = None, voice_delay: float = 0.0) -> Path | None:
    """Render the music bed (see the module docstring). ``None`` when no music
    file is set. The WAV (48 kHz stereo float) is silent until
    ``ms.start_at``, then plays ``[source_in, source_out]`` of the file -
    looped with short crossfades when ``ms.loop`` and the span is longer,
    otherwise once - until ``ms.end_at`` (or the video end), with fades at
    both ends of what plays. Level: loudness-normalised to ``MUSIC_LUFS``,
    then ``ms.volume_db``. With ``ms.duck`` and a voice (placed at
    ``voice_delay`` like in the final mix) the music dips by ``ms.duck_db``
    while the voice speaks."""
    if not ms.file:
        return None
    src = Path(ms.file).expanduser()
    if not src.is_file():
        raise MediaError(f"music file not found: {ms.file}")
    plan = _plan(ms, video_duration, audio_info(src)["duration"])
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".music-", dir=out_wav.parent) as tmp:
        selection = Path(tmp) / "selection.f32"
        loudness = _extract(src, plan.source_in, plan.source_out, selection)
        n_sel = selection.stat().st_size // _FRAME_BYTES
        if n_sel < _samples(MIN_SELECTION):
            raise ValueError(f"The selected part of the music is too short ({n_sel / SAMPLE_RATE:.2f} s).")
        loop = bool(ms.loop) and plan.span > n_sel
        play = plan.span if loop else min(plan.span, n_sel)
        norm = 0.0 if loudness is None else _clamp(MUSIC_LUFS - loudness, MAX_NORM_GAIN)
        gain = norm + _clamp(ms.volume_db, 60.0)

        n_inputs = 3 if loop else 1
        inputs = ["-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", str(selection)] * n_inputs
        graph = [_selection_chain(n_sel, play, loop)]
        fades = _fades(play, ms.fade_in, ms.fade_out)
        delay = f",adelay=delays={plan.start}S:all=1" if plan.start else ""
        graph.append(f"[sel]asetpts=N/SR/TB{fades},volume={gain:.3f}dB{delay},"
                     f"apad=whole_len={plan.total},atrim=end_sample={plan.total}[bed]")

        duck = min(abs(ms.duck_db or 0.0), DUCK_MAX) if ms.duck and voice_wav else 0.0
        voice_level = _file_loudness(Path(voice_wav)) if duck >= 0.1 else None
        if voice_level is not None:
            inputs += ["-i", str(voice_wav)]
            graph += _duck_chains(n_inputs, voice_level, voice_delay, duck, plan.total)
        else:
            graph.append("[bed]anull[out]")
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph),
             "-map", "[out]", "-c:a", "pcm_f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", str(out_wav)])
    log.info("music bed: %.2f s of %s from %.2f s (%s), gain %+.1f dB, ducked %.1f dB",
             play / SAMPLE_RATE, src.name, plan.source_in, "looped" if loop else "once",
             gain, duck if voice_level is not None else 0.0)
    return out_wav


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value or 0.0)))


def _extract(src: Path, source_in: float, source_out: float | None, dst: Path) -> float | None:
    """Decode ``[source_in, source_out]`` of ``src`` to raw 48 kHz stereo
    float32 at ``dst`` and return its integrated loudness (one decode)."""
    end = f":end_sample={_samples(source_out)}" if source_out is not None else ""
    graph = (f"[0:a:0]{_STEREO},atrim=start_sample={_samples(source_in)}{end},asetpts=N/SR/TB,"
             "asplit=2[sel][m];[m]ebur128[meter]")
    return _measured(["-i", str(src), "-filter_complex", graph, "-map", "[sel]", "-f", "f32le",
                      str(dst), "-map", "[meter]", "-f", "null", "-"])


def _selection_chain(n_sel: int, play: int, loop: bool) -> str:
    """Graph section ``[sel]``: ``play`` samples of the selection (one input,
    or three copies of it for a loop). A loop plays the selection once up to
    the seam, then repeats a seamless cycle whose first ``xf`` samples
    crossfade the selection's tail into its head, so no repeat clicks and the
    first pass starts cleanly. (Separate inputs rather than ``asplit``:
    ``acrossfade`` fed from one split stream stalls in ffmpeg 6.)"""
    if not loop:
        return f"[0:a]atrim=end_sample={play}[sel]"
    xf = max(1, min(_samples(LOOP_XFADE), n_sel // 4))
    cycle = n_sel - xf
    return ";".join([
        f"[0:a]atrim=end_sample={cycle}[head]",
        f"[1:a]atrim=start_sample={cycle},asetpts=N/SR/TB[tail]",
        f"[2:a]atrim=end_sample={cycle}[body]",
        f"[tail][body]acrossfade=ns={xf}:c1=qsin:c2=qsin,aloop=loop=-1:size={cycle}[cycle]",
        f"[head][cycle]concat=n=2:v=0:a=1,atrim=end_sample={play}[sel]",
    ])


def _fades(play: int, fade_in: float, fade_out: float) -> str:
    """afade filters for the played music; fades longer than the music are
    shortened proportionally so they never overlap."""
    fi, fo = _samples(max(0.0, fade_in or 0.0)), _samples(max(0.0, fade_out or 0.0))
    if fi + fo > play:
        k = play / (fi + fo)
        fi, fo = int(fi * k), int(fo * k)
    out = f",afade=t=in:ss=0:ns={fi}:curve=qsin" if fi else ""
    if fo:
        out += f",afade=t=out:ss={play - fo}:ns={fo}:curve=qsin"
    return out


def _shift(seconds: float) -> str:
    """Filters that move a stream later (or earlier) by ``seconds``."""
    n = _samples(seconds)
    if n > 0:
        return f",adelay=delays={n}S:all=1"
    if n < 0:
        return f",atrim=start_sample={-n},asetpts=N/SR/TB"
    return ""


def _duck_chains(voice_input: int, voice_lufs: float, voice_delay: float, duck: float,
                 total: int) -> list[str]:
    """Graph sections that turn ``[bed]`` into the ducked ``[out]``.

    Sidechain: the voice (normalised so the gate is relative to its own
    level) -> mean-square envelope -> 1 while speaking, 0 otherwise; a copy
    delayed by ``DUCK_HOLD`` rides in a second channel and the compressor
    links channels by maximum, which bridges short pauses. With a constant
    full-scale sidechain, a hard knee and ratio R the gain reduction is
    ``-threshold_dB * (1 - 1/R)``, so the threshold is chosen to make it
    exactly ``duck`` dB."""
    level = _clamp(_VOICE_REF_LUFS - voice_lufs, MAX_NORM_GAIN)
    gate = 10 ** ((_VOICE_REF_LUFS + _SPEECH_GATE_DB) / 10)          # mean-square level
    threshold = 10 ** (-duck / (1 - 1 / DUCK_RATIO) / 20)
    hold = _samples(DUCK_HOLD)
    return [
        f"[{voice_input}:a:0]aresample={SAMPLE_RATE},aformat=sample_fmts=flt:channel_layouts=mono,"
        f"volume={level:.3f}dB{_shift(voice_delay - DUCK_LOOKAHEAD)},asplit=2[va][vb]",
        f"[va][vb]amultiply,lowpass=f=25:p=1,aeval='gte(val(0),{gate:.3e})',asplit=2[now][late]",
        f"[late]adelay=delays={hold}S[held]",
        f"[now]apad=pad_len={hold}[nowp]",
        "[nowp][held]amerge=inputs=2,apad[sc]",
        f"[bed][sc]sidechaincompress=threshold={threshold:.6f}:ratio={DUCK_RATIO:g}:knee=1:"
        f"attack={DUCK_ATTACK_MS:g}:release={DUCK_RELEASE_MS:g}:detection=peak:link=maximum,"
        f"apad=whole_len={total},atrim=end_sample={total}[out]",
    ]


# --------------------------------------------------------------------------- final mix

def mix(voice_wav: Path | None, music_wav: Path | None, voice_delay: float, video_duration: float,
        out_wav: Path) -> Path:
    """The reel's soundtrack: the voice placed at ``voice_delay`` plus the
    music bed (already placed on the video timeline), normalised to about
    ``MIX_LUFS`` and peak-limited to ``MIX_LIMIT``. 48 kHz stereo 16-bit WAV
    of exactly ``video_duration``; silence when both inputs are ``None``."""
    total = _samples(video_duration)
    if total <= 0:
        raise ValueError(f"The video length must be positive (got {video_duration} s).")
    tracks = [(Path(p), delay) for p, delay in ((voice_wav, voice_delay), (music_wav, 0.0))
              if p is not None]
    inputs = [arg for path, _ in tracks for arg in ("-i", str(path))]
    labels = ["sum"] if len(tracks) == 1 else [f"t{k}" for k in range(len(tracks))]
    graph = [f"[{k}:a:0]{_STEREO}{_shift(delay)},apad=whole_len={total},"
             f"atrim=end_sample={total}[{label}]"
             for k, ((_, delay), label) in enumerate(zip(tracks, labels))]
    if len(tracks) == 2:
        graph.append("[t0][t1]amix=inputs=2:normalize=0:duration=longest[sum]")
    elif not tracks:
        graph.append(f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=end_sample={total}[sum]")
    head = ";".join(graph)

    loudness = _measured([*inputs, "-filter_complex", f"{head};[sum]ebur128[meter]",
                          "-map", "[meter]", "-f", "null", "-"])
    gain = 0.0 if loudness is None else _clamp(MIX_LUFS - loudness, MAX_NORM_GAIN)
    tail = (f"[sum]volume={gain:.3f}dB,"
            f"alimiter=limit={MIX_LIMIT}:attack=5:release=50:level=0:latency=1,"
            f"apad=whole_len={total},atrim=end_sample={total}[out]")
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", f"{head};{tail}",
         "-map", "[out]", "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "2", str(out_wav)])
    log.info("mix: %.2f s, measured %s LUFS, gain %+.1f dB", total / SAMPLE_RATE,
             "silence" if loudness is None else f"{loudness:.1f}", gain)
    return out_wav


# --------------------------------------------------------------------------- loudness

_INTEGRATED = re.compile(r"Integrated loudness:\s*I:\s*(-?(?:inf|[\d.]+)) LUFS")


def _measured(args: list[str]) -> float | None:
    """Run ffmpeg ``args`` (whose graph contains one ``ebur128``) and return
    the integrated loudness in LUFS, or ``None`` for silence."""
    proc = run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-v", "info", "-y", *args])
    found = _INTEGRATED.findall(proc.stderr.decode(errors="replace"))
    if not found:
        raise MediaError("ffmpeg did not report a loudness measurement")
    value = float(found[-1])
    return value if value > SILENT_LUFS else None


def _file_loudness(path: Path) -> float | None:
    return _measured(["-i", str(path), "-filter_complex", f"[0:a:0]{_STEREO},ebur128[meter]",
                      "-map", "[meter]", "-f", "null", "-"])
