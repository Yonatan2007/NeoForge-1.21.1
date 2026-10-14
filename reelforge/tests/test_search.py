import requests

from reelforge import search


class Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class YouTube:
    """YouTube's suggestion endpoint, answering from a table of queries."""

    def __init__(self, table, fail=()):
        self.table, self.fail, self.asked = table, set(fail), []

    def get(self, url, params=None, timeout=None):
        q = params["q"]
        self.asked.append((url, params))
        if q in self.fail:
            raise requests.ConnectionError("no network")
        return Resp(200, [q, self.table.get(q, []), [], {}])


def test_suggestions_are_read_and_failures_are_empty():
    yt = YouTube({"how to let go of": ["how to let go of someone you love", "how to let go of the past"]},
                 fail=["why do i feel"])
    assert search.suggestions("how to let go of", yt) == ["how to let go of someone you love", "how to let go of the past"]
    url, params = yt.asked[0]
    assert url == search.SUGGEST and params["ds"] == "yt" and params["client"] == "firefox"
    assert search.suggestions("why do i feel", yt) == []
    assert search.suggestions("x", type("S", (), {"get": lambda *a, **k: Resp(500, {})})()) == []
    assert search.suggestions("x", type("S", (), {"get": lambda *a, **k: Resp(200, ValueError("html"))})()) == []


def test_phrases_mix_every_angle_and_drop_other_worlds():
    yt = YouTube({
        "letting go": ["letting go of someone", "letting go lyrics", "letting go"],
        "how to stop overthinking": ["how to stop overthinking", "how to stop overthinking in basketball",
                                     "how to stop overthinking at night"],
    })
    out = search.phrases(session=yt, seeds=["letting go", "how to stop overthinking"])
    # interleaved by rank across seeds; songs and sports are left out
    assert out == ["letting go of someone", "how to stop overthinking", "letting go", "how to stop overthinking at night"]
    assert search.seeds_for("Letting go, self-worth")[:3] == ["letting go", "self-worth", search.SEEDS[0]]
    assert search.seeds_for("")[0] == search.SEEDS[0]
