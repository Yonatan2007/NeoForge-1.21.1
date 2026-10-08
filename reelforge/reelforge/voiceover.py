"""Voiceover generation. Every route ends with an audio file on disk.

1. Higgsfield through the Claude connector (default)
   A Python process cannot call Claude's MCP connectors, and the connector's
   OAuth session never leaves Claude. So `reelforge plan` writes
   ``higgsfield_request.json``: the exact arguments for the connector's
   ``generate_audio`` tool. Claude submits it with your connected account,
   waits for the job and hands the audio URL to
   ``reelforge render --voiceover <url>``. The project skill in
   ``.claude/skills/jackk-reel/SKILL.md`` automates this hand-off.

2. Higgsfield REST API (headless, experimental)
   Uses the documented request lifecycle (submit, poll ``status_url``, read
   ``audio.url``). As of Oct 2026 Higgsfield's public API catalogue lists
   image and video models only, so the TTS endpoint path must come from your
   API console via ``HIGGSFIELD_TTS_ENDPOINT``.

3. Piper (free, offline) for drafts, so visuals can be iterated without
   spending credits.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

from .media import MediaError, download
from .script import Script

# A male preset narrator voice from `list_voices`. Audition others with the
# connector's list_voices preview URLs and set HIGGSFIELD_VOICE_ID.
DEFAULT_VOICE_ID = "30fc8796-ceb6-4a66-b3a7-4a145ef7f346"  # "Arthur"

# Arguments for the connector's generate_audio tool (see models_explore type=audio).
VOICE_PRESETS: dict[str, dict] = {
    # ElevenLabs engine: the most natural, emotionally weighted reads.
    "elevenlabs": {"model": "text2speech_v2", "variant": "elevenlabs"},
    # Seed Audio exposes a speed control; slightly slower reads feel heavier.
    "seed": {"model": "seed_audio", "speech_rate": -10, "format": "wav", "sample_rate": 48000},
}


def higgsfield_request(script: Script, preset: str = "elevenlabs", voice_id: str | None = None,
                       voice_type: str = "preset", extra: dict | None = None) -> dict:
    """The connector call that turns ``script`` into a voiceover."""
    if preset not in VOICE_PRESETS:
        raise ValueError(f"unknown voice preset {preset!r}; choose from {sorted(VOICE_PRESETS)}")
    params = dict(VOICE_PRESETS[preset])
    params.update(prompt=script.text, voice_type=voice_type, voice_id=voice_id or DEFAULT_VOICE_ID)
    params.update(extra or {})
    return {
        "tool": "mcp__higgsfield__generate_audio",
        "params": params,
        "next": "wait for the job, then run: python -m reelforge render <script> --voiceover <audio url>",
    }


def write_request(request: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request, indent=2, ensure_ascii=False) + "\n")
    return path


class HiggsfieldAPI:
    """Experimental REST route; see the module docstring."""

    base_url = "https://api.higgsfield.ai"
    terminal = {"completed", "failed", "nsfw", "canceled"}

    def __init__(self, key: str, endpoint: str, timeout: float = 600.0):
        if not key:
            raise MediaError("set HF_KEY (or HF_API_KEY + HF_API_SECRET) for --tts higgsfield-api")
        if not endpoint:
            raise MediaError("set HIGGSFIELD_TTS_ENDPOINT to the speech model path from console.higgsfield.ai")
        self.headers = {"Authorization": f"Key {key}"}
        self.url = f"{self.base_url}/{endpoint.lstrip('/')}"
        self.timeout = timeout

    def synthesize(self, params: dict, dst: Path) -> Path:
        arguments = {k: v for k, v in params.items() if k != "model"}
        r = requests.post(self.url, json=arguments, timeout=60,
                          headers={**self.headers, "Idempotency-Key": str(uuid.uuid4())})
        r.raise_for_status()
        status_url = r.json()["status_url"]

        delay, deadline = 2.0, time.monotonic() + self.timeout
        while True:
            s = requests.get(status_url, headers=self.headers, timeout=30)
            s.raise_for_status()
            result = s.json()
            if result["status"] in self.terminal:
                break
            if time.monotonic() > deadline:
                raise MediaError(f"Higgsfield request timed out: {status_url}")
            time.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 1.5, 10.0)

        if result["status"] != "completed":
            raise MediaError(f"Higgsfield request {result['status']}: {result.get('error')}")
        audio = result.get("audio") or (result.get("audios") or [{}])[0]
        if not audio.get("url"):
            raise MediaError(f"no audio URL in Higgsfield response: {result}")
        return download(audio["url"], dst.with_suffix(Path(audio["url"].split("?")[0]).suffix or ".mp3"))


def synthesize_piper(text: str, model: str | Path, dst: Path,
                     length_scale: float = 1.12, sentence_silence: float = 0.45) -> Path:
    """Local draft voice (pip install piper-tts; voices: huggingface.co/rhasspy/piper-voices)."""
    proc = subprocess.run(
        [sys.executable, "-m", "piper", "-m", str(model), "-f", str(dst),
         "--length-scale", str(length_scale), "--sentence-silence", str(sentence_silence)],
        input=text.encode(), capture_output=True)
    if proc.returncode != 0 or not dst.exists():
        raise MediaError("piper failed:\n" + proc.stderr.decode(errors="replace")[-1500:])
    return dst
