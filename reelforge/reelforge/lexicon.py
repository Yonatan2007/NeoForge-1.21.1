"""Hand-tuned word lists that encode the aesthetic.

* ``EMPHASIS_WORDS`` / ``EMPHASIS_PHRASES`` decide which words get coloured.
  Level 1 = highlight (yellow), level 2 = alert (red, finality and absolutes).
* ``VISUAL_CONCEPTS`` maps script vocabulary to stock-footage searches.
* ``ATMOSPHERE`` is the moody B-roll palette used to fill the remaining shots.

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
