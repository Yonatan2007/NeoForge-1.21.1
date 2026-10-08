import argparse
import json

import pytest

from reelforge import cli, config
from reelforge.config import Settings


def args(**kw):
    base = dict(project=None, script="Say it today. Just say it.", out="out", slug=None, preset=None,
                voiceover=None, tts=None, piper_model=None, voice_id=None, voice_preset=None,
                align=None, music=None, footage_dir=None, reference_dir=None, queries=None,
                no_stock=False, duration=None, aspect=None, set=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_set_parses_json_values_and_rejects_unknown_paths():
    data = config.to_dict(config.Project())
    cli._set(data, "style.caption.font_size", "80")
    cli._set(data, "music.loop", "false")
    cli._set(data, "footage.queries", '["sea", "sky"]')
    cli._set(data, "style.caption.case", "upper")
    p = config.from_dict(config.Project, data)
    assert p.style.caption.font_size == 80 and p.music.loop is False
    assert p.footage.queries == ["sea", "sky"] and p.style.caption.case == "upper"
    with pytest.raises(SystemExit):
        cli._set(data, "style.caption.nope", "1")


def test_build_project_applies_flags_and_folders(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "b.jpg").write_bytes(b"x")
    (clips / "a.mp4").write_bytes(b"x")
    (clips / "notes.txt").write_text("ignored")
    settings = Settings(home_dir=tmp_path / "home")
    p, d = cli._build_project(args(out=str(tmp_path), voiceover="v.mp3", footage_dir=str(clips),
                                   duration=30, aspect="1:1", queries="sea; sky",
                                   set=["style.hook.mode=center"]), settings)
    assert d == tmp_path / "say-it-today-just-say-it"
    assert p.voice.source == "file" and p.voice.file == "v.mp3"
    assert [i.path.rsplit("/", 1)[1] for i in p.footage.items] == ["a.mp4", "b.jpg"]
    assert p.duration.target == 30 and p.style.video.aspect == "1:1"
    assert p.footage.queries == ["sea", "sky"] and p.style.hook.mode == "center"


def test_build_project_starts_from_saved_defaults_or_preset(tmp_path):
    settings = Settings(home_dir=tmp_path)
    d = config.load_defaults(settings)
    d.style.caption.font_size = 77
    config.save_defaults(d, settings)
    p, _ = cli._build_project(args(out=str(tmp_path)), settings)
    assert p.style.caption.font_size == 77
    p, _ = cli._build_project(args(out=str(tmp_path), preset="moody"), settings)
    assert p.style.caption.case == "upper" and p.style.caption.font_size == 96


def test_project_file_round_trip_with_preset_switch(tmp_path):
    proj = config.from_dict(config.Project, config.preset_dict("reference"))
    proj.script = "Hello there."
    path = config.save_project(proj, tmp_path / "p" / "project.json")
    p, d = cli._build_project(args(script=None, project=str(path), preset="moody"), Settings(home_dir=tmp_path))
    assert d == path.parent and p.script == "Hello there." and p.style.caption.case == "upper"
    assert json.loads(path.read_text())["script"] == "Hello there."
