"""Local web server for the reelforge UI (``python -m reelforge ui``).

A FastAPI app over a folder of projects::

    <REELFORGE_HOME>/projects/<id>/project.json   the reel's settings
                                  /uploads/        the user's files
                                  /thumbs/         previews of the uploads
                                  /output/         the rendered reel
                                  /work/           the pipeline's scratch files

Paths inside ``project.json`` are relative to the project folder
(``uploads/song.mp3``), so a project folder can be moved or copied whole.

Rendering is slow (minutes), so ``POST /render`` only queues a job; one
worker thread renders one project at a time and the UI polls
``GET /api/jobs/{id}`` for stage, progress and the log. The heavy media
modules (``pipeline``, ``music``, ``usermedia``) are imported inside the
endpoints that need them: the server starts fast and the project screens keep
working even if an optional media tool is missing.
"""
from __future__ import annotations

import codecs
import collections
import json
import logging
import os
import queue
import re
import secrets
import shutil
import socket
import threading
import time
import webbrowser
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from . import __version__, config, fonts, schema, voiceover
from .config import FootageItem, Project, Settings
from .script import parse_script

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "ui" / "static"
DEFAULT_MAX_UPLOAD_MB = 500.0
ROLES = ("script", "voice", "music", "footage", "reference")
# Which kinds of file each upload role accepts, and how to say so.
ROLE_KINDS = {
    "script": ({"text"}, "a .txt or .md text file"),
    "voice": ({"audio", "video"}, "an audio file (or a video with sound)"),
    "music": ({"audio", "video"}, "an audio file (or a video with sound)"),
    "footage": ({"image", "video"}, "a picture or a video"),
    "reference": ({"image", "video"}, "a picture or a video"),
}
LOG_LINES = 200
JOB_HISTORY = 50
# ES modules must be served as JavaScript; some systems map .js to text/plain.
STATIC_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
                ".html": "text/html", ".json": "application/json", ".svg": "image/svg+xml",
                ".woff2": "font/woff2", ".webmanifest": "application/manifest+json"}


class NotFound(LookupError):
    """A project, job or file that does not exist (HTTP 404)."""


# --------------------------------------------------------------------------- paths

def safe_join(root: Path, relative: str) -> Path:
    """``root / relative`` if it stays inside ``root`` after resolving ``..``
    and symlinks; anything else is "not found" so nothing outside leaks."""
    root = root.resolve()
    try:
        path = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:  # symlink loops, NUL bytes
        raise NotFound(relative) from exc
    if path != root and not path.is_relative_to(root):
        raise NotFound(relative)
    return path


def safe_filename(name: str) -> str:
    """A file name that is safe on every OS: no folders, no leading dots, only
    letters, digits, ``.``, ``-`` and ``_``; at most 100 characters."""
    path = Path(Path(name.replace("\\", "/")).name.strip())
    stem = re.sub(r"[^\w\-]+", "_", path.stem).strip("._-")[:90] or "file"
    ext = re.sub(r"[^\w]+", "", path.suffix)[:10].lower()
    return f"{stem}.{ext}" if ext else stem


def unique_path(directory: Path, name: str) -> Path:
    """``directory/name``, or ``name-2``, ``name-3`` ... if that is taken."""
    path = directory / name
    stem, suffix = path.stem, path.suffix
    n = 2
    while path.exists():
        path = directory / f"{stem}-{n}{suffix}"
        n += 1
    return path


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())[:5])[:40] or "reel"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- media helpers

@lru_cache(maxsize=1024)
def _media_info(path: str, size: int, mtime_ns: int) -> dict:
    """``usermedia.media_info`` cached per file version (size, mtime): the
    project view lists every upload on each autosave."""
    from . import usermedia

    try:
        return usermedia.media_info(path)
    except Exception as exc:  # unreadable file: show it as unknown, never fail the listing
        log.warning("cannot read %s (%s)", path, exc)
        return {"kind": "unknown", "duration": None, "width": None, "height": None}


def media_info(path: Path) -> dict:
    st = path.stat()
    return dict(_media_info(str(path), st.st_size, st.st_mtime_ns))


_THUMB_FAILED: set[tuple[str, int]] = set()


