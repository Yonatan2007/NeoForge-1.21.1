"""Field metadata for the settings forms of the web UI, and validation.

Every setting of ``config.Project`` is described here once: a label and help
text written for someone who has never edited a video, the kind of input
(``type``), sensible limits and the choices for drop-downs. The UI builds its
forms from :func:`sections`; the server validates incoming projects with
:func:`validate` against the same limits, so the form and the server never
disagree. ``tests/test_server.py`` walks the dataclasses and fails when a
config field has no entry here.

Paths are dotted, as in ``cli --set``: ``style.caption.font_size``.
* A pair such as ``shadow_offset`` is two fields, ``...shadow_offset.0`` and
  ``...shadow_offset.1`` (JSON arrays index the same way in JavaScript).
* Fields of each uploaded clip (``footage.items``) use ``footage.items[].x``.
* ``color`` values are ``[r, g, b]`` lists in the project JSON; ``#rrggbb``
  strings are accepted on the way in.

Types: ``number``, ``int``, ``bool``, ``text``, ``select``, ``color``,
``optional-number`` (empty = automatic / ``null``) and, for the non-style
sections only, ``textarea`` (the script), ``lines`` (a list of strings, one
per line), ``multiselect`` (a list of option values) and ``list`` (managed by
uploads, not a form field). ``nullable`` text fields store ``null`` when empty;
``custom`` selects also accept values outside their options (a font file
path, a hand-written vignette). A ``color`` field with options (the hook
colour) stores either one of those words (``"auto"``) or a ``#rrggbb`` string.
"""
from __future__ import annotations

import math
import re
import typing
from dataclasses import dataclass

from . import config, fonts, voiceover
from .config import ASPECTS, PRESET_LABELS, PRESETS, Project

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
TYPES = {"number", "int", "bool", "text", "select", "color", "optional-number",
         "textarea", "lines", "multiselect", "list"}

Options = tuple[tuple[typing.Any, str], ...]


@dataclass(frozen=True)
class Field:
    path: str
    label: str
    help: str
    type: str
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: Options = ()
    unit: str = ""
    advanced: bool = False
    nullable: bool = False
    custom: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "label": self.label, "help": self.help, "type": self.type,
                "min": self.min, "max": self.max, "step": self.step,
                "options": [{"value": v, "label": label} for v, label in self.options],
                "unit": self.unit, "advanced": self.advanced, "nullable": self.nullable,
                "custom": self.custom}


@dataclass(frozen=True)
class Section:
    id: str
    label: str
    help: str
    fields: tuple[Field, ...]

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "help": self.help,
                "fields": [f.to_dict() for f in self.fields]}


# --------------------------------------------------------------------------- choices
# Shared with GET /api/meta so drop-downs and the meta endpoint agree.

FONTS: Options = (("inter", "Inter (clean, modern)"), ("montserrat", "Montserrat (bold, geometric)"))
WEIGHTS: Options = tuple((w, w) for w in fonts.WEIGHTS)
CASES: Options = (("lower", "lowercase"), ("upper", "UPPERCASE"), ("as-is", "As written"))
EMPHASIS: Options = (("none", "No colours (all words white)"),
                     ("color", "Colour key words (yellow / red)"))
REVEAL: Options = (("build", "Word by word, as spoken"), ("phrase", "Whole caption at once"))
HOOK_MODES: Options = (("terrain", "Along the skyline (the reference look)"),
                       ("center", "Like the other captions"),
                       ("off", "No text for the hook"))
HOOK_COLOR_AUTO: Options = (("auto", "Automatic"),)
HOOK_FALLBACKS: Options = (("diagonal", "Diagonal line"), ("arc", "Gentle arc"), ("center", "Centred line"))
ASPECT_LABELS = {"9:16": "9:16 vertical (Reels, TikTok, Shorts)", "4:5": "4:5 portrait (feed post)",
                 "1:1": "1:1 square", "16:9": "16:9 landscape (YouTube)"}
ASPECT_OPTIONS: Options = tuple((a, ASPECT_LABELS.get(a, a)) for a in ASPECTS)
ENCODER_PRESETS: Options = tuple((p, p) for p in ("ultrafast", "superfast", "veryfast", "faster", "fast",
                                                   "medium", "slow", "slower", "veryslow"))
