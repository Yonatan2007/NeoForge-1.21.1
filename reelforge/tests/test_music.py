"""Music bed and final mix, measured on tiny synthetic signals.

Every input is generated with ffmpeg's lavfi sources (tones, tone bursts as a
stand-in "voice", frequency steps that reveal which part of a file plays), and
every output is decoded back to samples, so lengths, placement, looping, levels
and ducking are checked numerically rather than by eye."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelforge import music
from reelforge.config import MusicSettings
from reelforge.media import MediaError

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

SR = 48000
VOICE_BURSTS = ((2.0, 4.0), (7.0, 9.0))   # where the synthetic voice "speaks"


def synth(path: Path, source: str, *args: str) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, *args, str(path)],
                   check=True)
    return path


def decode(path: Path) -> np.ndarray:
    """(frames, 2) float32 at the file's own rate (no resampling)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def stream(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-print_format", "json",
                          str(path)], capture_output=True, check=True).stdout
    return json.loads(out)["streams"][0]


def lufs(path: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])


def db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12))


def level(a: np.ndarray, t0: float, t1: float) -> float:
    return db(a[int(t0 * SR):int(t1 * SR)])


def dominant_hz(a: np.ndarray, t0: float, t1: float) -> float:
    x = a[int(t0 * SR):int(t1 * SR), 0]
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spectrum) * SR / len(x))


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("music")
    burst = "+".join(f"between(t,{a},{b})" for a, b in VOICE_BURSTS)
    return {
        "tone": synth(d / "tone.mp3", "sine=f=220:d=6:r=44100", "-ac", "2", "-b:a", "128k"),
        "quiet": synth(d / "quiet.wav", "sine=f=220:d=6:r=44100", "-af", "volume=-20dB"),
        # 0-1 s 300 Hz, 1-2 s 600 Hz, 2-3 s 900 Hz, 3-4 s 1200 Hz; mono 22.05 kHz
        "steps": synth(d / "steps.wav", "aevalsrc='0.5*sin(2*PI*300*(1+floor(t))*t)':s=22050:d=4"),
        "voice": synth(d / "voice.wav", f"aevalsrc='if({burst},0.5*sin(2*PI*1000*t),0)':s=48000:d=10"),
        "soft_voice": synth(d / "soft.wav", f"aevalsrc='if({burst},0.01*sin(2*PI*1000*t),0)':s=48000:d=10"),
    }


def bed(tmp_path, file: Path, video: float, voice: Path | None = None, voice_delay: float = 0.0,
        **settings) -> np.ndarray:
    settings = {"fade_in": 0.0, "fade_out": 0.0, "duck": False} | settings
    out = music.prepare_music(MusicSettings(file=str(file), **settings), video, tmp_path / "bed.wav",
                              voice, voice_delay)
    return decode(out)


# --------------------------------------------------------------------------- inspection

def test_audio_info_reads_mp3_wav_and_m4a(tmp_path):
    mp3 = synth(tmp_path / "a.mp3", "sine=f=440:d=2:r=44100", "-ac", "2")
    wav = synth(tmp_path / "b.wav", "sine=f=440:d=1.5:r=22050")
    m4a = synth(tmp_path / "c.m4a", "sine=f=440:d=3:r=32000", "-c:a", "aac")
    assert music.audio_info(mp3)["sample_rate"] == 44100 and music.audio_info(mp3)["channels"] == 2
    assert music.audio_info(wav) == {"duration": pytest.approx(1.5, abs=1e-3), "sample_rate": 22050,
                                     "channels": 1}
    info = music.audio_info(m4a)
    assert info["sample_rate"] == 32000 and info["duration"] == pytest.approx(3.0, abs=0.05)
    image = synth(tmp_path / "d.png", "color=c=red:s=16x16", "-frames:v", "1")
    with pytest.raises(MediaError):
        music.audio_info(image)


