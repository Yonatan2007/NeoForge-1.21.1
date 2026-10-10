import json

import pytest

from reelforge import config
from reelforge.config import CaptionStyle, Project, Settings, VideoStyle


def test_project_round_trips_through_json():
    p = Project(script="Say it today.")
    p.footage.items.append(config.FootageItem(path="uploads/a.jpg", role="reference", shot=0))
    data = json.loads(json.dumps(config.to_dict(p)))
    assert config.from_dict(Project, data) == p


def test_from_dict_coerces_and_ignores_unknown_keys():
    cs = config.from_dict(CaptionStyle, {"text_color": "#fc0", "font_size": "80.4",
                                         "shadow_offset": [1, 2], "not_a_field": 1})
    assert cs.text_color == (255, 204, 0)
    assert cs.font_size == 80 and cs.shadow_offset == (1, 2)


def test_presets_layer_on_the_reference_defaults():
    ref = config.from_dict(Project, config.preset_dict("reference"))
    moody = config.from_dict(Project, config.preset_dict("moody"))
    assert ref.style.caption.case == "lower" and ref.style.hook.mode == "terrain"
    assert moody.style.caption.case == "upper" and moody.style.caption.emphasis == "color"
    assert moody.style.video.crossfade == 0.35 and moody.footage.palette == "moody"
    assert moody.voice == ref.voice  # presets only touch the look
    with pytest.raises(ValueError):
        config.preset_dict("nope")


def test_saved_defaults_survive_and_drop_per_reel_content(tmp_path):
    settings = Settings(home_dir=tmp_path)
    p = config.load_defaults(settings)
    assert p.preset == "reference"
    p.style.caption.font_size = 88
    p.music.file = "uploads/song.mp3"
    p.footage.items.append(config.FootageItem(path="uploads/x.mp4"))
    p.script = "secret script"
    config.save_defaults(p, settings)
    again = config.load_defaults(settings)
    assert again.style.caption.font_size == 88
    assert again.music.file is None and again.footage.items == [] and again.script == ""
    config.reset_defaults(settings)
    assert config.load_defaults(settings).style.caption.font_size == CaptionStyle().font_size


def test_letterbox_pads_wide_shapes_to_9_16():
    assert VideoStyle(aspect="4:5", letterbox=True).frame_size == (1080, 1920)
    assert VideoStyle(aspect="4:5").frame_size == (1080, 1350)
    assert VideoStyle(aspect="9:16", letterbox=True).frame_size == (1080, 1920)
    assert VideoStyle(aspect="1:1", letterbox=True, draft=True).frame_size == (540, 960)


def test_aspect_and_draft_sizes():
    assert (VideoStyle().width, VideoStyle().height) == (1080, 1920)
    assert (VideoStyle(aspect="1:1", draft=True).width, VideoStyle(aspect="1:1", draft=True).height) == (540, 540)
    assert VideoStyle(aspect="4:5", draft=True).height % 2 == 0


def test_load_project_fills_new_fields_from_its_preset(tmp_path):
    path = tmp_path / "project.json"
    path.write_text(json.dumps({"preset": "moody", "script": "Hi.", "style": {"video": {"aspect": "1:1"}}}))
    p = config.load_project(path)
    assert p.style.video.aspect == "1:1" and p.style.caption.case == "upper" and p.script == "Hi."