AUDIO_BITRATES: Options = tuple((b, b.replace("k", " kbit/s")) for b in ("128k", "160k", "192k",
                                                                          "256k", "320k"))
LOOK_MATCH: Options = (("reference", "Reference look (warm sunny film)"),
                       ("uploads", "Match my reference pictures"),
                       ("off", "Off (only the sliders below)"))
VIGNETTES: Options = (("", "None"), ("PI/6", "Light"), ("PI/5", "Medium"), ("PI/4", "Strong"))
VOICE_SOURCES: Options = (("file", "Upload a voiceover (or paste a link)"),
                          ("higgsfield", "Higgsfield voice, made by Claude"),
                          ("higgsfield-api", "Higgsfield API (needs API keys)"),
                          ("piper", "Piper (free offline voice, for drafts)"),
                          ("none", "No voice (music and captions only)"))
VOICE_PRESET_LABELS = {"elevenlabs": "ElevenLabs (most natural)", "seed": "Seed Audio (slightly slower)"}
VOICE_PRESETS: Options = tuple((p, VOICE_PRESET_LABELS.get(p, p)) for p in voiceover.VOICE_PRESETS)
ALIGN: Options = (("auto", "Automatic (Whisper if installed)"), ("whisper", "Whisper speech recognition"),
                  ("estimate", "Estimate from the audio"))
STOCK_SOURCES: Options = (("mixkit", "Mixkit (free, no key needed)"), ("pexels", "Pexels (needs an API key)"),
                          ("pixabay", "Pixabay (needs an API key)"))
PALETTES: Options = (("bright", "Bright (sunny nature)"),
                     ("moody", "Moody (night, rain)"))
ROLES: Options = (("footage", "Use in the video"), ("reference", "Reference only (steers stock and colour)"))
KINDS: Options = (("auto", "Detect automatically"), ("image", "Picture"), ("video", "Video"))
MOTIONS: Options = (("auto", "Automatic"), ("zoom-in", "Slow zoom in"), ("zoom-out", "Slow zoom out"),
                    ("pan-left", "Pan left"), ("pan-right", "Pan right"), ("none", "Still"))
PRESET_OPTIONS: Options = tuple((p, PRESET_LABELS.get(p, p)) for p in PRESETS)

PX_NOTE = " Measured on a 1080-pixel-wide video; it scales with the output size."


def _num(path, label, help, lo, hi, step, unit="", advanced=False) -> Field:
    return Field(path, label, help, "number", lo, hi, step, unit=unit, advanced=advanced)


def _int(path, label, help, lo, hi, unit="", advanced=False) -> Field:
    return Field(path, label, help, "int", lo, hi, 1, unit=unit, advanced=advanced)


def _opt(path, label, help, lo, hi, step, unit="", advanced=False) -> Field:
    return Field(path, label, help, "optional-number", lo, hi, step, unit=unit, advanced=advanced)


def _sel(path, label, help, options, advanced=False, custom=False) -> Field:
    return Field(path, label, help, "select", options=options, advanced=advanced, custom=custom)


def _bool(path, label, help, advanced=False) -> Field:
    return Field(path, label, help, "bool", advanced=advanced)


def _color(path, label, help, advanced=False) -> Field:
    return Field(path, label, help, "color", advanced=advanced)


def _text(path, label, help, advanced=False, nullable=False) -> Field:
    return Field(path, label, help, "text", advanced=advanced, nullable=nullable)


C, H, V, L = "style.caption.", "style.hook.", "style.video.", "style.look."