def make_thumb(src: Path, dst: Path) -> Path | None:
    """JPEG preview of a picture or video (kept until the source changes);
    None when the file has no picture or cannot be decoded."""
    from . import usermedia

    mtime = src.stat().st_mtime_ns
    if dst.is_file() and dst.stat().st_mtime_ns >= mtime:
        return dst
    if (str(src), mtime) in _THUMB_FAILED:
        return None
    try:
        return usermedia.thumbnail(src, dst, max_side=480)
    except Exception as exc:
        log.warning("no thumbnail for %s (%s)", src.name, exc)
        _THUMB_FAILED.add((str(src), mtime))
        return None


def waveform(path: Path, buckets: int) -> dict:
    """``{"duration", "peaks"}`` for drawing an audio file (cached by music)."""
    from . import music

    return music.waveform_peaks(path, buckets)


def read_text(path: Path) -> str:
    """A script file as text, whatever editor wrote it: UTF-8, UTF-16 with a
    byte-order mark (Notepad "Unicode", PowerShell 5) or Windows-1252.
    Raises ValueError for anything that is not plain text."""
    data = path.read_bytes()
    text = None
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    else:
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    if text is None or "\x00" in text:
        raise ValueError(f"“{path.name}” is not a plain-text file: save the script as text (UTF-8).")
    return text


# --------------------------------------------------------------------------- workspace

class Workspace:
    """The project folders under ``root`` (``<home>/projects``)."""

    ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

    def __init__(self, root: Path):
        self.root = Path(root)
        self._locks: dict[str, threading.RLock] = collections.defaultdict(threading.RLock)
        self._locks_guard = threading.Lock()

    @contextmanager
    def lock(self, pid: str) -> Iterator[None]:
        """Serialise read-modify-write of one project (parallel uploads)."""
        with self._locks_guard:
            lock = self._locks[pid]
        with lock:
            yield

    def dir(self, pid: str) -> Path:
        if not self.ID.match(pid) or not (self.root / pid / "project.json").is_file():
            raise NotFound(f"project {pid}")
        return self.root / pid

    def load(self, pid: str) -> Project:
        path = self.dir(pid) / "project.json"
        try:
            return config.load_project(path)
        except ValueError as exc:
            raise ValueError(f"The project file {path} is damaged ({exc}).") from exc

    def save(self, pid: str, project: Project) -> None:
        path = self.root / pid / "project.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(config.to_dict(project), indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        tmp.replace(path)  # atomic: a crash never leaves half a project file

    def create(self, project: Project) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            pid = f"{_slug(project.name if project.name != 'untitled' else 'reel')}-{secrets.token_hex(3)}"
            try:
                (self.root / pid).mkdir()
                break
            except FileExistsError:
                continue
        for sub in ("uploads", "output"):
            (self.root / pid / sub).mkdir()
        self.save(pid, project)
        return pid

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return [p.name for p in self.root.iterdir()
                if self.ID.match(p.name) and (p / "project.json").is_file()]

    def updated(self, pid: str) -> float:
        return (self.root / pid / "project.json").stat().st_mtime

    def duplicate(self, pid: str) -> str:
        """Copy settings, uploads and thumbnails (not renders or scratch files)."""
        src = self.dir(pid)
        project = self.load(pid)
        project.name = f"{project.name} (copy)"
        new = self.create(project)
        for sub in ("uploads", "thumbs"):
            if (src / sub).is_dir():
                shutil.copytree(src / sub, self.root / new / sub, dirs_exist_ok=True)
        return new

    def delete(self, pid: str) -> None:
        shutil.rmtree(self.dir(pid))


# --------------------------------------------------------------------------- render jobs

@dataclass
class Job:
    id: str
    project_id: str
    run: Callable[["Job"], dict]
    status: str = "queued"          # queued | running | done | error | cancelled
    stage: str = ""
    progress: float = 0.0
    message: str = "Waiting for another render to finish"
    result: dict | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    log: collections.deque = field(default_factory=lambda: collections.deque(maxlen=LOG_LINES))
    _log_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def add_log(self, text: str) -> None:
        with self._log_lock:
            self.log.extend(line.rstrip() for line in str(text).splitlines() if line.strip())

    def report(self, stage: str, fraction: float, message: str) -> None:
        """The pipeline's progress callback."""
        self.stage = stage
        self.progress = min(1.0, max(0.0, float(fraction)))
        if message:
            self.message = message

    def to_dict(self, position: int | None = None) -> dict:
        with self._log_lock:
            lines = list(self.log)
        return {"id": self.id, "project_id": self.project_id, "status": self.status,
                "stage": self.stage, "progress": round(self.progress, 4), "message": self.message,
                "log": lines, "result": self.result, "error": self.error, "position": position,
                "created": _iso(self.created),
                "started": _iso(self.started) if self.started else None,
                "finished": _iso(self.finished) if self.finished else None}


