"""Claude's side of the reelforge control panel (``panel/``).

The panel keeps each reel in its Artifact database (``projects/<id>``) and
its files in the Artifact's asset store; a plan or render is a task
(``tasks/<id>``) that Claude carries out with these commands:

    python -m reelforge.panelrun stage  DOC.json --assets DIR --work DIR
        Turn the project document (as ArtifactData saved it) into a project
        folder. Prints {"need": [asset ids]} until every file is in DIR.
    python -m reelforge.panelrun plan   --work DIR            -> DIR/plan.json
    python -m reelforge.panelrun review --work DIR            -> DIR/review/review.json
        Stock clips to choose from before rendering, with DIR/review/review.jpg
        (all their thumbnails on one sheet) to upload to the asset store.
    python -m reelforge.panelrun render --work DIR [--voiceover URL]
        Renders; DIR/progress.json follows it, a DIR/cancel file stops it,
        DIR/result.json holds the result (or the error).
    python -m reelforge.panelrun status --work DIR
        The task fields to write back: {status, stage, progress, message, log}.
    python -m reelforge.panelrun outputs --work DIR
        Files to upload to the asset store, keyed like the panel's outputs
        (the video re-encoded under the asset size limit when needed).
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import config, media
from .config import Project, Settings

ASSET_LIMIT = 18 * 1024 * 1024   # the asset store takes 20 MiB per file; keep a margin
LOG_LINES = 200


def _doc_body(raw: dict) -> dict:
    """ArtifactData's saved file may wrap the document ({id, version, data})."""
    for key in ("data", "document", "fields"):
        if isinstance(raw.get(key), dict) and "project" in raw[key]:
            return raw[key]
    return raw


def _asset_file(assets: Path, asset_id: str) -> Path | None:
    hits = sorted(p for p in assets.rglob(f"{asset_id}*") if p.is_file())
    return hits[0] if hits else None


def stage(doc_path: Path, assets: Path, work: Path) -> dict:
    doc = _doc_body(json.loads(doc_path.read_text(encoding="utf-8")))
    files = doc.get("files") or []
    need = [i for f in files for i in (f.get("store") or {}).get("ids", []) if not _asset_file(assets, i)]
    if need:
        return {"need": need}
    up = work / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    for f in files:
        store = f.get("store") or {}
        parts = [_asset_file(assets, i) for i in store.get("ids", [])]
        dst = up / f["name"]
        if store.get("type") == "b64":
            with open(dst, "wb") as out:
                for part in parts:
                    out.write(base64.b64decode("".join(part.read_text(encoding="utf-8").split())))
        elif parts:
            dst.write_bytes(parts[0].read_bytes())
    project = config.from_dict(Project, doc["project"])
    config.save_project(project, work / "project.json")
    out = {"ready": True, "work": str(work), "files": sorted(p.name for p in up.iterdir()),
           "voice": project.voice.source, "script_words": len(project.script.split())}
    if project.voice.source == "higgsfield" and not project.voice.file:
        # Claude makes the voice first: this is the generate_audio call to make
        from . import voiceover
        from .script import parse_script
        script = parse_script(project.script, project.auto_emphasis)
        if script.words:
            out["higgsfield_request"] = voiceover.higgsfield_request(
                script, project.voice.higgsfield_preset,
                project.voice.voice_id or Settings.from_env().higgsfield_voice_id,
                voice_type=project.voice.voice_type if project.voice.voice_id else "preset")
    return out


def _load(work: Path, voiceover: str | None = None) -> Project:
    project = config.load_project(work / "project.json")
    if voiceover:
        project.voice.source, project.voice.file = "file", voiceover
    return project


def _settings(work: Path) -> Settings:
    s = Settings.from_env()
    s.cache_dir = Path(s.cache_dir) if s.cache_dir else work.parent / ".cache"
    return s


def plan(work: Path) -> dict:
    from .pipeline import plan_project
    result = plan_project(_load(work), work, _settings(work))
    (work / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def review(work: Path) -> dict:
    from .pipeline import review_footage
    out_dir = work / "review"
    try:
        result = review_footage(_load(work), work, _settings(work), out_dir)
    except Exception as exc:  # reported to the panel
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "sheet": str(out_dir / result["sheet"]) if result.get("sheet") else None,
            "json": str(out_dir / "review.json"), "opening": len(result["opening"]),
            "pool": len(result["pool"]), "stock_shots": result["stock_shots"], "warnings": result["warnings"]}