CAPTION = Section("caption", "Captions", "The words shown on screen after the hook.", (
    _sel(C + "font", "Font", "Typeface of the captions. Inter is clean and modern (the reference look); "
         "Montserrat is bolder.", FONTS, custom=True),
    _sel(C + "font_weight", "Font weight", "How thick the letters are. Bold matches the reference.", WEIGHTS),
    _int(C + "font_size", "Text size", "Height of the caption letters." + PX_NOTE, 24, 200, "px"),
    _sel(C + "case", "Letter case", "Show the words in lowercase (the reference look), in capitals, "
         "or exactly as written in the script.", CASES),
    _color(C + "text_color", "Text colour", "Colour of normal caption words."),
    _sel(C + "emphasis", "Key word colours", "Colour important words. Mark them yourself with *word* "
         "(yellow) or **word** (red) in the script, or let the app find them.", EMPHASIS),
    _color(C + "highlight_color", "Highlight colour", "Colour of key words (marked *like this*)."),
    _color(C + "alert_color", "Strong highlight colour",
           "Colour of the strongest words (marked **like this**)."),
    _int(C + "stroke_width", "Outline thickness", "Dark outline around each letter; 0 = no outline "
         "(the reference look)." + PX_NOTE, 0, 20, "px"),
    _color(C + "stroke_color", "Outline colour", "Colour of the outline around the letters.", advanced=True),
    _int(C + "shadow_offset.0", "Shadow offset sideways", "How far the shadow is moved to the right "
         "(negative = left).", -30, 30, "px", advanced=True),
    _int(C + "shadow_offset.1", "Shadow offset down", "How far the shadow is moved down "
         "(negative = up).", -30, 30, "px", advanced=True),
    _num(C + "shadow_blur", "Shadow softness", "How blurred the shadow behind the words is; 0 = sharp.",
         0, 40, 0.5, "px"),
    _num(C + "shadow_opacity", "Shadow strength", "How dark the shadow is: 0 = no shadow, 1 = solid.",
         0, 1, 0.05),
    _int(C + "max_words", "Words per caption", "1 shows one word at a time (the reference look); "
         "2 to 4 shows short phrases.", 1, 8),
    _int(C + "max_chars", "Letters per caption", "Longest caption in letters before it is split.",
         6, 60, advanced=True),
    _int(C + "max_line_width", "Line width", "Widest a caption line may get before it wraps." + PX_NOTE,
         300, 1080, "px", advanced=True),
    _int(C + "max_lines", "Lines per caption", "How many lines one caption may use.", 1, 4, advanced=True),
    _num(C + "line_spacing", "Line spacing", "Space between caption lines (1 = tight).", 0.8, 2.0, 0.05,
         "×", advanced=True),
    _num(C + "y_center", "Vertical position", "Where the captions sit: 0 = top edge, 0.5 = middle, "
         "1 = bottom edge.", 0.05, 0.95, 0.01),
    _sel(C + "reveal", "How words appear", "Pop each word in as it is spoken, or show the whole caption "
         "at once.", REVEAL),
    _num(C + "pop_duration", "Pop-in time", "How long the little zoom when a word appears takes.",
         0, 0.6, 0.01, "s", advanced=True),
    _num(C + "pop_start_scale", "Pop-in start size", "Size a word starts at when it pops in "
         "(1 = no zoom).", 0.3, 1.0, 0.05, "×", advanced=True),
    _num(C + "lead", "Show words early by", "Show each word slightly before it is heard, which feels "
         "in sync.", 0, 0.5, 0.01, "s", advanced=True),
    _num(C + "hold", "Linger", "Keep a caption on screen this long after its last word.",
         0, 2, 0.05, "s", advanced=True),
    _num(C + "min_duration", "Shortest caption", "No caption is shown for less than this.",
         0.05, 2, 0.05, "s", advanced=True),
    _num(C + "gap_break", "Pause that starts a new caption", "A pause in the voice this long always "
         "starts a new caption.", 0.1, 3, 0.05, "s", advanced=True),
    _num(C + "flicker_gap", "Close gaps shorter than", "Tiny gaps between captions are closed so the "
         "text does not flicker.", 0, 1, 0.05, "s", advanced=True),
))

