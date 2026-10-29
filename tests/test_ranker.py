import random

from minitok.algorithm.ranker import Ranker
from minitok.config import AlgorithmConfig
from minitok.models import Video


def cfg(preset="niche", **kw):
    c = AlgorithmConfig()
    c.apply_preset(preset)
    c.exploration = 0.0
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_interest_preset_prefers_matching_video():
    cats = Video(id="c", url="u", description="#cats", views=100)
    viral = Video(id="v", url="u", description="#news", views=50_000_000, likes=5_000_000)
    ranker = Ranker(cfg("niche", interests={"#cats": 2.0}), random.Random(0))
    assert ranker.pick([viral, cats], []).video.id == "c"


def test_popular_preset_prefers_viral_video():
    cats = Video(id="c", url="u", description="#cats", views=100)
    viral = Video(id="v", url="u", description="#news", views=50_000_000, likes=5_000_000)
    ranker = Ranker(cfg("popular", interests={"#cats": 2.0}), random.Random(0))
    assert ranker.pick([cats, viral], []).video.id == "v"


def test_blocked_words_tags_and_creators_are_never_shown():
    a = Video(id="a", url="u", description="free giveaway now")
    b = Video(id="b", url="u", description="#spam")
    c = Video(id="c", url="u", author="Bot", description="x")
    d = Video(id="d", url="u", description="fine")
    ranker = Ranker(cfg(blocked=["giveaway", "#spam", "@bot"]), random.Random(0))
    assert [s.video.id for s in ranker.rank([a, b, c, d], [])] == ["d"]


def test_diversity_pushes_down_creator_just_seen():
    same = Video(id="s", url="u", author="amy", description="#cats")
    other = Video(id="o", url="u", author="bob", description="#cats")
    ranker = Ranker(cfg("niche", interests={"#cats": 1.0}, diversity=1.0), random.Random(0))
    assert ranker.pick([same, other], ["amy"]).video.id == "o"


def test_formula_preset():
    long = Video(id="l", url="u", duration=60)
    short = Video(id="s", url="u", duration=5)
    c = cfg("formula", formula="-duration")
    assert Ranker(c, random.Random(0)).pick([long, short], []).video.id == "s"


def test_exploration_sometimes_picks_random():
    vids = [Video(id=str(i), url="u", views=10 ** i) for i in range(6)]
    c = cfg("popular", exploration=1.0)
    picks = {Ranker(c, random.Random(s)).pick(vids, []).video.id for s in range(30)}
    assert len(picks) > 2