class _JobLog(logging.Handler):
    """Copies the pipeline's log records into the running job, so the UI can
    show what is happening. Only one job runs at a time; the server's own
    records (request handling) are left out."""

    def __init__(self, job: Job):
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:
            return
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            msg = f"{record.levelname.lower()}: {msg}"
        self.job.add_log(msg)


class JobQueue:
    """One render at a time, in order; the rest wait in the queue."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def submit(self, project_id: str, run: Callable[[Job], dict]) -> Job:
        job = Job(id=secrets.token_hex(6), project_id=project_id, run=run)
        with self._lock:
            self._prune()
            self._jobs[job.id] = job
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._loop, name="reelforge-render", daemon=True)
                self._worker.start()
        self._queue.put(job)
        return job

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFound(f"job {job_id}")
        return job

    def position(self, job: Job) -> int | None:
        """1 = next in line; None unless queued."""
        waiting = sorted((j for j in self._snapshot() if j.status == "queued"), key=lambda j: j.created)
        return 1 + waiting.index(job) if job in waiting else None

    def view(self, job: Job) -> dict:
        return job.to_dict(self.position(job))

    def latest(self, project_id: str) -> Job | None:
        jobs = [j for j in self._snapshot() if j.project_id == project_id]
        return max(jobs, key=lambda j: j.created) if jobs else None

    def _snapshot(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def cancel(self, job: Job) -> Job:
        """A queued job never starts; a running one stops at the pipeline's
        next progress report."""
        job.cancel.set()
        if job.status == "queued":
            self._finish(job, "cancelled", "Cancelled before it started")
        elif job.status == "running":
            job.message = "Cancelling..."
        return job

    def cancel_project(self, project_id: str) -> None:
        for job in self._snapshot():
            if job.project_id == project_id and job.active:
                self.cancel(job)

    def shutdown(self) -> None:
        for job in self._snapshot():
            if job.active:
                self.cancel(job)
        self._queue.put(None)

    def _prune(self) -> None:
        done = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.created)
        for job in done[:max(0, len(self._jobs) - JOB_HISTORY)]:
            del self._jobs[job.id]

    @staticmethod
    def _finish(job: Job, status: str, message: str) -> None:
        job.status, job.message, job.finished = status, message, time.time()

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            if job.status == "queued":
                self._execute(job)

    def _execute(self, job: Job) -> None:
        job.status, job.started, job.message = "running", time.time(), "Starting"
        logger = logging.getLogger("reelforge")
        handler, level = _JobLog(job), logger.level
        logger.addHandler(handler)
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)
        try:
            job.result = job.run(job)
            job.progress = 1.0
            self._finish(job, "done", "Finished")
        except Exception as exc:
            if job.cancel.is_set():  # pipeline.Cancelled, or a step torn down by the cancel
                self._finish(job, "cancelled", "Render cancelled")
                job.add_log("render cancelled")
                return
            # shown to the user; the traceback goes to the console
            log.exception("render of %s failed", job.project_id)
            job.error = str(exc) or type(exc).__name__
            self._finish(job, "error", job.error.splitlines()[0])
            job.add_log(f"error: {job.error}")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)


# --------------------------------------------------------------------------- app state

@dataclass
class Context:
    settings: Settings
    workspace: Workspace
    jobs: JobQueue
    static_dir: Path
    max_upload_bytes: int

    # ---- urls and views -------------------------------------------------------------

    @staticmethod
    def file_url(pid: str, rel: str, versioned: Path | None = None) -> str:
        url = f"/api/projects/{pid}/files/{quote(rel)}"
        if versioned is not None and versioned.exists():
            url += f"?v={versioned.stat().st_mtime_ns // 1_000_000}"
        return url

    def outputs(self, pid: str) -> dict:
        """URLs of the last render's files (None where missing). The pipeline's
        result is stored in ``output/render.json``; a folder rendered by the
        command line falls back to its newest MP4."""
        pdir = self.workspace.root / pid
        out = pdir / "output"
        saved: dict = {}
        if (out / "render.json").is_file():
            try:
                saved = json.loads((out / "render.json").read_text(encoding="utf-8"))
            except ValueError:
                saved = {}
        videos = sorted(out.glob("*.mp4"), key=lambda p: p.stat().st_mtime) if out.is_dir() else []
        rel = {"video": saved.get("video") or (f"output/{videos[-1].name}" if videos else None),
               "srt": saved.get("srt") or "output/captions.srt",
               "cover": saved.get("cover") or "output/cover.jpg",
               "credits": saved.get("credits") or "output/credits.txt",
               "timings": saved.get("timings") or "output/timings.json"}
        urls = {}
        for key, path in rel.items():
            target = pdir / path if path else None
            urls[key] = self.file_url(pid, path, target) if target and target.is_file() else None
        return urls

    def upload_role(self, project: Project, rel: str, kind: str) -> str | None:
        """What an uploaded file is used for in the project right now."""
        if project.voice.file == rel:
            return "voice"
        if project.music.file == rel:
            return "music"
        for item in project.footage.items:
            if item.path == rel:
                return item.role
        return "script" if kind == "text" else None

    def file_view(self, pid: str, project: Project, path: Path, role: str | None = None) -> dict:
        pdir = self.workspace.root / pid
        rel = f"uploads/{path.name}"
        info = media_info(path)
        thumb = None
        if info["kind"] in ("image", "video"):
            thumb = make_thumb(path, pdir / "thumbs" / f"{path.name}.jpg")
        return {"name": path.name, "url": self.file_url(pid, rel), "path": rel,
                "role": role or self.upload_role(project, rel, info["kind"]),
                "kind": info["kind"], "info": info, "size": path.stat().st_size,
                "uploaded": _iso(path.stat().st_mtime),
                "thumb_url": self.file_url(pid, f"thumbs/{thumb.name}", thumb) if thumb else None}

    def uploads(self, pid: str, project: Project) -> list[dict]:
        updir = self.workspace.root / pid / "uploads"
        files = [p for p in updir.iterdir() if p.is_file() and not p.name.startswith(".")] \
            if updir.is_dir() else []
        return [self.file_view(pid, project, p) for p in sorted(files, key=lambda p: p.stat().st_mtime)]

    def project_view(self, pid: str, project: Project | None = None) -> dict:
        project = project or self.workspace.load(pid)
        job = self.jobs.latest(pid)
        return {"id": pid, "project": config.to_dict(project), "uploads": self.uploads(pid, project),
                "outputs": self.outputs(pid),
                "job": self.jobs.view(job) if job and job.active else None}

    def summary(self, pid: str) -> dict:
        project = self.workspace.load(pid)
        outputs = self.outputs(pid)
        thumb = outputs["cover"]
        if thumb is None:
            pdir = self.workspace.root / pid
            for item in project.footage.items:
                candidate = pdir / "thumbs" / f"{Path(item.path).name}.jpg"
                if candidate.is_file():
                    thumb = self.file_url(pid, f"thumbs/{candidate.name}", candidate)
                    break
        return {"id": pid, "name": project.name, "updated": _iso(self.workspace.updated(pid)),
                "has_video": outputs["video"] is not None, "thumb": thumb}


def _ctx(request: Request) -> Context:
    return request.app.state.ctx


Ctx = Annotated[Context, Depends(_ctx)]
ProjectBody = Annotated[dict[str, Any], Body()]
OptionalProjectBody = Annotated[dict[str, Any] | None, Body()]


# --------------------------------------------------------------------------- reference data

api = APIRouter(prefix="/api")


def capabilities(settings: Settings) -> dict:
    return {"whisper": find_spec("faster_whisper") is not None,
            "piper": find_spec("piper") is not None,
            "pexels": bool(settings.pexels_api_key),
            "pixabay": bool(settings.pixabay_api_key),
            "higgsfield_api": bool(settings.higgsfield_key and settings.higgsfield_tts_endpoint),
            "ffmpeg": shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None}


@api.get("/meta")
def meta(ctx: Ctx) -> dict:
    """Choices for the UI's drop-downs and what this machine can do."""
    return {
        "version": __version__,
        "presets": schema.options(schema.PRESET_OPTIONS),
        "aspects": [{"id": a, "label": schema.ASPECT_LABELS.get(a, a), "width": w, "height": h}
                    for a, (w, h) in config.ASPECTS.items()],
        "fonts": [{"id": v, "label": label, "weights": list(fonts.WEIGHTS)} for v, label in schema.FONTS],
        "font_weights": list(fonts.WEIGHTS),
        "voice_sources": schema.options(schema.VOICE_SOURCES),
        "voice_presets": schema.options(schema.VOICE_PRESETS),
        "palettes": schema.options(schema.PALETTES),
        "hook_modes": schema.options(schema.HOOK_MODES),
        "caption_cases": schema.options(schema.CASES),
        "stock_sources": schema.options(schema.STOCK_SOURCES),
        "motions": schema.options(schema.MOTIONS),
        "roles": list(ROLES),
        "max_upload_mb": round(ctx.max_upload_bytes / 1e6),
        "capabilities": capabilities(ctx.settings),
    }