HOOK = Section("hook", "Hook", "The opening sentence, laid along the skyline of the first shot.", (
    _sel(H + "mode", "Hook style", "Lay the opening words along the mountain skyline (the reference "
         "look), show them like normal captions, or show no text during the hook.", HOOK_MODES),
    _int(H + "sentences", "Sentences in the hook", "How many opening sentences form the hook.", 1, 5),
    _sel(H + "font", "Font", "Typeface of the hook words.", FONTS, custom=True),
    _sel(H + "font_weight", "Font weight", "How thick the hook letters are.", WEIGHTS),
    _int(H + "size", "Word size", "Size of a normal hook word; the first, last and stressed words are "
         "bigger." + PX_NOTE, 20, 160, "px"),
    _num(H + "big_scale", "Big word size", "How much bigger the first word, the last word and stressed "
         "words are.", 1.0, 4.0, 0.1, "×"),
    _num(H + "small_scale", "Small word size", "How much smaller little words (a, the, to, of) are.",
         0.4, 1.0, 0.02, "×"),
    Field(H + "color", "Text colour", "Automatic picks dark text on a bright sky and white text on a "
          "dark one; or choose one colour for every hook word.", "color", options=HOOK_COLOR_AUTO),
    _color(H + "dark_color", "Dark text colour", "Used by automatic colour on bright skies.", advanced=True),
    _color(H + "light_color", "Light text colour", "Used by automatic colour on dark skies.", advanced=True),
    _bool(H + "shadow", "Shadow", "Soft shadow behind the hook words (the reference has none)."),
    _int(H + "offset", "Gap above the skyline", "Distance between the ridge line and the words." + PX_NOTE,
         -40, 120, "px"),
    _int(H + "margin", "Side margin", "Keep the hook this far from the left and right edges." + PX_NOTE,
         0, 200, "px", advanced=True),
    _bool(H + "per_glyph", "Bend letters around peaks", "Place single letters one by one around sharp "
          "peaks, like \"w o u l d\" in the reference.", advanced=True),
    _bool(H + "last_word_below", "Drop the last word below", "Show the final word large, under the end "
          "of the line (\"are?\" in the reference)."),
    _num(H + "hold", "Keep on screen", "How long the hook stays after its last word is spoken.",
         0, 3, 0.05, "s"),
    _num(H + "min_confidence", "Skyline certainty needed", "Skylines the app is less sure about than this "
         "use the fallback layout instead.", 0, 1, 0.05, advanced=True),
    _sel(H + "fallback", "Layout without a skyline", "Shape of the hook line when the first shot has no "
         "clear skyline.", HOOK_FALLBACKS, advanced=True),
))

VIDEO = Section("video", "Video", "Format, cutting rhythm and file quality.", (
    _sel(V + "aspect", "Format", "Shape of the video. 9:16 fills a phone screen.", ASPECT_OPTIONS),
    _bool(V + "letterbox", "Black bars to 9:16", "Keep this shape but save the video as 9:16, with black "
          "bars above and below, so it posts full-screen with the picture in the middle."),
    _bool(V + "draft", "Quick draft", "Render at half resolution: much faster, for checking timing "
          "before the final video."),
    _int(V + "fps", "Frames per second", "30 suits social media.", 15, 60, "fps", advanced=True),
    _num(V + "min_shot", "Shortest shot", "Cuts happen at sentence and phrase breaks, no sooner than this.",
         1, 15, 0.5, "s"),
    _num(V + "max_shot", "Longest shot", "A shot is cut after this long even mid-sentence.",
         1.5, 30, 0.5, "s"),
    _num(V + "cut_preroll", "Cut before a sentence by", "Cut slightly before a sentence starts, which "
         "feels natural.", 0, 1, 0.01, "s", advanced=True),
    _num(V + "crossfade", "Crossfade", "Blend between shots; 0 = hard cuts (the reference look).",
         0, 1.5, 0.05, "s"),
    _num(V + "fade_in", "Fade in", "Fade from black at the start.", 0, 3, 0.05, "s"),
    _num(V + "fade_out", "Fade out", "Fade to black at the end.", 0, 5, 0.05, "s"),
    _num(V + "voice_delay", "Silence before the voice", "Picture before the first word is spoken.",
         0, 5, 0.05, "s", advanced=True),
    _num(V + "tail", "Picture after the last word", "How long the video keeps running after the voice ends.",
         0, 10, 0.1, "s"),
    _num(V + "image_seconds", "Photo clip length", "How long one of your pictures is shown, unless "
         "set on the picture itself.", 1, 20, 0.5, "s"),
    _num(V + "max_clip_seconds", "Longest source clip", "Only this much of each source video is "
         "prepared (keeps rendering fast).", 5, 120, 1, "s", advanced=True),
    _int(V + "crf", "Quality (CRF)", "Lower = better quality and a bigger file; 18 looks "
         "like the original.", 12, 35, advanced=True),
    _text(V + "maxrate", "Max bitrate", "Upper limit of the video data rate, e.g. 10M. Instagram "
          "re-encodes anyway.", advanced=True),
    _text(V + "bufsize", "Rate buffer", "Encoder buffer for the bitrate limit, usually twice the max "
          "bitrate, e.g. 20M.", advanced=True),
    _sel(V + "preset", "Encoding speed", "Slower = slightly smaller file at the same quality.",
         ENCODER_PRESETS, advanced=True),
    _sel(V + "audio_bitrate", "Audio quality", "Bitrate of the sound track.", AUDIO_BITRATES, advanced=True),
))

