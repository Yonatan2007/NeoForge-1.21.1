"""Hand-tuned word lists that encode the aesthetic.

* ``EMPHASIS_WORDS`` / ``EMPHASIS_PHRASES`` decide which words get coloured.
  Level 1 = highlight (yellow), level 2 = alert (red, finality and absolutes).
* ``BRIGHT_CONCEPTS`` / ``BRIGHT_ATMOSPHERE`` (the default "bright" footage
  palette, the reference look) map script vocabulary to sunny nature and
  adventure searches, and fill the remaining shots.
* ``VISUAL_CONCEPTS`` / ``ATMOSPHERE`` are the "moody" palette (night, rain).
* ``PALETTES`` names both.

Edit freely: these lists are the "taste" of the generator.
"""
from __future__ import annotations

# Words are matched after normalisation (lower case, apostrophes removed).
EMPHASIS_WORDS: dict[str, int] = {
    # level 2: finality, absolutes, loss
    **{w: 2 for w in (
        "last never nobody nothing noone die dies dying dead death gone forever "
        "goodbye lost late alone regret final grave funeral end ending"
    ).split()},
    # level 1: emotional anchors
    **{w: 1 for w in (
        "today now stop love loved proud sorry thank thanks warning time life "
        "heart matters matter enough everything everyone always afraid "
        "fear pain hope courage badly truth miss tomorrow yesterday chance "
        "listen remember forgive mother father mom dad home alive"
    ).split()},
}

# Multi-word phrases beat single words. Asterisks mark which words inside the
# phrase get coloured; an unmarked phrase is coloured as a whole. Every phrase
# is also kept together on one caption when it fits.
EMPHASIS_PHRASES: dict[str, int] = {
    "thank you": 1,
    "i'm *sorry*": 1,
    "i'm *proud* of you": 1,
    "i *love* you": 1,
    "i *miss* you": 1,
    "last time": 2,
    "last conversation": 2,
    "too late": 2,
    "one day": 1,
    "right now": 1,
    "let *go*": 1,
}

STOPWORDS = set(
    "a an the and or but if so of to in on at by for with from as is are was "
    "were be been being it its it's this that these those you you've you're "
    "your yours i i'm me my we our us he she him her they them their what "
    "which who whom when where why how just don't do does did not no yes all "
    "any some can could would should will shall may might must have has had "
    "there here than then too very really about into over under again only "
    "one ones before after".split()
)

# (trigger words, stock searches). Order of first appearance in the script
# decides shot order, so visuals follow the narrative.
VISUAL_CONCEPTS: list[tuple[frozenset[str], list[str]]] = [
    (frozenset("conversation conversations talk talking said words call phone voice".split()),
     ["phone screen dark night", "two people silhouette sunset"]),
    (frozenset("last time clock warning late before ending final hours years".split()),
     ["clock ticking dark", "train leaving station night"]),
    (frozenset("sorry regret mistake forgive apology wrong".split()),
     ["rain on window night", "man alone window rain"]),
    (frozenset("proud father mother family parent son daughter dad mom".split()),
     ["father and son silhouette", "old hands holding"]),
    (frozenset("thank thanks grateful gratitude".split()),
     ["hands holding close up", "candle flame dark"]),
    (frozenset("today now morning tomorrow day sunrise".split()),
     ["sunrise over city", "morning fog road"]),
    (frozenset("alone lonely nobody someone empty silence quiet".split()),
     ["lonely man walking night", "empty street night rain"]),
    (frozenset("love heart miss missing".split()),
     ["couple silhouette sunset", "old photographs"]),
    (frozenset("die death dead gone grave funeral lost".split()),
     ["cemetery fog", "empty chair dark room"]),
    (frozenset("life living live old young grow growing".split()),
     ["old man looking out window", "city crowd slow motion"]),
    (frozenset("stop wait waiting saving save hold holding".split()),
     ["man standing still crowd", "traffic lights night"]),
    (frozenset("road drive driving journey path way leave leaving".split()),
     ["night drive highway", "car driving rain night"]),
    (frozenset("fear afraid scared dark darkness".split()),
     ["dark forest fog", "storm clouds timelapse"]),
    (frozenset("hope light chance again".split()),
     ["sunlight through clouds", "light at end of tunnel"]),
]

ATMOSPHERE: list[str] = [
    "night city drive",
    "rain window night",
    "dark ocean waves",
    "foggy forest",
    "city lights night",
    "empty road night",
    "neon street rain",
    "storm clouds timelapse",
    "moody mountains fog",
    "streetlight rain night",
]