def test_waveform_peaks_follow_the_envelope_and_are_normalised(tmp_path):
    ramp = synth(tmp_path / "ramp.wav", "aevalsrc='0.4*t/4*sin(2*PI*440*t)':s=44100:d=4")
    w = music.waveform_peaks(ramp, buckets=40)
    p = np.array(w["peaks"])
    assert w["duration"] == pytest.approx(4.0, abs=1e-3) and len(p) == 40
    assert p.max() == 1.0 and p.min() >= 0.0 and p[0] < 0.05
    assert np.all(np.diff(p) > -0.01) and p[20] == pytest.approx(0.5, abs=0.05)

    silent = synth(tmp_path / "silent.wav", "anullsrc=r=8000:cl=mono", "-t", "1")
    assert music.waveform_peaks(silent, buckets=10)["peaks"] == [0.0] * 10
    assert len(music.waveform_peaks(ramp, buckets=0)["peaks"]) == 1


def test_waveform_peaks_of_five_minute_mp3_are_fast(tmp_path):
    import time
    track = synth(tmp_path / "long.mp3", "sine=f=220:d=300:r=44100", "-ac", "2", "-q:a", "9")
    t0 = time.perf_counter()
    w = music.waveform_peaks(track)
    assert time.perf_counter() - t0 < 2.0
    assert len(w["peaks"]) == 1000 and w["duration"] == pytest.approx(300, abs=0.1)


# --------------------------------------------------------------------------- music bed

def test_no_music_file_gives_none(tmp_path):
    assert music.prepare_music(MusicSettings(), 10.0, tmp_path / "m.wav") is None


@pytest.mark.parametrize("video", [3.0, 7.3333333, 0.5])
def test_bed_is_exactly_the_video_length_48k_stereo(tmp_path, media, video):
    out = music.prepare_music(MusicSettings(file=str(media["tone"])), video, tmp_path / "m.wav")
    s = stream(out)
    assert (int(s["sample_rate"]), s["channels"]) == (SR, 2)
    assert int(s["duration_ts"]) == round(video * SR) == len(decode(out))


def test_music_sits_between_start_at_and_end_at(tmp_path, media):
    a = bed(tmp_path, media["tone"], 5.0, start_at=1.0, end_at=3.5)
    assert np.abs(a[:int(0.999 * SR)]).max() == 0 and np.abs(a[int(3.501 * SR):]).max() == 0
    assert level(a, 1.05, 2.0) == pytest.approx(level(a, 2.5, 3.45), abs=0.3)


def test_selection_plays_and_loops_seamlessly(tmp_path, media):
    a = bed(tmp_path, media["steps"], 4.5, source_in=1.0, source_out=2.0, loop=True)
    for t in np.arange(0.0, 4.4, 0.4):          # only the 600 Hz second of the file is used
        assert dominant_hz(a, t, t + 0.1) == pytest.approx(600, abs=15)
    # The loop is the selection up to the seam, then a cycle of (selection -
    # crossfade) that starts with the crossfade: periodic outside the seams ...
    xf, cycle = round(music.LOOP_XFADE * SR), SR - round(music.LOOP_XFADE * SR)
    np.testing.assert_allclose(a[cycle + xf:2 * cycle], a[xf:cycle], atol=1e-6)
    np.testing.assert_allclose(a[2 * cycle + xf:3 * cycle], a[xf:cycle], atol=1e-6)
    # ... and without drop-outs at the seams.
    short = [level(a, t, t + 0.005) for t in np.arange(0.0, 4.49, 0.005)]
    assert min(short) > max(short) - 4


def test_without_loop_a_short_selection_plays_once(tmp_path, media):
    a = bed(tmp_path, media["steps"], 5.0, source_in=2.0, source_out=3.0, loop=False, start_at=0.5)
    assert len(a) == 5 * SR
    assert dominant_hz(a, 0.6, 1.4) == pytest.approx(900, abs=15)
    assert np.abs(a[int(1.51 * SR):]).max() == 0


def test_source_out_none_or_past_the_end_means_the_end_of_the_file(tmp_path, media):
    a = bed(tmp_path, media["steps"], 4.0, source_in=3.0, source_out=None, loop=False)
    b = bed(tmp_path, media["steps"], 4.0, source_in=3.0, source_out=60.0, loop=False)
    np.testing.assert_array_equal(a, b)
    assert dominant_hz(a, 0.1, 0.9) == pytest.approx(1200, abs=15)
    assert np.abs(a[int(1.01 * SR):]).max() == 0