@api.get("/schema")
def get_schema() -> list[dict]:
    """Form metadata: a list of sections ``{id, label, help, fields: [...]}``
    (see ``schema.py``)."""
    return schema.sections()


@api.get("/defaults")
def get_defaults(ctx: Ctx) -> dict:
    return config.to_dict(config.load_defaults(ctx.settings))


@api.put("/defaults")
def put_defaults(data: ProjectBody, ctx: Ctx) -> dict:
    config.save_defaults(schema.validate(data), ctx.settings)
    return config.to_dict(config.load_defaults(ctx.settings))


@api.delete("/defaults")
def delete_defaults(ctx: Ctx) -> dict:
    config.reset_defaults(ctx.settings)
    return config.to_dict(config.load_defaults(ctx.settings))


@api.get("/presets/{name}")
def get_preset(name: str) -> dict:
    if name not in config.PRESETS:
        raise NotFound(f"preset {name}")
    return config.preset_dict(name)


# --------------------------------------------------------------------------- projects

class NewProject(BaseModel):
    name: str | None = None
    preset: str | None = None


@api.get("/projects")
def list_projects(ctx: Ctx) -> list[dict]:
    """Newest first. Damaged project folders are skipped (and logged)."""
    rows = []
    for pid in sorted(ctx.workspace.ids(), key=ctx.workspace.updated, reverse=True):
        try:
            rows.append(ctx.summary(pid))
        except (ValueError, OSError) as exc:
            log.warning("skipping project %s: %s", pid, exc)
    return rows