LOOK = Section("look", "Colour", "The colour grade applied to every clip.", (
    _sel(L + "match", "Colour match", "Give every clip the same look: the warm sunny film look of the "
         "reference, the colours of your reference pictures, or no matching.", LOOK_MATCH),
    _num(L + "match_strength", "Match strength", "0 = clips keep their own colours, 1 = full match.",
         0, 1, 0.05),
    _num(L + "contrast", "Contrast", "1 = unchanged; lower is softer, higher is punchier.", 0.5, 1.5, 0.01),
    _num(L + "brightness", "Brightness", "0 = unchanged.", -0.3, 0.3, 0.01),
    _num(L + "saturation", "Saturation", "Colour intensity: 1 = unchanged, 0 = black and white.", 0, 2, 0.01),
    _num(L + "gamma", "Mid-tones", "Above 1 brightens the mid-tones, below 1 darkens them.",
         0.5, 2, 0.01, advanced=True),
    _num(L + "lift", "Faded blacks", "Lift the darkest parts for a soft film look; 0 = deep blacks.",
         0, 0.15, 0.005),
    _text(L + "tint", "Colour tint", "Advanced: FFmpeg colorbalance values, e.g. rs=-0.04:bh=-0.03. "
          "Leave empty for none.", advanced=True),
    _sel(L + "vignette", "Vignette", "Darken the corners of the picture.", VIGNETTES, custom=True),
    _int(L + "grain", "Film grain", "Fine noise like analogue film; 0 = clean.", 0, 30),
))

MUSIC = Section("music", "Music", "Background music, its part, position and level.", (
    _text("music.file", "Music file", "Set by uploading a song on the Music tab, or a path / link.",
          advanced=True, nullable=True),
    _num("music.source_in", "Song start", "Use the song from this point.", 0, 3600, 0.1, "s"),
    _opt("music.source_out", "Song end", "Use the song up to this point (empty = to its end).",
         0, 3600, 0.1, "s"),
    _num("music.start_at", "Starts in the video at", "Where the music begins in the video.",
         0, 600, 0.1, "s"),
    _opt("music.end_at", "Stops in the video at", "Where the music ends in the video (empty = at the end).",
         0, 600, 0.1, "s"),
    _bool("music.loop", "Loop", "Repeat the chosen part if it is shorter than the video."),
    _num("music.volume_db", "Volume", "Music level: 0 is loudest; -16 sits well under a voice.",
         -40, 0, 0.5, "dB"),
    _num("music.fade_in", "Fade in", "Fade the music in over this long.", 0, 10, 0.1, "s"),
    _num("music.fade_out", "Fade out", "Fade the music out over this long.", 0, 15, 0.1, "s"),
    _bool("music.duck", "Lower under the voice", "Automatically turn the music down while the voice speaks."),
    _num("music.duck_db", "Lower by", "How much quieter the music gets while the voice speaks.",
         -30, 0, 0.5, "dB"),
))

DURATION = Section("duration", "Length", "How long the finished video is.", (
    _opt("duration.target", "Target length", "Empty = as long as the voiceover needs. Otherwise the voice "
         "is sped up a little (never cut) and the opening and ending are stretched to reach this length.",
         5, 180, 0.5, "s"),
    _num("duration.min_tempo", "Slowest voice speed", "The voice is never slowed down more than this.",
         0.8, 1.0, 0.01, "×", advanced=True),
    _num("duration.max_tempo", "Fastest voice speed", "The voice is never sped up more than this.",
         1.0, 1.3, 0.01, "×", advanced=True),
    _num("duration.max_intro", "Longest extra opening", "When the voice is short, at most this much "
         "picture and music is added before the first word; the rest goes to the ending.",
         0, 10, 0.1, "s", advanced=True),
))

