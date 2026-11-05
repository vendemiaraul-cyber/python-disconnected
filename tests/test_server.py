import json
import threading
import urllib.error
import urllib.request

import pytest

from minitok.models import Comment
from minitok.server import make_server, parse_interests, video_from_param
from minitok.storage import Cache


class FakeDownloader:
    def __init__(self, cache):
        self.cache = cache
        self.asked = []

    def comments(self, video, limit):
        self.asked.append((video.id, limit))
        if video.id == "1111111111":
            raise RuntimeError("TikTok said no")
        cs = [Comment(id=str(i), author=f"u{i}", text=f'hi "{i}"', likes=i) for i in range(50)][:limit]
        self.cache.save_comments(video.id, cs)
        return cs


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    dl = FakeDownloader(Cache(tmp_path / "c"))
    server = make_server("127.0.0.1", 0, downloader=dl)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return r.status, r.headers, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode("utf-8")

    get.dl = dl
    yield get
    server.shutdown()


def test_health_and_cors(api):
    status, headers, body = api("/health")
    assert status == 200 and json.loads(body)["ok"] is True
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_comments_capped_at_30_and_not_kept(api):
    status, _, body = api("/comments?url=https%3A%2F%2Fwww.tiktok.com%2F%40a%2Fvideo%2F7047596209028074758&limit=99")
    data = json.loads(body)
    assert status == 200 and data["count"] == 30 and data["video"]["id"] == "7047596209028074758"
    assert api.dl.asked == [("7047596209028074758", 30)]
    assert not api.dl.cache.comments_file("7047596209028074758").exists()


def test_comments_csv_and_errors(api):
    status, headers, body = api("/comments?id=7047596209028074758&format=csv&limit=2")
    assert status == 200 and headers["Content-Type"].startswith("text/csv") and body.count("\n") == 3
    assert api("/comments?id=nope")[0] == 400
    assert api("/comments?id=7047596209028074758&format=pdf")[0] == 400
    status, _, body = api("/comments?id=1111111111")
    assert status == 502 and "TikTok said no" in json.loads(body)["error"]


def test_rank_endpoint(api):
    status, _, body = api("/rank?videos=7047596209028074758,https://www.tiktok.com/@cats/video/6718335390845095173"
                          "&preset=niche&interests=@cats:3")
    data = json.loads(body)
    assert status == 200 and data["ranked"][0]["author"] == "cats"
    assert api("/rank?videos=1000000000&preset=nope")[0] == 400
    assert api("/rank?videos=1000000000&formula=__import__('os')")[0] == 400
    assert api("/rank")[0] == 400


def test_helpers():
    assert video_from_param("https://www.tiktok.com/@hank/video/7047596209028074758").author == "hank"
    assert video_from_param("https://www.tiktok.com/player/v1/6718335390845095173").id == "6718335390845095173"
    assert video_from_param("hello") is None
    assert parse_interests("#cats:2, @bob, x:bad") == {"#cats": 2.0, "@bob": 1.0}
