"""Web server and settings schema. The media modules (pipeline, music,
usermedia) are replaced by small fakes, so these tests need no FFmpeg, no
network and run in a few seconds."""
import dataclasses
import io
import socket
import sys
import threading
import time
import types
import typing
import warnings

import pytest
from PIL import Image

import reelforge
from reelforge import config, schema, server
from reelforge.config import FootageItem, Project, Settings

with warnings.catch_warnings():  # starlette nags about its httpx backend on import
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- schema

def config_paths(cls=Project, prefix=""):
    """Every leaf setting of ``cls`` as a schema path (see schema.py's docstring)."""
    hints = typing.get_type_hints(cls)
    for f in dataclasses.fields(cls):
        path, tp = prefix + f.name, hints[f.name]
        args = typing.get_args(tp)
        if dataclasses.is_dataclass(tp):
            yield from config_paths(tp, path + ".")
        elif typing.get_origin(tp) is list and args and dataclasses.is_dataclass(args[0]):
            yield path
            yield from config_paths(args[0], path + "[].")
        elif typing.get_origin(tp) is tuple and tp != config.RGB:
            yield from (f"{path}.{i}" for i in range(len(args)))
        else:
            yield path


def default_of(path):
    obj = FootageItem(path="uploads/a.jpg") if path.startswith(schema.ITEM) else Project()
    for key in path.removeprefix(schema.ITEM).split("."):
        obj = obj[int(key)] if key.isdigit() else getattr(obj, key)
    return obj


def test_schema_describes_every_config_field_and_nothing_else():
    paths = list(config_paths())
    assert {"style.video.draft", "voice.align", "style.caption.shadow_offset.1",
            "footage.items[].motion"} <= set(paths)
    missing = [p for p in paths if p not in schema.FIELDS]
    stale = [p for p in schema.FIELDS if p not in paths]
    assert not missing, f"config fields without schema metadata: {missing}"
    assert not stale, f"schema entries for fields that do not exist: {stale}"
    assert len(paths) == len(schema.FIELDS)


@pytest.mark.parametrize("path", list(schema.FIELDS))
def test_every_schema_field_is_complete_and_fits_its_default(path):
    f = schema.FIELDS[path]
    assert f.label.strip() and len(f.help) > 10 and f.type in schema.TYPES
    default = default_of(path)
    if f.type in ("select", "multiselect"):
        assert f.options, path
        values = [v for v, _ in f.options]
        if f.type == "multiselect":
            assert set(default) <= set(values)
        elif not f.custom:
            assert default in values, f"{path}: default {default!r} not in options"
    if f.type in ("number", "int", "optional-number"):
        assert f.min is not None and f.max is not None and f.min < f.max and f.step
        if default is not None:
            assert f.min <= default <= f.max, f"{path}: default {default} outside {f.min}..{f.max}"


def test_validate_accepts_presets_and_reports_bad_values_by_label():
    for name in config.PRESETS:
        assert schema.validate(config.preset_dict(name)).preset == name
    p = schema.validate({"style": {"caption": {"text_color": "#ff0000"}}})
    assert p.style.caption.text_color == (255, 0, 0) and p.style.caption.case == "lower"
    with pytest.raises(ValueError) as err:
        schema.validate({"style": {"caption": {"font_size": 999, "case": "shout"}},
                         "footage": {"items": [{"path": "uploads/a.jpg", "role": "maybe"}],
                                     "sources": ["mixkit", "youtube"]}})
    msg = str(err.value)
    assert "Captions › Text size: must be between 24 and 200 (got 999)" in msg
    assert "Letter case" in msg and "Clip 1: Uploaded clip › Use" in msg and "youtube" in msg
    with pytest.raises(ValueError, match="preset"):
        schema.validate({"preset": "nope"})
    # custom selects take values outside their options (a font file, a hex colour)
    custom = schema.validate({"style": {"hook": {"color": "#123456", "font": "/f.ttf"}}})
    assert custom.style.hook.color == "#123456" and custom.style.hook.font == "/f.ttf"
    with pytest.raises(ValueError, match="Hook › Text colour"):
        schema.validate({"style": {"hook": {"color": "dark-ish"}}})