@api.post("/projects")
def create_project(ctx: Ctx, body: NewProject | None = None) -> dict:
    """A new project from the user's saved defaults, or from a preset."""
    body = body or NewProject()
    if body.preset:
        if body.preset not in config.PRESETS:
            raise HTTPException(422, f"Unknown preset {body.preset!r}.")
        project = config.from_dict(Project, config.preset_dict(body.preset))
    else:
        project = config.load_defaults(ctx.settings)
    project.name = (body.name or "").strip() or "untitled"
    pid = ctx.workspace.create(project)
    return ctx.project_view(pid, project)


@api.get("/projects/{pid}")
def get_project(pid: str, ctx: Ctx) -> dict:
    return ctx.project_view(pid)


@api.put("/projects/{pid}")
def put_project(pid: str, data: ProjectBody, ctx: Ctx) -> dict:
    ctx.workspace.dir(pid)
    project = schema.validate(data)
    with ctx.workspace.lock(pid):
        ctx.workspace.save(pid, project)
    return ctx.project_view(pid, project)


@api.delete("/projects/{pid}")
def delete_project(pid: str, ctx: Ctx) -> dict:
    ctx.jobs.cancel_project(pid)
    with ctx.workspace.lock(pid):
        ctx.workspace.delete(pid)
    return {"ok": True}


@api.post("/projects/{pid}/duplicate")
def duplicate_project(pid: str, ctx: Ctx) -> dict:
    return ctx.project_view(ctx.workspace.duplicate(pid))


# --------------------------------------------------------------------------- files

