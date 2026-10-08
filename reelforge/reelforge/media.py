"""Thin wrappers around ffmpeg/ffprobe plus a download helper."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import requests

USER_AGENT = "reelforge/1.0"


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(f"{tool} not found on PATH; install FFmpeg first")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-2000:]
        raise MediaError(f"command failed: {' '.join(cmd[:6])} ...\n{tail}")
    return proc


def probe(path: str | Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)]).stdout
    return json.loads(out)


def duration(path: str | Path) -> float:
    return float(probe(path)["format"]["duration"])


def decode_audio(path: str | Path, sample_rate: int = 16000) -> np.ndarray:
    """Decode any audio/video file to mono float32 PCM via ffmpeg."""
    out = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
               "-ac", "1", "-ar", str(sample_rate), "-"]).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def prepare_voiceover(src: str | Path, dst: str | Path) -> Path:
    """Broadcast-style voice chain: rumble filter, gentle compression,
    loudness normalised to -16 LUFS (music sits under it), 48 kHz stereo WAV."""
    dst = Path(dst)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af",
         "highpass=f=70,acompressor=threshold=-20dB:ratio=3:attack=5:release=150,"
         "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ar", "48000", "-ac", "2", str(dst)])
    return dst


def download(url: str, dst: str | Path, headers: dict | None = None, timeout: int = 60) -> Path:
    """Stream ``url`` to ``dst`` atomically; skip if it already exists."""
    dst = Path(dst)
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    with requests.get(url, headers=hdrs, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(part, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    part.replace(dst)
    return dst


def fetch(src: str, dst_dir: Path, name: str) -> Path:
    """Return a local path for ``src`` (a path or an http(s) URL)."""
    if src.startswith(("http://", "https://")):
        suffix = Path(src.split("?")[0]).suffix or ".bin"
        tag = hashlib.sha1(src.encode()).hexdigest()[:8]
        return download(src, dst_dir / f"{name}_{tag}{suffix}")
    path = Path(src).expanduser()
    if not path.is_file():
        raise MediaError(f"file not found: {src}")
    return path