def test_sections_are_json_ready():
    data = schema.sections()
    assert [s["id"] for s in data][:8] == ["caption", "hook", "video", "look", "music", "duration",
                                           "footage", "voice"]
    field = next(f for f in data[0]["fields"] if f["path"] == "style.caption.case")
    assert field["type"] == "select" and {"value": "lower", "label": "lowercase"} in field["options"]
    assert set(field) >= {"path", "label", "help", "type", "min", "max", "step", "options", "unit",
                          "advanced"}


# --------------------------------------------------------------------------- fakes

KINDS = {".txt": "text", ".md": "text", ".mp3": "audio", ".wav": "audio", ".mp4": "video",
         ".jpg": "image", ".png": "image"}


class FakePipeline(types.ModuleType):
    """Stands in for reelforge.pipeline; tests steer it through attributes."""

    class Cancelled(RuntimeError):
        pass

    def __init__(self):
        super().__init__("reelforge.pipeline")
        self.gate: threading.Event | None = None   # render waits here (to test queueing)
        self.fail: Exception | None = None
        self.rendered: list[str] = []

    def plan_project(self, project, project_dir, settings):
        if not project.script:
            raise ValueError("Write or upload a script first.")
        return {"captions": project.script.lower().split(), "hook": [], "estimated_seconds": 4.2,
                "target_seconds": project.duration.target, "shots": [], "queries": ["mountains"],
                "higgsfield_request": None, "warnings": [], "hook_preview": None}

    def render_project(self, project, project_dir, settings, progress=None, cancel=None):
        import logging

        logging.getLogger("reelforge.pipeline").info("[voice] preparing the voiceover")
        progress("voice", 0.05, "preparing the voiceover")
        if self.gate is not None:
            while not self.gate.wait(0.01):
                if cancel.is_set():
                    raise self.Cancelled("render cancelled")
        if self.fail is not None:
            raise self.fail
        progress("render", 0.8, "rendering video")
        out = project_dir / "output"
        out.mkdir(exist_ok=True)
        (out / "reel.mp4").write_bytes(b"\0" * 2048)
        (out / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        Image.new("RGB", (9, 16)).save(out / "cover.jpg")
        (out / "timings.json").write_text("{}")
        self.rendered.append(project.name)
        return {"video": "output/reel.mp4", "srt": "output/captions.srt", "cover": "output/cover.jpg",
                "credits": None, "timings": "output/timings.json", "duration": 4.2, "warnings": []}


def fake_usermedia():
    mod = types.ModuleType("reelforge.usermedia")

    def media_info(path):
        kind = KINDS.get(str(path)[str(path).rfind("."):].lower(), "unknown")
        return {"kind": kind, "duration": 2.0 if kind in ("audio", "video") else None,
                "width": 64 if kind in ("image", "video") else None,
                "height": 36 if kind in ("image", "video") else None}

    def thumbnail(path, dst, max_side=480, t=None):
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 18), (90, 140, 200)).save(dst, "JPEG")
        return dst

    mod.media_info, mod.thumbnail = media_info, thumbnail
    return mod


def fake_music():
    mod = types.ModuleType("reelforge.music")
    mod.waveform_peaks = lambda path, buckets=1000: {"duration": 2.0, "peaks": [0.5] * buckets}
    return mod


@pytest.fixture
def pipeline(monkeypatch):
    fakes = {"pipeline": FakePipeline(), "usermedia": fake_usermedia(), "music": fake_music()}
    for name, mod in fakes.items():
        monkeypatch.setitem(sys.modules, f"reelforge.{name}", mod)
        monkeypatch.setattr(reelforge, name, mod, raising=False)
    server._media_info.cache_clear()
    return fakes["pipeline"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REELFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REELFORGE_CACHE", str(tmp_path / "cache"))
    return tmp_path / "home"


@pytest.fixture
def client(home, tmp_path, pipeline):
    app = server.create_app(Settings.from_env(), max_upload_mb=1, static_dir=tmp_path / "static")
    with TestClient(app) as c:
        yield c


def new_project(client, **body):
    r = client.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def upload(client, pid, name, data, role):
    return client.post(f"/api/projects/{pid}/upload", data={"role": role},
                       files={"file": (name, io.BytesIO(data))})


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (200, 180, 90)).save(buf, "JPEG")
    return buf.getvalue()