def test_fades_at_both_ends_of_the_span(tmp_path, media):
    a = bed(tmp_path, media["tone"], 6.0, start_at=1.0, fade_in=1.0, fade_out=1.0)
    full = level(a, 2.5, 3.5)
    assert level(a, 1.0, 1.1) < full - 15 and level(a, 1.5, 1.6) < full - 1
    assert level(a, 2.0, 2.1) == pytest.approx(full, abs=0.3)
    assert level(a, 5.9, 6.0) < full - 15 and np.abs(a[-10:]).max() < 1e-3


def test_fades_longer_than_the_music_are_shortened(tmp_path, media):
    a = bed(tmp_path, media["tone"], 2.0, fade_in=5.0, fade_out=5.0)
    peak_at = np.argmax(np.abs(a[:, 0])) / SR
    assert 0.8 < peak_at < 1.2 and np.abs(a[:50]).max() < 0.01 and np.abs(a[-50:]).max() < 0.01


def test_level_is_loudness_normalised_then_volume_db(tmp_path, media):
    loud = level(bed(tmp_path, media["tone"], 3.0, volume_db=-10), 0.5, 2.5)
    quiet = level(bed(tmp_path, media["quiet"], 3.0, volume_db=-10), 0.5, 2.5)
    softer = level(bed(tmp_path, media["tone"], 3.0, volume_db=-20), 0.5, 2.5)
    assert quiet == pytest.approx(loud, abs=0.5)          # 20 dB apart, mono vs stereo before
    assert loud - softer == pytest.approx(10, abs=0.1)
    # -16 LUFS minus 10 dB; a 220 Hz tone on both channels reads ~ +1.9 LU above its RMS
    assert loud == pytest.approx(music.MUSIC_LUFS - 10 - 1.9, abs=1.0)


@pytest.mark.parametrize("name,source,args,tolerance", [
    ("mono_8k.wav", "sine=f=330:d=2:r=8000", [], 1e-6),
    ("odd_37800.wav", "sine=f=330:d=2:r=37800", ["-ac", "2"], 1e-6),
    ("mono_22k.mp3", "sine=f=330:d=2:r=22050", [], 1e-6),
    ("stereo.m4a", "sine=f=330:d=2:r=44100", ["-ac", "2", "-c:a", "aac"], 1e-3),  # joint stereo
])
def test_any_rate_and_channel_count_becomes_48k_stereo(tmp_path, name, source, args, tolerance):
    src = synth(tmp_path / name, source, *args)
    out = music.prepare_music(MusicSettings(file=str(src), fade_in=0, fade_out=0), 3.0,
                              tmp_path / "m.wav")
    a = decode(out)
    assert stream(out)["sample_rate"] == str(SR) and a.shape == (3 * SR, 2)
    np.testing.assert_allclose(a[:, 0], a[:, 1], atol=tolerance)
    assert dominant_hz(a, 0.5, 2.5) == pytest.approx(330, abs=5)     # looped past the 2 s file


@pytest.mark.parametrize("settings,video,message", [
    ({"source_in": 3.0, "source_out": 2.0}, 5.0, "selection"),
    ({"source_in": 2.0, "source_out": 2.0}, 5.0, "selection"),
    ({"source_in": 9.0}, 5.0, "track"),
    ({"start_at": 5.0}, 5.0, "video is only"),
    ({"start_at": 2.0, "end_at": 1.0}, 5.0, "ends at"),
    ({}, 0.0, "positive"),
])
def test_contradictory_settings_are_rejected(tmp_path, media, settings, video, message):
    with pytest.raises(ValueError, match=message):
        music.prepare_music(MusicSettings(file=str(media["tone"]), **settings), video,
                            tmp_path / "m.wav")


def test_out_of_range_settings_are_clamped(tmp_path, media):
    a = bed(tmp_path, media["tone"], 3.0, source_in=-2.0, start_at=-1.0, end_at=99.0)
    assert len(a) == 3 * SR and np.abs(a[:100]).max() > 0.001 and np.abs(a[-100:]).max() > 0.001


def test_missing_music_file_is_an_error(tmp_path):
    with pytest.raises(MediaError, match="not found"):
        music.prepare_music(MusicSettings(file=str(tmp_path / "nope.mp3")), 3.0, tmp_path / "m.wav")


# --------------------------------------------------------------------------- ducking

def duck_depth(a: np.ndarray, shift: float = 0.0) -> tuple[float, float]:
    """(level while the voice speaks, level between/before) of a music bed."""
    during = np.mean([level(a, s + shift + 0.5, e + shift - 0.5) for s, e in VOICE_BURSTS])
    between = np.mean([level(a, 0.3, 1.5), level(a, 5.2 + shift, 6.5 + shift)])
    return float(during), float(between)


