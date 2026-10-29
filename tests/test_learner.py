import random

from minitok.algorithm.learner import examples_from_export, examples_from_history, fit_logistic, learn
from minitok.algorithm.tiktok_export import ExportData, Record
from minitok.events import Impression
from minitok.models import Video


def test_logistic_regression_finds_signal():
    rng = random.Random(0)
    rows, labels = [], []
    for _ in range(300):
        a, b = rng.random(), rng.random()
        rows.append([a, b])
        labels.append(1.0 if a > 0.5 else 0.0)  # only feature a matters
    w, _ = fit_logistic(rows, labels)
    assert w[0] > 3 * abs(w[1])


def synthetic_history(n=200, seed=1):
    """A viewer who loves #cats and short videos, and skips everything else."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        topic = rng.choice(["cats", "news", "gym", "cars"])
        dur = rng.choice([8, 12, 45, 60])
        v = Video(id=str(i), url="u", author=f"{topic}{rng.randint(0, 5)}", description=f"clip #{topic}",
                  duration=dur, views=rng.randint(100, 10 ** 6), likes=rng.randint(1, 1000))
        enjoys = topic == "cats" and dur <= 12
        watched = dur * (1.0 if enjoys else 0.05)
        out.append(Impression(video=v, watched_seconds=watched, liked=enjoys and rng.random() < 0.3))
    return out


def test_learns_interests_duration_and_weights_from_app_history():
    result = learn(examples_from_history(synthetic_history()))
    assert result.examples == 200
    assert result.interests["#cats"] > 1.0
    assert result.interests.get("#news", 0) < 0
    assert result.preferred_duration <= 12
    assert result.weights["interest"] > 0
    assert result.weights["duration_fit"] > 0
    assert result.accuracy > 0.85
    assert any("interest" in line for line in result.report)


def test_too_little_data_still_returns_interests():
    result = learn(examples_from_history(synthetic_history(5)))
    assert "not enough" in result.report[0]


def test_examples_from_export_labels_with_fetched_metadata():
    data = ExportData(
        watched=[Record(ts=0, link="/video/1/"), Record(ts=2, link="/video/2/"), Record(ts=50, link="/video/3/"),
                 Record(ts=51, link="/video/4/")],
        liked=[Record(ts=10, link="/video/3/")],
    )
    meta = {"1": 30, "2": 40, "3": 20, "4": 9}

    def fetch(url):
        vid = url.rstrip("/").rsplit("/", 1)[-1]
        return Video(id=vid, url=url, duration=meta[vid], description="#x")

    exs = {e.video.id: e.label for e in examples_from_export(data, fetch, sample=10)}
    assert exs["1"] == 0.0  # 2s on a 30s video -> skip
    assert exs["2"] == 1.0  # 48s on a 40s video -> finished
    assert exs["3"] == 1.0  # liked