def wait_job(client, job_id, until=("done", "error", "cancelled"), timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in until:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} stuck in {job['status']}")


# --------------------------------------------------------------------------- reference data

def test_meta_lists_choices_and_capabilities(client):
    meta = client.get("/api/meta").json()
    assert {"id": "reference", "label": config.PRESET_LABELS["reference"]} in meta["presets"]
    assert [a["id"] for a in meta["aspects"]] == list(config.ASPECTS)
    assert {f["id"] for f in meta["fonts"]} == {"inter", "montserrat"} and "Black" in meta["font_weights"]
    assert {"file", "higgsfield", "piper", "none"} <= {v["id"] for v in meta["voice_sources"]}
    assert {p["id"] for p in meta["palettes"]} == {"bright", "moody"}
    assert {m["id"] for m in meta["hook_modes"]} == {"terrain", "center", "off"}
    assert {c["id"] for c in meta["caption_cases"]} == {"lower", "upper", "as-is"}
    assert set(meta["capabilities"]) >= {"whisper", "piper", "pexels", "pixabay", "higgsfield_api"}
    assert meta["max_upload_mb"] == 1
    assert client.get("/api/schema").json() == schema.sections()


def test_defaults_can_be_changed_validated_and_reset(client, home):
    d = client.get("/api/defaults").json()
    assert d["preset"] == "reference" and d["style"]["caption"]["font_size"] == 70
    d["style"]["caption"]["font_size"] = 88
    d["script"] = "not stored in defaults"
    saved = client.put("/api/defaults", json=d).json()
    assert saved["style"]["caption"]["font_size"] == 88 and saved["script"] == ""
    assert (home / "defaults.json").is_file()
    d["style"]["caption"]["font_size"] = 5
    r = client.put("/api/defaults", json=d)
    assert r.status_code == 422 and "Text size" in r.json()["detail"]
    assert client.delete("/api/defaults").json()["style"]["caption"]["font_size"] == 70
    # new projects start from the saved defaults
    client.put("/api/defaults", json=saved)
    assert new_project(client)["project"]["style"]["caption"]["font_size"] == 88


def test_presets_endpoint(client):
    moody = client.get("/api/presets/moody").json()
    assert moody["preset"] == "moody" and moody["style"]["caption"]["case"] == "upper"
    assert client.get("/api/presets/nope").status_code == 404


# --------------------------------------------------------------------------- projects

def test_project_lifecycle(client, home):
    a = new_project(client, name="First reel")
    b = new_project(client, preset="moody")
    assert a["project"]["name"] == "First reel" and b["project"]["name"] == "untitled"
    assert b["project"]["style"]["caption"]["case"] == "upper"
    assert (home / "projects" / a["id"] / "project.json").is_file()
    assert a["outputs"] == {"video": None, "srt": None, "cover": None, "credits": None, "timings": None}

    p = a["project"]
    p["script"] = "If you lost your memory, who would you trust?"
    p["style"]["video"]["aspect"] = "1:1"
    p["style"]["caption"]["text_color"] = "#fc0"
    time.sleep(0.01)  # distinct mtimes for the newest-first order
    got = client.put(f"/api/projects/{a['id']}", json=p).json()
    assert got["project"]["style"]["caption"]["text_color"] == [255, 204, 0]
    again = client.get(f"/api/projects/{a['id']}").json()["project"]
    assert again["script"].startswith("If you") and again["style"]["video"]["aspect"] == "1:1"

    p["style"]["video"]["aspect"] = "3:2"
    r = client.put(f"/api/projects/{a['id']}", json=p)
    assert r.status_code == 422 and "Format" in r.json()["detail"]

    listing = client.get("/api/projects").json()
    assert [row["id"] for row in listing] == [a["id"], b["id"]]
    assert set(listing[0]) == {"id", "name", "updated", "has_video", "thumb"}

    upload(client, a["id"], "tree.jpg", jpeg_bytes(), "footage")
    dup = client.post(f"/api/projects/{a['id']}/duplicate").json()
    assert dup["id"] != a["id"] and dup["project"]["name"] == "First reel (copy)"
    assert [u["name"] for u in dup["uploads"]] == ["tree.jpg"] and dup["uploads"][0]["thumb_url"]

    assert client.delete(f"/api/projects/{a['id']}").json() == {"ok": True}
    assert client.get(f"/api/projects/{a['id']}").status_code == 404
    assert client.get("/api/projects/..%2F..%2Fetc").status_code == 404