# The "bright" palette: sunny outdoor scenes with people small in the
# landscape. Every search here is one or two words and was checked on Mixkit's
# vertical listing (2026-10-08, results on the first page): mountains 24+,
# forest 24+, lake 23, clouds 24+, sky clouds 24+, trees 24+, snow 24+,
# flowers 24+, sunset 24+, woman nature 21, beach 20, woman walking 18,
# sea 17, sunlight 16, river 14, field 12, hills 11, man nature 11,
# road trip 10, couple nature 6, hiking 5, camping 4, waterfall 4, sunrise 3.
# Longer phrases match loosely there ("sunset mountains" finds a DJ in a
# desert) or not at all ("alpine lake", "hiker", "tent", "valley"), so keep
# additions short and check them first. Pexels and Pixabay have more of all.
BRIGHT_CONCEPTS: list[tuple[frozenset[str], list[str]]] = [
    (frozenset("conversation conversations talk talking talked said say saying words call called "
               "phone voice listen".split()),
     ["woman nature", "couple nature"]),
    (frozenset("last time times clock late before ending final hours years minutes moment "
               "moments".split()),
     ["sunset", "sky clouds"]),
    (frozenset("sorry regret regrets mistake mistakes forgive forgiven apology wrong".split()),
     ["man nature", "river"]),
    (frozenset("proud father mother family parent parents son daughter dad mom brother sister "
               "child children kids".split()),
     ["woman walking", "field"]),
    (frozenset("thank thanks grateful gratitude".split()),
     ["flowers", "sunlight"]),
    (frozenset("today now morning tomorrow day days sunrise begin beginning start".split()),
     ["sunrise", "field"]),
    (frozenset("alone lonely nobody empty silence solitude".split()),
     ["lake", "man nature"]),
    (frozenset("love loved loving heart miss missing kiss".split()),
     ["couple nature", "sunset"]),
    (frozenset("die dies dying dead death gone grave funeral lost loss".split()),
     ["sky clouds", "snow"]),
    (frozenset("life living live alive old young grow growing age".split()),
     ["forest", "trees"]),
    (frozenset("stop wait waiting slow still pause".split()),
     ["hills", "lake"]),
    (frozenset("road roads drive driving journey path way leave leaving travel".split()),
     ["road trip", "hiking"]),
    (frozenset("fear afraid scared dark darkness storm".split()),
     ["waterfall", "mountains"]),
    (frozenset("hope light chance again believe faith".split()),
     ["sunlight", "sunrise"]),
    (frozenset("memory memories remember forget forgot past".split()),
     ["field", "sunset"]),
    (frozenset("trust truth honest real true".split()),
     ["mountains", "hiking"]),
    (frozenset("free freedom escape fly wild".split()),
     ["sea", "beach"]),
    (frozenset("friend friends people together everyone someone show stay home".split()),
     ["camping", "woman walking"]),
    (frozenset("peace calm rest quiet breathe".split()),
     ["river", "lake"]),
    (frozenset("dream dreams future become change".split()),
     ["clouds", "road trip"]),
    (frozenset("world everything universe earth".split()),
     ["mountains", "sky clouds"]),
    (frozenset("pain hurt broken tears cry".split()),
     ["snow", "river"]),
    (frozenset("walk walking walked steps step run running".split()),
     ["woman walking", "hiking"]),
    # Nature named in the script: show it.
    (frozenset("mountain mountains hill hills peak peaks summit climb top".split()),
     ["mountains", "hiking"]),
    (frozenset("sea seas ocean oceans wave waves shore water".split()),
     ["sea", "beach"]),
    (frozenset("sky skies cloud clouds heaven stars".split()),
     ["sky clouds", "clouds"]),
    (frozenset("sun sunshine summer warm warmth".split()),
     ["sunlight", "beach"]),
    (frozenset("tree trees forest forests woods".split()),
     ["forest", "trees"]),
    (frozenset("river rivers lake lakes stream flow".split()),
     ["river", "lake"]),
    (frozenset("flower flowers spring bloom garden".split()),
     ["flowers", "field"]),
    (frozenset("snow winter cold ice".split()),
     ["snow", "mountains"]),
]

# Fills the shots between concepts, most reference-like first: big
# landscapes, lakes and forest, people small in nature, open sky.
BRIGHT_ATMOSPHERE: list[str] = [
    "mountains",
    "lake",
    "forest",
    "woman nature",
    "sky clouds",
    "hills",
    "sunlight",
    "river",
    "man nature",
    "field",
    "trees",
    "sea",
    "road trip",
    "snow",
]

PALETTES: dict[str, tuple[list[tuple[frozenset[str], list[str]]], list[str]]] = {
    "bright": (BRIGHT_CONCEPTS, BRIGHT_ATMOSPHERE),
    "moody": (VISUAL_CONCEPTS, ATMOSPHERE),
}