@api.post("/projects/{pid}/upload")
async def upload(pid: str, request: Request, ctx: Ctx) -> dict:
    """Multipart ``file`` + ``role`` (script | voice | music | footage |
    reference). Too-large uploads are refused from the Content-Length header
    before the body is read."""
    ctx.workspace.dir(pid)
    limit = ctx.max_upload_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit + 64 * 1024:  # + multipart overhead
        raise HTTPException(413, _too_big(limit))
    form = await request.form(max_files=1)
    try:
        file, role = form.get("file"), str(form.get("role") or "")
        if file is None or isinstance(file, str):
            raise HTTPException(422, "No file in the upload (form field 'file').")
        if role not in ROLES:
            raise HTTPException(422, f"Upload role must be one of {', '.join(ROLES)}.")
        return await run_in_threadpool(_store_upload, ctx, pid, file.file, file.filename or "upload", role)
    finally:
        await form.close()


def _too_big(limit: int) -> str:
    return f"The file is too big: the limit is {limit / 1e6:.0f} MB."


def _store_upload(ctx: Context, pid: str, stream, filename: str, role: str) -> dict:
    pdir = ctx.workspace.dir(pid)
    updir = pdir / "uploads"
    updir.mkdir(exist_ok=True)
    with ctx.workspace.lock(pid):
        path = unique_path(updir, safe_filename(filename))
        _copy_limited(stream, path, ctx.max_upload_bytes)
        info = media_info(path)
        kinds, wanted = ROLE_KINDS[role]
        if info["kind"] not in kinds:
            path.unlink()
            raise HTTPException(415, f"“{filename}” can't be used as {role}: please upload {wanted}.")
        if role == "script":
            try:
                read_text(path)
            except ValueError as exc:  # never replace the script with garbage
                path.unlink()
                raise HTTPException(415, str(exc)) from None
        project = ctx.workspace.load(pid)
        rel = f"uploads/{path.name}"
        _attach(project, rel, role, info["kind"], path)
        ctx.workspace.save(pid, project)
    view = ctx.file_view(pid, project, path, role)
    view["peaks"] = None
    if role in ("voice", "music"):
        try:
            view["peaks"] = waveform(path, 1000)
        except Exception as exc:  # the upload itself worked; the UI can retry /waveform
            log.warning("no waveform for %s (%s)", path.name, exc)
    return {"project": config.to_dict(project), "file": view}


def _copy_limited(stream, dst: Path, limit: int) -> None:
    written = 0
    try:
        with open(dst, "wb") as out:
            while chunk := stream.read(1 << 20):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, _too_big(limit))
                out.write(chunk)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    if written == 0:
        dst.unlink()
        raise HTTPException(422, "The uploaded file is empty.")


def _attach(project: Project, rel: str, role: str, kind: str, path: Path) -> None:
    """Point the project at a new upload."""
    if role == "script":
        project.script = read_text(path).strip()
        if project.name in ("", "untitled"):
            project.name = path.stem.replace("_", " ")
    elif role == "voice":
        project.voice.file, project.voice.source = rel, "file"
    elif role == "music":
        project.music.file, project.music.source_in, project.music.source_out = rel, 0.0, None
    else:
        project.footage.items.append(FootageItem(path=rel, role=role, kind=kind))


@api.delete("/projects/{pid}/uploads/{name}")
def delete_upload(pid: str, name: str, ctx: Ctx) -> dict:
    """Remove an upload and every reference to it (a script's text stays)."""
    pdir = ctx.workspace.dir(pid)
    if name != Path(name).name or name.startswith("."):
        raise NotFound(name)
    path = safe_join(pdir / "uploads", name)
    rel = f"uploads/{name}"
    # idempotent: a second click (or a file removed by hand) still cleans the references
    with ctx.workspace.lock(pid):
        project = ctx.workspace.load(pid)
        if project.voice.file == rel:
            project.voice.file = None
        if project.music.file == rel:
            project.music.file, project.music.source_in, project.music.source_out = None, 0.0, None
        project.footage.items = [i for i in project.footage.items if i.path != rel]
        ctx.workspace.save(pid, project)
        path.unlink(missing_ok=True)
        (pdir / "thumbs" / f"{name}.jpg").unlink(missing_ok=True)
    return ctx.project_view(pid, project)