FOOTAGE = Section("footage", "Footage", "Your own pictures and videos, and free stock footage.", (
    _bool("footage.stock", "Use free stock footage", "Fill the shots that have none of your own media "
          "with free stock clips."),
    Field("footage.sources", "Stock sites", "Where to search. Pexels and Pixabay need a free API key in "
          "the .env file.", "multiselect", options=STOCK_SOURCES),
    _sel("footage.palette", "Footage mood", "The kind of scenes searched for.", PALETTES),
    Field("footage.queries", "Custom searches", "One search per line, used for the shots in order. Leave "
          "empty to search automatically from the script.", "lines", nullable=True),
    _text("footage.hook_query", "Search for the opening shot", "The first shot needs a clear skyline for "
          "the hook text, e.g. mountains. Empty = use the normal searches.", nullable=True),
    _bool("footage.reference_matching", "Prefer clips like my references", "Rank stock clips by how much "
          "they look like your reference pictures."),
    _bool("footage.allow_landscape", "Allow landscape clips", "Crop landscape clips to fit when there "
          "are not enough vertical ones.", advanced=True),
))

VOICE = Section("voice", "Voice", "Where the voiceover comes from and how words are timed.", (
    _sel("voice.source", "Voice", "Upload a finished voiceover, let Claude make one with Higgsfield, use "
         "a free offline voice, or make a reel without a voice.", VOICE_SOURCES),
    _text("voice.file", "Voiceover file or link", "Set by uploading on the Voice tab; a link to an audio "
          "file also works.", nullable=True),
    _sel("voice.higgsfield_preset", "Higgsfield voice engine", "Which Higgsfield speech model reads the "
         "script.", VOICE_PRESETS),
    _text("voice.voice_id", "Higgsfield voice ID", "A voice from your Higgsfield account; empty = the "
          "default narrator.", nullable=True),
    _sel("voice.voice_type", "Higgsfield voice type", "Built-in voice, or one you made in Higgsfield.",
         (("preset", "Built-in voice"), ("element", "My own voice")), advanced=True),
    _text("voice.piper_model", "Piper voice model", "Path to a Piper .onnx voice file "
          "(huggingface.co/rhasspy/piper-voices).", advanced=True, nullable=True),
    _num("voice.words_per_second", "Reading pace", "Without a voice, captions follow this reading speed.",
         1, 5, 0.1, "words/s"),
    _sel("voice.align", "Word timing", "How the app finds when each word is spoken.", ALIGN, advanced=True),
))

PROJECT = Section("project", "Project", "The reel itself.", (
    _text("name", "Project name", "Shown in the project list and used for the video file name."),
    _sel("preset", "Look preset", "A starting point for every style setting.", PRESET_OPTIONS),
    Field("script", "Script", "What the voice says. The first sentence becomes the hook. Mark key words "
          "with *word* (yellow) or **word** (red).", "textarea"),
    _bool("auto_emphasis", "Find key words automatically", "Highlight important words (love, never, "
          "today ...) without marking them."),
))

ITEM = "footage.items[]."
FOOTAGE_ITEM = Section("footage_item", "Uploaded clip", "Settings of one of your pictures or videos.", (
    Field("footage.items", "Your pictures and videos", "Added by uploading on the Footage tab; their order "
          "is the shot order.", "list", advanced=True),
    _text(ITEM + "path", "File", "The uploaded file, inside the project folder.", advanced=True),
    _sel(ITEM + "role", "Use", "Show it in the video, or only use it as a reference for the stock "
         "search and the colours.", ROLES),
    _sel(ITEM + "kind", "Type", "Picture or video; detected from the file.", KINDS, advanced=True),
    _num(ITEM + "trim_in", "Start at", "Videos: use the clip from this point.", 0, 3600, 0.1, "s"),
    _opt(ITEM + "trim_out", "End at", "Videos: use the clip up to this point (empty = to its end).",
         0, 3600, 0.1, "s"),
    _opt(ITEM + "seconds", "Show for", "Pictures: how long it is shown (empty = the default photo length).",
         1, 30, 0.5, "s"),
    _sel(ITEM + "motion", "Movement", "Pictures: a slow camera move so the still image feels alive.",
         MOTIONS),
    _opt(ITEM + "shot", "Pin to shot", "Always use it for this shot (0 = the opening hook shot; empty = "
         "next free shot in order).", 0, 99, 1),
    _text(ITEM + "note", "Note", "Your own note, e.g. what this clip is for."),
))