def test_uploads_update_the_project_by_role(client):
    pid = new_project(client)["id"]
    r = upload(client, pid, "My Script.txt", "Say it today.\nJust say it.\n".encode(), "script")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project"]["script"] == "Say it today.\nJust say it."
    assert body["project"]["name"] == "My Script" and body["file"]["kind"] == "text"

    voice = upload(client, pid, "voice.mp3", b"ID3fake", "voice").json()
    assert voice["project"]["voice"] == voice["project"]["voice"] | {"file": "uploads/voice.mp3",
                                                                     "source": "file"}
    assert len(voice["file"]["peaks"]["peaks"]) == 1000

    p = voice["project"]
    p["music"].update(file="uploads/old.mp3", source_in=12.0, source_out=40.0)
    client.put(f"/api/projects/{pid}", json=p)
    music = upload(client, pid, "song.mp3", b"ID3fake", "music").json()
    assert music["project"]["music"]["file"] == "uploads/song.mp3"
    assert music["project"]["music"]["source_in"] == 0 and music["project"]["music"]["source_out"] is None

    pic = upload(client, pid, "../../evil name!.jpg", jpeg_bytes(), "footage").json()
    assert pic["file"]["name"] == "evil_name.jpg" and pic["file"]["role"] == "footage"
    assert pic["file"]["info"]["width"] == 64 and pic["file"]["thumb_url"].startswith(
        f"/api/projects/{pid}/files/thumbs/evil_name.jpg.jpg")
    assert client.get(pic["file"]["thumb_url"]).headers["content-type"] == "image/jpeg"
    same = upload(client, pid, "evil name!.jpg", jpeg_bytes(), "reference").json()
    assert same["file"]["name"] == "evil_name-2.jpg"
    items = same["project"]["footage"]["items"]
    assert [(i["path"], i["role"], i["kind"]) for i in items] == [
        ("uploads/evil_name.jpg", "footage", "image"), ("uploads/evil_name-2.jpg", "reference", "image")]

    view = client.get(f"/api/projects/{pid}").json()
    roles = {u["name"]: u["role"] for u in view["uploads"]}
    assert roles == {"My_Script.txt": "script", "voice.mp3": "voice", "song.mp3": "music",
                     "evil_name.jpg": "footage", "evil_name-2.jpg": "reference"}


def test_uploads_are_checked(client, home):
    pid = new_project(client)["id"]
    r = upload(client, pid, "song.mp3", b"ID3", "footage")
    assert r.status_code == 415 and "picture or a video" in r.json()["detail"]
    assert upload(client, pid, "a.jpg", jpeg_bytes(), "sticker").status_code == 422
    assert upload(client, pid, "empty.jpg", b"", "footage").status_code == 422
    big = upload(client, pid, "big.mp4", b"\0" * 1_200_000, "footage")  # limit is 1 MB here
    assert big.status_code == 413 and "1 MB" in big.json()["detail"]
    assert list((home / "projects" / pid / "uploads").iterdir()) == []
    assert client.get(f"/api/projects/{pid}").json()["project"]["footage"]["items"] == []


def test_upload_size_is_checked_while_copying_too(tmp_path):
    src = io.BytesIO(b"x" * 3000)
    with pytest.raises(server.HTTPException) as err:
        server._copy_limited(src, tmp_path / "f.bin", limit=1000)
    assert err.value.status_code == 413 and not (tmp_path / "f.bin").exists()