@api.get("/projects/{pid}/files/{path:path}")
def get_file(pid: str, path: str, ctx: Ctx) -> FileResponse:
    """Any file inside the project folder. Range requests work (seeking in
    the audio and video players); nothing outside the folder is served."""
    target = safe_join(ctx.workspace.dir(pid), path)
    if not target.is_file():
        raise NotFound(path)
    return FileResponse(target, media_type=STATIC_TYPES.get(target.suffix.lower()))


api.add_api_route("/projects/{pid}/files/{path:path}", get_file, methods=["HEAD"],
                  include_in_schema=False)  # media players probe with HEAD


@api.get("/projects/{pid}/waveform")
def get_waveform(pid: str, ctx: Ctx, file: str = Query(...),
                 buckets: int = Query(1000, ge=10, le=10000)) -> dict:
    """Peaks for drawing the music waveform: ``{"duration", "peaks": [0..1]}``."""
    target = safe_join(ctx.workspace.dir(pid), file)
    if not target.is_file():
        raise NotFound(file)
    try:
        return waveform(target, buckets)
    except Exception as exc:
        raise HTTPException(422, f"Cannot read the audio of {target.name}: {exc}") from exc


# --------------------------------------------------------------------------- plan and render

def _apply_body(ctx: Context, pid: str, data: dict | None) -> Project:
    """Save the UI's latest state first when it is sent along, so a plan or
    render never uses settings from before the last autosave."""
    if data is None:
        return ctx.workspace.load(pid)
    project = schema.validate(data)
    with ctx.workspace.lock(pid):
        ctx.workspace.save(pid, project)
    return project


@api.post("/projects/{pid}/plan")
def plan(pid: str, ctx: Ctx, data: OptionalProjectBody = None) -> dict:
    from . import pipeline

    project_dir = ctx.workspace.dir(pid)
    project = _apply_body(ctx, pid, data)
    try:
        return pipeline.plan_project(project, project_dir, ctx.settings)
    except ValueError:
        raise  # a problem with the project the user can fix: 422 via the app's handler
    except Exception as exc:
        log.exception("planning %s failed", pid)
        raise HTTPException(500, f"Planning failed: {exc}") from exc


