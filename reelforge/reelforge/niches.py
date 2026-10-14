"""What each autopilot account is about.

An account is one niche posted to one Metricool brand (see ``autopilot``):
the main account ("philosophy") and one per niche the user added. A niche
gives Gemini its brief (``ideas.prompt`` and ``ideas.line_prompt``): what
the account is about, the shape of its longer videos and what its comment
videos ask. It also sets the stock footage the reels search for (a
``lexicon`` palette, and the opening shot's searches: the hook is laid
along a skyline) and the real searches its audience makes (``search``).

Two kinds of video (``autopilot.kinds_for``):

* watch: 20-55 s, a narrated script that holds its answer back for the end.
* comment: 7-10 s, one clip and one line on screen (an unfinished sentence
  or a question), music and no voice. People answer in the comments, and the
  short video loops while they read them.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Niche:
    label: str
    about: str                      # "an account of ..." in Gemini's brief
    arc: tuple[str, ...]            # the shape of a longer (watch) video's script
    lines: tuple[str, ...]          # what a comment video's line asks
    examples: tuple[str, ...]       # comment-video lines of the kind wanted (never reused as they are)
    seeds: tuple[str, ...]          # openings of searches this audience makes (search.phrases)
    topic_hint: str = ""            # placeholder of the panel's Topics field
    footage: dict = field(default_factory=dict)   # project "footage" overrides (palette, hook searches ...)
    style: dict = field(default_factory=dict)     # project "style" overrides


NICHES: dict[str, Niche] = {
    "philosophy": Niche(
        label="Late-night philosophy",
        about="late-night philosophical thoughts about love, loss, self-worth and becoming who you are",
        arc=("Build from the hook to an uncomfortable truth, then turn to something hopeful and end on a line "
             "that echoes the hook.",),
        lines=("A line about love, loss, regret or self-worth that people answer from their own life.",),
        examples=("One sentence you never got to say to them:", "The moment you knew you had outgrown someone:",
                  "Something you are still healing from, in three words:"),
        seeds=("how to stop overthinking", "how to let go of", "how to move on from", "why do i feel",
               "how to stop people pleasing", "how to heal from", "how to stop waiting", "how to love yourself",
               "why do i push people away", "how to deal with loneliness", "signs you are",
               "how to stop caring what"),
        topic_hint="e.g. letting go, self-worth, loneliness at night",
    ),
    "dilemmas": Niche(
        label="Would you rather",
        about="moral dilemmas and would-you-rather questions: hard choices that make people stop and pick a side",
        arc=("Open with the dilemma itself: one hard choice between two things people value, in a single line.",
             "Lay out what each side gives and what it costs, fairly, so neither answer is obvious.",
             "Hold the answer back: near the end, say what a well-known thinker or school of thought would "
             "choose and why (name them only when it is accurate).",
             "End by asking which one the viewer would choose."),
        lines=("A would-you-rather or a hard choice between two options that are both tempting or both painful.",),
        examples=("Would you rather know the date you die, or how?",
                  "Would you erase your worst memory if your best one went with it?",
                  "Would you relive your best day or skip your worst one?"),
        seeds=("would you rather", "moral dilemma", "what would you do if", "hard choices", "thought experiment",
               "trolley problem", "deep questions to ask", "philosophy questions"),
        topic_hint="e.g. money or love, time, loyalty, the past",
        # the symbols of a choice (chess, a coin toss, an hourglass, silhouettes), opening on a
        # sunset horizon, in a darker, more dramatic grade than the main account's sunny one
        footage={"palette": "choices", "hook_query": "sun over hills",
                 "hook_searches": ["horizon", "sunset", "hill sunset", "clouds sunset"],
                 "theme": ["chess", "coin", "hourglass", "clock", "watch", "candle", "silhouette",
                           "silhouetted", "sunset", "sun", "horizon", "hill", "hills", "clouds", "sky", "city",
                           "night", "couple", "lighthouse", "photographs", "camera", "storm", "fog", "road",
                           "tracks", "sea", "beach", "desert"]},
        style={"look": {"match": "off", "contrast": 1.08, "saturation": 0.88, "lift": 0.02,
                        "vignette": "PI/5", "grain": 4},
               "hook": {"curve": "gentle"}},  # horizons, not peaks: the opening line bends only a little
    ),
    "psychology": Niche(
        label="Everyday psychology",
        about="everyday psychology: why people think, feel and act the way they do, explained with real research",
        arc=("Open with a behavior almost everyone recognizes in themselves, as a question or a sharp claim.",
             "Hold the reason back: explain it near the end with what research actually found. Name an effect "
             "or a researcher only when you are sure it is real; never invent studies, numbers or names.",
             "End with one small thing the viewer can notice or try.",
             "No 'dark psychology', no manipulation tactics, no diagnosing anyone."),
        lines=("An unfinished sentence or a question about everyday behavior that people answer from their own "
               "life.",),
        examples=("A small thing that makes you trust someone instantly:", "The habit you only have when you're alone:",
                  "What do you overthink the most?"),
        seeds=("why do we", "psychology facts", "why do i always", "human behavior", "psychology of",
               "body language", "why do people", "overthinking"),
        topic_hint="e.g. overthinking, first impressions, habits",
        footage={"palette": "people"},
    ),
    "space": Niche(
        label="Space and science",
        about="space and science: true facts and what-ifs about the universe that make people feel small and "
              "curious",
        arc=("Open with an astonishing true fact or a what-if, in one line.",
             "Build up what it means step by step and keep the most surprising part for the end.",
             "Every fact must be correct and current: round numbers sensibly, no myths, nothing unproven stated "
             "as fact.",
             "End on a line that makes the viewer feel the scale of it."),
        lines=("A question about space, the universe or the future that anyone can answer with an opinion or a "
               "wish.",),
        examples=("If you could ask the universe one question, what would it be?",
                  "Would you go to Mars knowing you could never come back?", "What do you think is out there?"),
        seeds=("what if the sun", "how big is the universe", "space facts", "black hole", "what happens if",
               "is there life on", "how far is", "universe facts"),
        topic_hint="e.g. black holes, the Moon, humans on Mars",
        # starfields have no subject to lose when a wide clip is cropped; the night sky
        # opening keeps the hook on a horizon; the reference grade would grey the dark
        footage={"palette": "space", "hook_query": "night sky",
                 "hook_searches": ["milky way", "starry sky", "stars", "moon"], "allow_landscape": True,
                 "theme": ["space", "star", "starry", "galaxy", "milky", "nebula", "universe", "planet", "moon", "sky",
                           "cosmos", "cosmic", "astronaut", "aurora", "supernova", "earth", "orbit", "sun", "solar",
                           "comet", "satellite", "telescope", "constellation", "astronomy", "rocket"]},
        style={"look": {"match": "off"}},
    ),
}
DEFAULT = "philosophy"


def get(name: str | None) -> Niche:
    return NICHES.get(name or DEFAULT) or NICHES[DEFAULT]


def catalog() -> list[dict]:
    """The niches as the panel shows them (panel-data/niches.json)."""
    return [{"id": k, "label": n.label, "about": n.about, "topic_hint": n.topic_hint, "examples": list(n.examples)}
            for k, n in NICHES.items()]
