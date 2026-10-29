import pytest

from minitok.algorithm.features import ViewerState, extract
from minitok.algorithm.formula import Formula, FormulaError
from minitok.models import Video


def feats(video):
    return extract(video, ViewerState(interests={"#cats": 2.0}))


def test_basic_arithmetic_and_features():
    v = Video(id="1", url="u", description="hi #cats", views=1000, likes=100)
    f = Formula("2*interest + like_rate")
    expected = 2 * feats(v)["interest"] + feats(v)["like_rate"]
    assert f.evaluate(feats(v), v) == pytest.approx(expected)


def test_has_contains_and_conditionals():
    v = Video(id="1", url="u", author="Chef", description="Pasta night #cooking")
    f = Formula('(5 if has("#cooking") else 0) + contains("pasta") + has("@chef") - has("#cats")')
    assert f.evaluate(feats(v), v) == 7.0


def test_raw_variables():
    v = Video(id="1", url="u", views=999, duration=12)
    assert Formula("log10(views + 1) + duration / 12").evaluate(feats(v), v) == pytest.approx(4.0)


@pytest.mark.parametrize(
    "src",
    [
        "__import__('os').system('echo hi')",
        "views.__class__",
        "open('x')",
        "[1, 2]",
        "lambda: 1",
        "unknown_var + 1",
        "",
        "1 +",
    ],
)
def test_rejects_unsafe_or_invalid(src):
    with pytest.raises(FormulaError):
        Formula(src)


def test_division_by_zero_is_zero():
    v = Video(id="1", url="u")
    assert Formula("1 / views").evaluate(feats(v), v) == 0.0
