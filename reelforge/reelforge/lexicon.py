"""Hand-tuned word lists that encode the aesthetic.

* ``EMPHASIS_WORDS`` / ``EMPHASIS_PHRASES`` decide which words get coloured.
  Level 1 = highlight (yellow), level 2 = alert (red, finality and absolutes).
* ``BRIGHT_CONCEPTS`` / ``BRIGHT_ATMOSPHERE`` (the default "bright" footage
  palette, the reference look) map script vocabulary to sunny nature and
  adventure searches, and fill the remaining shots.
* ``VISUAL_CONCEPTS`` / ``ATMOSPHERE`` are the "moody" palette (night, rain).
* ``PEOPLE_*`` (people in daily life), ``SPACE_*`` (stars, galaxies) and
  ``CHOICES_*`` (chess, clocks, silhouettes) serve the psychology, space and
  would-you-rather accounts (``niches``).
* ``PALETTES`` names them all.

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
# landscape. Mixkit (the keyless default source) matches searches loosely, so
# every search here was checked on its vertical listing (2026-10-08) for both
# the number of clips and what they show: mountains 24+, forest 24+ (meadows,
# waterfalls, families walking), hills 11 (green hills, sun over hills),
# sky clouds 24+, clouds 24+, trees 24+, beach 20, sea 17, sunlight 16,
# sunset 24+, road trip 10, hiking 5 (mountaineers), waterfall 4.
# Rejected after test renders: "woman nature" (picked an indoor yoga shot) and
# "camping" (picked a barbecue grill). Also rejected: "lake" (city
# reflections), "river" (cities, fashion), "field"
# (soccer), "man nature" and "flowers" (studio portraits), "snow" (fashion,
# Christmas), "woman walking" (indoors), "sunrise" (streets), and longer
# phrases, which match loosely ("sunset mountains" finds a DJ in a desert) or
# not at all ("alpine lake", "hiker", "tent", "valley"). Check additions the
# same way. Pexels and Pixabay have plenty of all of these.
BRIGHT_CONCEPTS: list[tuple[frozenset[str], list[str]]] = [
    (frozenset("conversation conversations talk talking talked said say saying words call called "
               "phone voice listen".split()),
     ["beach", "sky clouds"]),
    (frozenset("last time times clock late before ending final hours years minutes moment "
               "moments".split()),
     ["clouds", "sunset"]),
    (frozenset("sorry regret regrets mistake mistakes forgive forgiven apology wrong".split()),
     ["sea", "hiking"]),
    (frozenset("proud father mother family parent parents son daughter dad mom brother sister "
               "child children kids".split()),
     ["forest", "hills"]),
    (frozenset("thank thanks grateful gratitude".split()),
     ["sunlight", "trees"]),
    (frozenset("today now morning tomorrow day days sunrise begin beginning start".split()),
     ["hills", "sunlight"]),
    (frozenset("alone lonely nobody empty silence solitude".split()),
     ["hiking", "sea"]),
    (frozenset("love loved loving heart miss missing kiss".split()),
     ["sunset", "beach"]),
    (frozenset("die dies dying dead death gone grave funeral lost loss".split()),
     ["sky clouds", "mountains"]),
    (frozenset("life living live alive old young grow growing age".split()),
     ["forest", "trees"]),
    (frozenset("stop wait waiting slow still pause".split()),
     ["hills", "clouds"]),
    (frozenset("road roads drive driving journey path way leave leaving travel".split()),
     ["road trip", "hiking"]),
    (frozenset("fear afraid scared dark darkness storm".split()),
     ["waterfall", "mountains"]),
    (frozenset("hope light chance again believe faith".split()),
     ["sunlight", "sky clouds"]),
    (frozenset("memory memories remember forget forgot past".split()),
     ["beach", "sunset"]),
    (frozenset("trust truth honest real true".split()),
     ["mountains", "hiking"]),
    (frozenset("free freedom escape fly wild".split()),
     ["beach", "sea"]),
    (frozenset("friend friends people together everyone someone show stay home".split()),
     ["road trip", "beach"]),
    (frozenset("peace calm rest quiet breathe".split()),
     ["sea", "trees"]),
    (frozenset("dream dreams future become change".split()),
     ["clouds", "road trip"]),
    (frozenset("world everything universe earth".split()),
     ["mountains", "sky clouds"]),
    (frozenset("pain hurt broken tears cry".split()),
     ["sky clouds", "hills"]),
    (frozenset("walk walking walked steps step run running".split()),
     ["hiking", "forest"]),
    # Nature named in the script: show it (or its nearest good search).
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
     ["waterfall", "hills"]),
    (frozenset("flower flowers spring bloom garden".split()),
     ["trees", "sunlight"]),
    (frozenset("snow winter cold ice".split()),
     ["mountains", "forest"]),
]

# Fills the shots between concepts, most reference-like first: big
# landscapes, people small in nature, open sky.
BRIGHT_ATMOSPHERE: list[str] = [
    "mountains",
    "forest",
    "hills",
    "sky clouds",
    "hiking",
    "waterfall",
    "beach",
    "road trip",
    "trees",
    "clouds",
    "sunlight",
    "sea",
    "sunset",
]

# The "people" palette (everyday psychology): people in daily life. Checked
# on Mixkit's vertical listing (2026-10-14): phone, eyes, hands, office,
# conversation, laptop, mirror, smile 24+; city street 23; friends talking 14;
# subway 13; bed 13; thinking 12; coffee shop 10; people walking 9; texting 8;
# couple talking 7; woman window 7; walking city night 4.
PEOPLE_CONCEPTS: list[tuple[frozenset[str], list[str]]] = [
    (frozenset("trust friend friends friendship talk talking conversation said say words".split()),
     ["friends talking", "conversation"]),
    (frozenset("phone text texting message messages scroll scrolling screen online social notification".split()),
     ["texting", "phone"]),
    (frozenset("eyes eye look looking stare staring face faces contact".split()),
     ["eyes", "woman window"]),
    (frozenset("think thinking thought thoughts overthink overthinking mind brain memory memories replay".split()),
     ["thinking", "woman window"]),
    (frozenset("sleep night bed tired dream dreams awake".split()),
     ["bed", "walking city night"]),
    (frozenset("work job boss money office busy productive procrastinate procrastination".split()),
     ["office", "laptop"]),
    (frozenset("love partner relationship date couple crush".split()),
     ["couple talking", "friends talking"]),
    (frozenset("alone lonely loneliness quiet silence introvert introverts".split()),
     ["woman window", "walking city night"]),
    (frozenset("people crowd strangers stranger everyone others group".split()),
     ["people walking", "city street"]),
    (frozenset("smile smiling laugh laughing happy happiness".split()),
     ["smile", "friends laughing"]),
    (frozenset("mirror yourself self confidence image".split()),
     ["mirror", "eyes"]),
    (frozenset("habit habits routine morning coffee".split()),
     ["coffee shop", "cafe"]),
    (frozenset("anxiety anxious stress stressed fear nervous worry worried".split()),
     ["hands", "thinking"]),
    (frozenset("decision decisions choose choice choices".split()),
     ["city street", "subway"]),
]

PEOPLE_ATMOSPHERE: list[str] = [
    "city street",
    "people walking",
    "thinking",
    "subway",
    "coffee shop",
    "conversation",
    "hands",
    "phone",
    "walking city night",
]

# The "space" palette: stars, galaxies and planets. Mixkit's vertical listing
# (2026-10-14) is thin here (stars 24, universe 24, planet 23, galaxy 12,
# nebula 5, night sky 4, moon 4), so space reels may crop wide clips too.
SPACE_CONCEPTS: list[tuple[frozenset[str], list[str]]] = [
    (frozenset("galaxy galaxies milky universe cosmos cosmic".split()),
     ["galaxy", "universe"]),
    (frozenset("star stars starlight constellation constellations".split()),
     ["stars", "night sky"]),
    (frozenset("planet planets earth world mars jupiter saturn venus orbit".split()),
     ["planet", "space"]),
    (frozenset("moon moons lunar".split()),
     ["moon", "night sky"]),
    (frozenset("sun suns solar light heat burn burning".split()),
     ["sun", "stars"]),
    (frozenset("black hole holes gravity dark darkness collapse".split()),
     ["nebula", "space"]),
    (frozenset("explode explosion exploded supernova die dies dying".split()),
     ["galaxy", "nebula"]),
    (frozenset("time years billion million age ancient future".split()),
     ["universe", "stars"]),
    (frozenset("astronaut astronauts human humans rocket travel".split()),
     ["astronaut", "planet"]),
]

SPACE_ATMOSPHERE: list[str] = [
    "stars",
    "galaxy",
    "universe",
    "nebula",
    "planet",
    "space",
    "night sky",
    "milky way",
    "moon",
]

# The "choices" palette (would-you-rather dilemmas): the symbols of a hard
# choice. Checked on Mixkit's vertical listing (2026-10-14): chess, clock,
# candle, sunset, silhouette 24+ ("mirror" too, but it brings brides and car
# mirrors); city night 23; man silhouette 13;
# couple silhouette 11; dark clouds 5; sun over hills 3; coin (a coin toss),
# hourglass, old photographs 1-2 vertical and more wide ones.
CHOICES_CONCEPTS: list[tuple[frozenset[str], list[str]]] = [
    (frozenset("choose choice choices decide decision decisions either rather option options pick".split()),
     ["chess", "coin"]),
    (frozenset("time years year date die death dies dying future past forever age days day minutes".split()),
     ["hourglass", "clock"]),
    (frozenset("money million millions rich dollars pay price cost wealth".split()),
     ["coin", "city night"]),
    (frozenset("love partner heart relationship soulmate marry married".split()),
     ["couple silhouette", "sunset"]),
    (frozenset("memory memories remember forget forgot childhood".split()),
     ["old photographs", "candle"]),
    (frozenset("truth lie lies secret secrets honest know knowing".split()),
     ["candle", "dark clouds"]),
    (frozenset("yourself self face who".split()),
     ["man silhouette", "candle"]),
    (frozenset("alone lonely friends friend family people everyone world".split()),
     ["man silhouette", "city night"]),
    (frozenset("fear dark darkness pain lose loss".split()),
     ["dark clouds", "candle"]),
    (frozenset("win game power control".split()),
     ["chess", "dark clouds"]),
    (frozenset("hope light free freedom escape".split()),
     ["sunset", "sun over hills"]),
]

CHOICES_ATMOSPHERE: list[str] = [
    "chess",
    "hourglass",
    "silhouette",
    "sunset",
    "candle",
    "clock",
    "city night",
    "dark clouds",
    "coin",
    "sunset over train tracks",
]

PALETTES: dict[str, tuple[list[tuple[frozenset[str], list[str]]], list[str]]] = {
    "bright": (BRIGHT_CONCEPTS, BRIGHT_ATMOSPHERE),
    "moody": (VISUAL_CONCEPTS, ATMOSPHERE),
    "people": (PEOPLE_CONCEPTS, PEOPLE_ATMOSPHERE),
    "space": (SPACE_CONCEPTS, SPACE_ATMOSPHERE),
    "choices": (CHOICES_CONCEPTS, CHOICES_ATMOSPHERE),
}
