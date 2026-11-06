import time
from unittest.mock import MagicMock

import pytest

from videowatcher import create_app
from videowatcher.models import Video
from videowatcher.providers.archive import ArchiveProvider
from videowatcher.providers.base import Provider
from videowatcher.providers.dailymotion import DailymotionProvider
from videowatcher.providers.peertube import PeerTubeProvider
from videowatcher.providers.youtube import YouTubeProvider
from videowatcher.search import SearchAggregator, interleave


class FakeProvider(Provider):
    def __init__(self, key, n=3, error=None, delay=0.0, ads=False):
        super().__init__()
        self.key, self.name, self.domain, self.ad_supported = key, key.title(), f"{key}.test", ads
        self.n, self.error, self.delay, self.calls = n, error, delay, 0

    def search(self, query, limit):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return [
            self._video(id=f"{self.key}{i}", title=f"{query} {i}", url=f"https://{self.domain}/{i}",
                        embed_url=f"https://{self.domain}/embed/{i}")
            for i in range(min(self.n, limit))
        ]


def mock_session(payload):
    s = MagicMock()
    s.headers = {}
    s.get.return_value.json.return_value = payload
    return s


@pytest.fixture
def client():
    agg = SearchAggregator({"a": FakeProvider("a", ads=True), "b": FakeProvider("b", n=1)})
    return create_app(agg).test_client()


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Video Watcher" in r.data


def test_search_merges_sources_round_robin(client):
    data = client.get("/api/search?q=cats").get_json()
    assert [v["id"] for v in data["results"]] == ["a0", "b0", "a1", "a2"]
    assert data["results"][0]["ad_supported"] is True
    assert data["results"][0]["source_domain"] == "a.test"
    assert data["sources"]["a"] == {"ok": True, "count": 3, "error": None}


def test_search_filters_sources(client):
    data = client.get("/api/search?q=cats&sources=b").get_json()
    assert {v["source"] for v in data["results"]} == {"b"}


@pytest.mark.parametrize("qs", ["", "?q=", "?q=x&limit=abc", "?q=" + "x" * 201])
def test_search_validation(client, qs):
    assert client.get("/api/search" + qs).status_code == 400


def test_failing_and_slow_sources_are_isolated():
    agg = SearchAggregator(
        {"ok": FakeProvider("ok"), "bad": FakeProvider("bad", error=RuntimeError("boom")),
         "slow": FakeProvider("slow", delay=1.0)},
        deadline=0.3,
    )
    data = agg.search("x")
    assert len(data["results"]) == 3
    assert data["sources"]["bad"]["error"] == "boom"
    assert data["sources"]["slow"]["error"] == "timed out"


def test_cache_only_when_all_sources_ok():
    good = FakeProvider("g")
    agg = SearchAggregator({"g": good})
    agg.search("x"); agg.search("X")
    assert good.calls == 1
    bad = FakeProvider("b", error=RuntimeError())
    agg2 = SearchAggregator({"b": bad})
    agg2.search("x"); agg2.search("x")
    assert bad.calls == 2


def test_interleave_dedupes():
    v = Video(id="1", title="t", url="u", embed_url="e", source="s", source_name="S",
              source_domain="d", ad_supported=False)
    assert len(interleave([[v, v], [v]])) == 1


def test_youtube_uses_official_embed_and_skips_channels():
    p = YouTubeProvider()
    v = p._parse({"id": "abc", "title": "T", "duration": 61.0, "channel": "C"})
    assert v.embed_url.startswith("https://www.youtube.com/embed/abc")
    assert v.ad_supported and v.duration == 61 and v.source_domain == "youtube.com"
    assert p._parse({"id": "UCx", "ie_key": "YoutubeTab"}) is None


def test_dailymotion_parse():
    p = DailymotionProvider(session=mock_session({"list": [
        {"id": "x1", "title": "T", "url": "https://www.dailymotion.com/video/x1",
         "owner.screenname": "Me", "created_time": 0}]}))
    [v] = p.search("q", 5)
    assert v.embed_url.startswith("https://geo.dailymotion.com/player.html?video=x1")
    assert v.channel == "Me" and v.published == "1970-01-01" and v.ad_supported


def test_peertube_reports_instance_domain_and_rejects_insecure_embeds():
    p = PeerTubeProvider(session=mock_session({"data": [
        {"uuid": "u1", "name": "T", "url": "https://tube.example/w/u1",
         "embedUrl": "https://tube.example/videos/embed/u1", "channel": {"displayName": "Ch"}},
        {"uuid": "u2", "name": "Bad", "url": "https://x/w", "embedUrl": "javascript:alert(1)"}]}))
    [v] = p.search("q", 5)
    assert v.source_domain == "tube.example" and not v.ad_supported and v.channel == "Ch"


def test_archive_parse():
    p = ArchiveProvider(session=mock_session({"response": {"docs": [
        {"identifier": "film1", "title": ["Film"], "creator": ["A", "B"], "date": "1950-01-01T00:00:00Z"}]}}))
    [v] = p.search("q", 5)
    assert v.embed_url.startswith("https://archive.org/embed/film1")
    assert v.title == "Film" and v.channel == "A, B" and v.published == "1950-01-01"