@pytest.mark.parametrize("voice", ["voice", "soft_voice"])
@pytest.mark.parametrize("duck_db", [-8.0, -14.0])
def test_music_ducks_by_duck_db_while_the_voice_speaks(tmp_path, media, voice, duck_db):
    a = bed(tmp_path, media["tone"], 11.0, media[voice], duck=True, duck_db=duck_db)
    during, between = duck_depth(a)
    assert during - between == pytest.approx(duck_db, abs=0.75)   # same depth for a voice 34 dB softer


def test_ducking_is_smooth_and_follows_voice_delay(tmp_path, media):
    a = bed(tmp_path, media["tone"], 11.0, media["voice"], voice_delay=0.5, duck=True, duck_db=-8)
    plain = bed(tmp_path, media["tone"], 11.0)
    during, between = duck_depth(a, shift=0.5)
    assert during - between == pytest.approx(-8, abs=0.75)
    # Gain curve (ducked vs. unducked music, 10 ms windows) around the first word at 2.5 s.
    t = np.arange(1.0, 6.0, 0.01)
    gain = np.array([level(a, x, x + 0.01) - level(plain, x, x + 0.01) for x in t])
    assert np.abs(gain[t < 2.2]).max() < 0.1                        # untouched well before the voice
    assert gain[np.searchsorted(t, 2.5)] < -6                       # mostly down when it starts
    assert np.abs(np.diff(gain)).max() < 1.0                        # dB per 10 ms: no clicks/jumps
    down, up = gain[(t > 2.2) & (t < 2.7)], gain[(t > 4.4) & (t < 5.6)]
    assert np.all(np.diff(down) < 0.05) and np.all(np.diff(up) > -0.05)   # one smooth dip, one swell


def test_no_ducking_when_off_or_without_voice(tmp_path, media):
    off = bed(tmp_path, media["tone"], 11.0, media["voice"], duck=False)
    alone = bed(tmp_path, media["tone"], 11.0, None, duck=True)
    during, between = duck_depth(off)
    assert during == pytest.approx(between, abs=0.2)
    np.testing.assert_allclose(off, alone, atol=1e-6)


# --------------------------------------------------------------------------- mix

def test_mix_places_voice_and_hits_target_loudness(tmp_path, media):
    music_wav = music.prepare_music(MusicSettings(file=str(media["tone"]), duck=True), 11.0,
                                    tmp_path / "bed.wav", media["voice"], 0.25)
    out = music.mix(media["voice"], music_wav, 0.25, 11.0, tmp_path / "mix.wav")
    s = stream(out)
    assert (s["codec_name"], int(s["sample_rate"]), s["channels"]) == ("pcm_s16le", SR, 2)
    assert int(s["duration_ts"]) == 11 * SR
    assert lufs(out) == pytest.approx(music.MIX_LUFS, abs=1.0)
    a = decode(out)
    assert np.abs(a).max() <= music.MIX_LIMIT + 0.01
    assert dominant_hz(a, 2.4, 3.4) == pytest.approx(1000, abs=10)   # voice at 2+0.25 s
    assert dominant_hz(a, 5.0, 6.0) == pytest.approx(220, abs=10)    # music in the pause


def test_mix_voice_only_is_delayed_exactly(tmp_path, media):
    out = music.mix(media["voice"], None, 0.5, 8.0, tmp_path / "mix.wav")
    a = decode(out)
    assert len(a) == 8 * SR
    onset = np.argmax(np.abs(a[:, 0]) > 0.01) / SR
    assert onset == pytest.approx(2.5, abs=0.002)


def test_mix_music_only_and_nothing(tmp_path, media):
    music_wav = music.prepare_music(MusicSettings(file=str(media["tone"])), 4.0, tmp_path / "b.wav")
    out = music.mix(None, music_wav, 0.0, 4.0, tmp_path / "mix.wav")
    assert lufs(out) == pytest.approx(music.MIX_LUFS, abs=1.0)
    silent = decode(music.mix(None, None, 0.0, 2.5, tmp_path / "silent.wav"))
    assert silent.shape == (int(2.5 * SR), 2) and np.abs(silent).max() == 0