class _FileFlag(threading.Event):
    """Set once a ``cancel`` file appears in the work folder."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def is_set(self) -> bool:  # type: ignore[override]
        return self.path.exists() or super().is_set()


def render(work: Path, voiceover: str | None) -> dict:
    from .pipeline import render_project
    state = {"status": "running", "stage": "voice", "progress": 0.0, "message": "Starting…", "log": [],
             "started": time.time()}
    progress_file = work / "progress.json"

    def write() -> None:
        state["log"] = state["log"][-LOG_LINES:]
        tmp = progress_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(progress_file)

    class LogToState(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            state["log"].append(self.format(record))
            write()

    handler = LogToState()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("reelforge")
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def progress(stage_name: str, frac: float, message: str) -> None:
        state.update(stage=stage_name, progress=round(frac, 3), message=message or state["message"])
        write()

    (work / "cancel").unlink(missing_ok=True)
    write()
    try:
        result = render_project(_load(work, voiceover), work, _settings(work), progress=progress,
                                cancel=_FileFlag(work / "cancel"))
        state.update(status="done", stage="finish", progress=1.0, message="Finished")
        out = {"ok": True, "result": result}
    except Exception as exc:  # reported to the panel
        cancelled = (work / "cancel").exists()
        state.update(status="cancelled" if cancelled else "error",
                     message="Render cancelled" if cancelled else (str(exc).splitlines() or ["error"])[0])
        state["log"].append(traceback.format_exc(limit=3))
        out = {"ok": False, "cancelled": cancelled, "error": str(exc) or type(exc).__name__}
    finally:
        root.removeHandler(handler)
    write()
    (work / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def status(work: Path) -> dict:
    try:
        st = json.loads((work / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "running", "stage": "voice", "progress": 0, "message": "Starting…", "log": []}
    st.pop("started", None)
    return st


def _shrink(video: Path) -> Path:
    """A copy of ``video`` under the asset limit (same size and frame rate)."""
    duration = media.duration(video)
    kbps = max(400, int(ASSET_LIMIT * 8 / 1000 / max(duration, 1) * 0.9) - 160)
    small = video.with_name(video.stem + "-panel.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-c:v", "libx264", "-preset", "medium",
                    "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(small)], check=True)
    return small


def outputs(work: Path) -> dict:
    res = json.loads((work / "result.json").read_text(encoding="utf-8"))
    if not res.get("ok"):
        return {"error": res.get("error")}
    r = res["result"]
    files: dict[str, str] = {}
    video = work / r["video"]
    if video.stat().st_size > ASSET_LIMIT:
        video = _shrink(video)
    files["video"] = str(video)
    if r.get("cover"):
        files["cover"] = str(work / r["cover"])
    for key, name in (("srt", "captions.srt.txt"), ("credits", "credits.txt"), ("timings", "timings.json")):
        if r.get(key) and (work / r[key]).is_file():
            src = work / r[key]
            dst = work / "panel-upload" / name  # .srt is not an accepted asset type: send it as text
            dst.parent.mkdir(exist_ok=True)
            dst.write_bytes(src.read_bytes())
            files[key] = str(dst)
    summary = {k: r.get(k) for k in ("duration", "warnings", "tempo", "method") if k in r}
    return {"files": files, "result": summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m reelforge.panelrun", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("doc")
    s.add_argument("--assets", required=True)
    s.add_argument("--work", required=True)
    for name in ("plan", "review", "status", "outputs"):
        sub.add_parser(name).add_argument("--work", required=True)
    r = sub.add_parser("render")
    r.add_argument("--work", required=True)
    r.add_argument("--voiceover")
    args = ap.parse_args(argv)
    work = Path(args.work).resolve()
    if args.cmd == "stage":
        out = stage(Path(args.doc), Path(args.assets), work)
    elif args.cmd == "plan":
        out = plan(work)
    elif args.cmd == "review":
        out = review(work)
    elif args.cmd == "render":
        out = render(work, args.voiceover)
    elif args.cmd == "status":
        out = status(work)
    else:
        out = outputs(work)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not (isinstance(out, dict) and out.get("ok") is False) else 1


if __name__ == "__main__":
    sys.exit(main())
