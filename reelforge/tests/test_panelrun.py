import base64
import json
import subprocess

from reelforge import config, panelrun
from reelforge.config import FootageItem, Project


def make_doc(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x568", "-frames:v", "1",
                    str(tmp_path / "pic.jpg")], check=True)
    (assets / "aaaa.jpg").write_bytes((tmp_path / "pic.jpg").read_bytes())
    song = b"ID3" + bytes(range(256)) * 50
    b64 = base64.b64encode(song).decode()
    (assets / "bbbb.txt").write_text(b64[:1000] + "\n")       # a file split over two text assets
    (assets / "cccc.txt").write_text(b64[1000:])
    p = config.from_dict(Project, config.preset_dict("reference"))
    p.script = "Say it today. Just say it."
    p.voice.source = "none"
    p.footage.stock = False
    p.footage.items = [FootageItem(path="uploads/pic.jpg", kind="image")]
    p.music.file = "uploads/song.mp3"
    doc = {"id": "reel-1", "version": 3, "data": {
        "project": config.to_dict(p),
        "files": [{"name": "pic.jpg", "path": "uploads/pic.jpg", "store": {"type": "raw", "ids": ["aaaa"]}},
                  {"name": "song.mp3", "path": "uploads/song.mp3",
                   "store": {"type": "b64", "ids": ["bbbb", "cccc"]}},
                  {"name": "voice.mp3", "path": "uploads/voice.mp3", "store": {"type": "raw", "ids": ["dddd"]}}]}}
    (tmp_path / "doc.json").write_text(json.dumps(doc))
    return assets, song


def test_stage_lists_missing_assets_then_builds_the_project_folder(tmp_path):
    assets, song = make_doc(tmp_path)
    work = tmp_path / "work"
    assert panelrun.stage(tmp_path / "doc.json", assets, work) == {"need": ["dddd"]}
    (assets / "dddd.mp3").write_bytes(b"ID3voice")
    out = panelrun.stage(tmp_path / "doc.json", assets, work)
    assert out["ready"] and out["files"] == ["pic.jpg", "song.mp3", "voice.mp3"]
    assert (work / "uploads" / "song.mp3").read_bytes() == song
    assert config.load_project(work / "project.json").script == "Say it today. Just say it."


def test_plan_and_status(tmp_path):
    assets, _ = make_doc(tmp_path)
    (assets / "dddd.mp3").write_bytes(b"ID3voice")
    work = tmp_path / "work"
    panelrun.stage(tmp_path / "doc.json", assets, work)
    plan = panelrun.plan(work)
    assert plan["shots"] and json.loads((work / "plan.json").read_text())["shots"] == plan["shots"]
    assert panelrun.status(work)["status"] == "running"  # nothing rendered yet