def test_deleting_an_upload_removes_its_references(client, home):
    pid = new_project(client)["id"]
    upload(client, pid, "voice.mp3", b"ID3", "voice")
    upload(client, pid, "song.mp3", b"ID3", "music")
    upload(client, pid, "a.jpg", jpeg_bytes(), "footage")
    for name in ("voice.mp3", "song.mp3", "a.jpg"):
        r = client.delete(f"/api/projects/{pid}/uploads/{name}")
        assert r.status_code == 200, r.text
    view = r.json()
    p = view["project"]
    assert p["voice"]["file"] is None and p["music"]["file"] is None and p["footage"]["items"] == []
    assert view["uploads"] == [] and not (home / "projects" / pid / "thumbs" / "a.jpg.jpg").exists()
    assert client.delete(f"/api/projects/{pid}/uploads/a.jpg").status_code == 404
    assert client.delete(f"/api/projects/{pid}/uploads/%2E%2E").status_code == 404
    assert client.delete(f"/api/projects/{pid}/uploads/..%2Fproject.json").status_code in (404, 405)
    assert (home / "projects" / pid / "project.json").is_file()


def test_files_support_range_requests_and_stay_inside_the_project(client, home, tmp_path):
    pid = new_project(client)["id"]
    data = bytes(range(256)) * 8
    clip = upload(client, pid, "clip.mp4", data, "footage").json()["file"]
    full = client.get(clip["url"])
    assert full.status_code == 200 and full.content == data and full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"] == "video/mp4"
    part = client.get(clip["url"], headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == data[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(data)}"
    assert client.head(clip["url"]).status_code == 200

    (tmp_path / "secret.txt").write_text("top secret")
    (home / "projects" / pid / "uploads" / "link.txt").symlink_to(tmp_path / "secret.txt")
    base = f"/api/projects/{pid}/files"
    for bad in ("..%2F..%2F..%2Fsecret.txt", "%2E%2E/%2E%2E/%2E%2E/secret.txt",
                f"{tmp_path}/secret.txt", "uploads/link.txt", "uploads/nope.mp4", "uploads"):
        r = client.get(f"{base}/{bad}")
        assert r.status_code == 404 and "top secret" not in r.text, bad


def test_waveform(client):
    pid = new_project(client)["id"]
    upload(client, pid, "song.mp3", b"ID3", "music")
    w = client.get(f"/api/projects/{pid}/waveform", params={"file": "uploads/song.mp3", "buckets": 50})
    assert w.status_code == 200 and w.json() == {"duration": 2.0, "peaks": [0.5] * 50}
    assert client.get(f"/api/projects/{pid}/waveform", params={"file": "uploads/x.mp3"}).status_code == 404
    assert client.get(f"/api/projects/{pid}/waveform",
                      params={"file": "../../../etc/passwd"}).status_code == 404
    assert client.get(f"/api/projects/{pid}/waveform",
                      params={"file": "uploads/song.mp3", "buckets": 1}).status_code == 422


def test_higgsfield_request(client):
    view = new_project(client)
    pid = view["id"]
    assert client.get(f"/api/projects/{pid}/higgsfield-request").status_code == 422
    p = view["project"] | {"script": "Say it *today*. Just say it."}
    client.put(f"/api/projects/{pid}", json=p)
    req = client.get(f"/api/projects/{pid}/higgsfield-request").json()
    assert req["tool"] == "mcp__higgsfield__generate_audio"
    assert req["params"]["prompt"] == "Say it today. Just say it."


# --------------------------------------------------------------------------- plan and render

def test_plan_uses_the_saved_or_sent_project(client):
    view = new_project(client)
    pid = view["id"]
    r = client.post(f"/api/projects/{pid}/plan")
    assert r.status_code == 422 and "script" in r.json()["detail"]
    sent = view["project"] | {"script": "Hello there"}
    plan = client.post(f"/api/projects/{pid}/plan", json=sent).json()
    assert plan["captions"] == ["hello", "there"] and plan["estimated_seconds"] == 4.2
    assert client.get(f"/api/projects/{pid}").json()["project"]["script"] == "Hello there"


def test_render_job_reports_progress_log_and_outputs(client, pipeline):
    pid = new_project(client, name="Sunrise")["id"]
    started = client.post(f"/api/projects/{pid}/render").json()
    job = wait_job(client, started["job_id"])
    assert job["status"] == "done" and job["progress"] == 1.0 and job["error"] is None
    assert any("preparing the voiceover" in line for line in job["log"])
    assert job["result"]["video"] == "output/reel.mp4"
    assert job["result"]["outputs"]["video"].startswith(f"/api/projects/{pid}/files/output/reel.mp4?v=")
    assert pipeline.rendered == ["Sunrise"]

    view = client.get(f"/api/projects/{pid}").json()
    assert view["outputs"]["srt"] and view["outputs"]["cover"] and view["outputs"]["credits"] is None
    assert client.get(view["outputs"]["video"]).content == b"\0" * 2048
    row = client.get("/api/projects").json()[0]
    assert row["has_video"] and row["thumb"].startswith(f"/api/projects/{pid}/files/output/cover.jpg")
    assert client.get("/api/jobs/nope").status_code == 404


def test_renders_run_one_at_a_time_and_can_be_cancelled(client, pipeline):
    pipeline.gate = threading.Event()
    a, b, c = (new_project(client, name=n)["id"] for n in "abc")
    first = client.post(f"/api/projects/{a}/render").json()["job_id"]
    wait_job(client, first, until=("running",))
    second = client.post(f"/api/projects/{b}/render").json()["job_id"]
    third = client.post(f"/api/projects/{c}/render").json()["job_id"]
    queued = client.get(f"/api/jobs/{second}").json()
    assert queued["status"] == "queued" and queued["position"] == 1
    assert client.get(f"/api/jobs/{third}").json()["position"] == 2
    assert client.get(f"/api/projects/{c}").json()["job"]["id"] == third

    assert client.post(f"/api/jobs/{second}/cancel").json()["status"] == "cancelled"
    client.post(f"/api/jobs/{first}/cancel")
    assert wait_job(client, first)["status"] == "cancelled"
    pipeline.gate.set()
    assert wait_job(client, third)["status"] == "done"
    assert pipeline.rendered == ["c"]


def test_a_failed_render_reports_the_error(client, pipeline):
    pipeline.fail = RuntimeError("ffmpeg exploded\nlong stderr tail")
    pid = new_project(client)["id"]
    job = wait_job(client, client.post(f"/api/projects/{pid}/render").json()["job_id"])
    assert job["status"] == "error" and job["error"].startswith("ffmpeg exploded")
    assert job["message"] == "ffmpeg exploded" and "error: ffmpeg exploded" in job["log"]


# --------------------------------------------------------------------------- UI files

def test_ui_placeholder_then_static_files(client, tmp_path):
    r = client.get("/")
    assert r.status_code == 200 and "index.html" in r.text and "/docs" in r.text
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>reelforge</title>")
    (static / "app.js").write_text("export const x = 1;")
    assert client.get("/").text.startswith("<!doctype html><title>reelforge")
    js = client.get("/static/app.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert client.get("/app.js").text == "export const x = 1;"
    assert client.get("/static/..%2F..%2Fhome%2Fdefaults.json").status_code == 404
    assert client.get("/api/nope").status_code == 404


def test_other_web_sites_cannot_change_anything(client, home, tmp_path):
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/projects", json={}, headers=evil).status_code == 403
    assert client.post("/api/projects", json={}, headers={"Origin": "null"}).status_code == 403
    assert not (home / "projects").exists()
    assert client.post("/api/projects", json={}, headers={"Origin": "http://testserver"}).status_code == 200
    assert client.get("/api/meta", headers=evil).status_code == 200  # reading is harmless
    local = server.create_app(Settings(home_dir=tmp_path / "h2"), allowed_hosts=["127.0.0.1", "localhost"])
    with TestClient(local, base_url="http://localhost:8765") as c:
        assert c.get("/api/meta").status_code == 200
        assert c.get("/api/meta", headers={"Host": "rebound.example:8765"}).status_code == 400


def test_safe_filenames():
    assert server.safe_filename("../../a b?.MP4") == "a_b.mp4"
    assert server.safe_filename("C:\\Users\\me\\voice.wav") == "voice.wav"
    assert server.safe_filename(".hidden") == "hidden"
    assert server.safe_filename("") == "file"
    assert server.safe_filename("Привет мир.txt") == "Привет_мир.txt"


def test_a_busy_port_moves_to_the_next_free_one():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert server._free_port("127.0.0.1", port) > port