@api.post("/projects/{pid}/render")
def render(pid: str, ctx: Ctx, data: OptionalProjectBody = None) -> dict:
    """Queue a render of the project as it is now; poll ``/api/jobs/{job_id}``."""
    project_dir = ctx.workspace.dir(pid)
    project = _apply_body(ctx, pid, data)

    def run(job: Job) -> dict:
        from . import pipeline

        job.add_log(f"rendering “{project.name}”")
        result = pipeline.render_project(project, project_dir, ctx.settings,
                                         progress=job.report, cancel=job.cancel)
        out = project_dir / "output"
        out.mkdir(exist_ok=True)
        (out / "render.json").write_text(json.dumps(result | {"finished": _iso(time.time())},
                                                    indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
        job.add_log(f"finished: {result.get('video')}")
        return result | {"outputs": ctx.outputs(pid)}

    job = ctx.jobs.submit(pid, run)
    return {"job_id": job.id, "status": job.status}


@api.get("/jobs/{job_id}")
def get_job(job_id: str, ctx: Ctx) -> dict:
    return ctx.jobs.view(ctx.jobs.get(job_id))


@api.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, ctx: Ctx) -> dict:
    return ctx.jobs.view(ctx.jobs.cancel(ctx.jobs.get(job_id)))


@api.get("/projects/{pid}/higgsfield-request")
def higgsfield_request(pid: str, ctx: Ctx) -> dict:
    """The ``generate_audio`` call Claude makes with the user's Higgsfield
    account to voice this script."""
    project = ctx.workspace.load(pid)
    script = parse_script(project.script, project.auto_emphasis)
    if not script.words:
        raise HTTPException(422, "Write or upload a script first.")
    return voiceover.higgsfield_request(script, project.voice.higgsfield_preset,
                                        project.voice.voice_id or ctx.settings.higgsfield_voice_id)


# --------------------------------------------------------------------------- UI files

ui = APIRouter()

PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>reelforge</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111318;
         color: #e8e8ec; font: 16px/1.5 system-ui, sans-serif; }
  main { max-width: 34rem; padding: 2rem; }
  code { background: #23262e; padding: .1rem .35rem; border-radius: 4px; }
  a { color: #8ab4ff; }
</style></head>
<body><main>
  <h1>reelforge is running</h1>
  <p>The web interface files are missing: <code>reelforge/ui/static/index.html</code> was not found.
     Reinstall reelforge, or update your copy of the project.</p>
  <p>The server itself works: the API is documented at <a href="/docs">/docs</a>.</p>
</main></body></html>
"""


def _static(ctx: Context, path: str) -> FileResponse:
    target = safe_join(ctx.static_dir, path)
    if not target.is_file():
        raise NotFound(path)
    # no-cache: the browser revalidates, so an updated UI shows up on reload
    return FileResponse(target, media_type=STATIC_TYPES.get(target.suffix.lower()),
                        headers={"Cache-Control": "no-cache"})


@ui.get("/", response_model=None)
def index(ctx: Ctx) -> Response:
    if (ctx.static_dir / "index.html").is_file():
        return _static(ctx, "index.html")
    return HTMLResponse(PLACEHOLDER)


@ui.get("/static/{path:path}")
def static_file(path: str, ctx: Ctx) -> FileResponse:
    return _static(ctx, path)


@ui.get("/{path:path}", include_in_schema=False)
def static_root(path: str, ctx: Ctx) -> FileResponse:
    """UI files referenced relative to ``/`` (``app.js``, ``favicon.svg``)."""
    if path.startswith("api/"):
        raise NotFound(path)
    return _static(ctx, path)


# --------------------------------------------------------------------------- app

class SameOriginOnly:
    """Refuse state-changing requests made by other web sites (CSRF). Any page
    the user visits can make the browser POST a form to localhost, but it
    cannot make the ``Origin`` header match this server."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            headers = dict(scope["headers"])
            origin = headers.get(b"origin")
            if origin is not None and \
                    urlsplit(origin.decode("latin-1")).netloc != headers.get(b"host", b"").decode("latin-1"):
                response = JSONResponse({"detail": "Request from another web site refused."}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, *, max_upload_mb: float | None = None,
               static_dir: Path | None = None, allowed_hosts: list[str] | None = None) -> FastAPI:
    """The web app. ``settings`` default to the environment (``REELFORGE_HOME``
    etc.); the upload limit to ``REELFORGE_MAX_UPLOAD_MB`` or 500 MB.
    ``allowed_hosts`` limits the ``Host`` header (blocks DNS rebinding when
    the server only listens on this computer)."""
    settings = settings or Settings.from_env()
    if max_upload_mb is None:
        max_upload_mb = float(os.getenv("REELFORGE_MAX_UPLOAD_MB") or DEFAULT_MAX_UPLOAD_MB)
    ctx = Context(settings=settings, workspace=Workspace(Path(settings.home_dir) / "projects"),
                  jobs=JobQueue(), static_dir=Path(static_dir or STATIC_DIR),
                  max_upload_bytes=int(max_upload_mb * 1e6))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        ctx.jobs.shutdown()

    app = FastAPI(title="reelforge", version=__version__, lifespan=lifespan)
    app.state.ctx = ctx

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": f"Not found: {exc}"}, status_code=404)

    @app.exception_handler(ValueError)  # invalid settings, damaged project files: the user can fix these
    async def bad_value(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    app.include_router(api)
    app.include_router(ui)
    app.add_middleware(SameOriginOnly)
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    return app


def _free_port(host: str, port: int, tries: int = 20) -> int:
    """``port``, or the next free one (another reelforge may be running)."""
    for candidate in range(port, port + tries):
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
            try:
                s.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise OSError(f"no free port between {port} and {port + tries - 1}")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Run the UI server until Ctrl+C, opening it in the browser."""
    import uvicorn

    local = host in ("127.0.0.1", "localhost")
    app = create_app(allowed_hosts=["127.0.0.1", "localhost"] if local else None)
    chosen = _free_port(host, port)
    if chosen != port:
        log.warning("port %d is busy, using %d", port, chosen)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{'[' + shown + ']' if ':' in shown else shown}:{chosen}/"
    print(f"reelforge is running at {url}  (press Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(app, host=host, port=chosen, log_level="warning")