SECTIONS: tuple[Section, ...] = (CAPTION, HOOK, VIDEO, LOOK, MUSIC, DURATION, FOOTAGE, VOICE,
                                 PROJECT, FOOTAGE_ITEM)
FIELDS: dict[str, Field] = {f.path: f for s in SECTIONS for f in s.fields}
_SECTION_OF: dict[str, str] = {f.path: s.label for s in SECTIONS for f in s.fields}


def sections() -> list[dict]:
    """JSON for ``GET /api/schema``."""
    return [s.to_dict() for s in SECTIONS]


def options(choices: Options) -> list[dict]:
    """``[{"id", "label"}]`` for the meta endpoint."""
    return [{"id": v, "label": label} for v, label in choices]


# --------------------------------------------------------------------------- validation

def validate(data: dict) -> Project:
    """Turn a project dict from the UI into a ``Project``.

    Missing keys come from the project's preset (like ``config.load_project``),
    values are converted to their declared types and then checked against the
    limits and choices above. Raises ``ValueError`` with one readable line per
    problem, naming the setting the way the form labels it."""
    if not isinstance(data, dict):
        raise ValueError("The project must be a JSON object.")
    preset = data.get("preset") or "reference"
    if preset not in PRESETS:
        raise ValueError(f"Unknown look preset {preset!r}; choose one of {', '.join(PRESETS)}.")
    try:
        project = config.from_dict(Project, config.deep_merge(config.preset_dict(preset), data))
    except (TypeError, ValueError, IndexError, AttributeError, OverflowError) as exc:
        raise ValueError(f"Some settings have the wrong type ({exc}).") from exc

    problems: list[str] = []
    try:
        for f in FIELDS.values():
            if f.path.startswith(ITEM):
                for n, item in enumerate(project.footage.items):
                    problems += _check(f, _get(item, f.path[len(ITEM):]), f"Clip {n + 1}: ")
            else:
                problems += _check(f, _get(project, f.path), "")
    except (TypeError, IndexError, AttributeError, OverflowError) as exc:  # wrong shape, e.g. a list where a value belongs
        raise ValueError(f"Some settings have the wrong shape ({exc}).") from exc
    if problems:
        raise ValueError("\n".join(problems))
    return project


def _get(obj, path: str):
    for key in path.split("."):
        obj = obj[int(key)] if key.isdigit() else getattr(obj, key)
    return obj


def _check(f: Field, value, prefix: str) -> list[str]:
    name = f"{prefix}{_SECTION_OF[f.path]} › {f.label}"
    if value is None:
        ok = f.nullable or f.type in ("optional-number", "lines")
        return [] if ok else [f"{name}: a value is required."]
    if f.type in ("number", "int", "optional-number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return [f"{name}: must be a number."]
        if (f.min is not None and value < f.min) or (f.max is not None and value > f.max):
            return [f"{name}: must be between {f.min:g} and {f.max:g} (got {value:g})."]
    elif f.type == "select" and not f.custom:
        allowed = [v for v, _ in f.options]
        if value not in allowed:
            return [f"{name}: {value!r} is not one of {', '.join(map(str, allowed))}."]
    elif f.type == "multiselect":
        allowed = {v for v, _ in f.options}
        bad = [v for v in value if v not in allowed]
        if bad:
            return [f"{name}: unknown {', '.join(map(str, bad))}."]
    elif f.type == "color":
        if isinstance(value, str):  # the hook colour: "auto" or "#rrggbb"
            if value not in [v for v, _ in f.options] and not _HEX.match(value):
                return [f"{name}: {value!r} is not a colour like #ffcc00."]
        elif (not isinstance(value, (list, tuple)) or len(value) != 3
              or not all(isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 255
                         for c in value)):
            return [f"{name}: a colour needs red, green and blue values of 0-255."]
    return []

