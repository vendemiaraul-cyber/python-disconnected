import json

import pytest

from minitok.config import AppConfig
from minitok.downloader import Downloader, DownloadError
from minitok.models import Video
from minitok.storage import Cache

scrapy = pytest.importorskip("scrapy")
from scrapy.http import Request, TextResponse  # noqa: E402

from minitok.scrapy_comments import TikTokCommentsSpider  # noqa: E402


def page(start, n, has_more, cursor):
    return {
        "comments": [{"cid": str(i), "text": f"c{i}", "digg_count": i, "create_time": 1700000000,
                      "reply_comment_total": 0, "user": {"unique_id": f"u{i}"}} for i in range(start, start + n)],
        "has_more": has_more,
        "cursor": cursor,
    }


def respond(req, data):
    return TextResponse(url=req.url, body=json.dumps(data).encode(), encoding="utf-8", request=req)


def test_spider_paginates_and_stops_at_limit():
    spider = TikTokCommentsSpider(video_id="123", limit=30)
    first = spider._request(0)
    assert "aweme_id=123" in first.url and "count=30" in first.url
    out = list(spider.parse(respond(first, page(0, 20, True, 20))))
    items = [o for o in out if isinstance(o, dict)]
    [nxt] = [o for o in out if isinstance(o, Request)]
    assert len(items) == 20 and items[0]["author"] == "u0" and items[0]["likes"] == 0
    assert "cursor=20" in nxt.url and "count=10" in nxt.url
    out2 = list(spider.parse(respond(nxt, page(20, 20, True, 40))))
    assert len(out2) == 10 and all(isinstance(o, dict) for o in out2)  # capped at 30, no further request


def test_spider_handles_non_json():
    spider = TikTokCommentsSpider(video_id="1")
    req = spider._request(0)
    resp = TextResponse(url=req.url, body=b"<html>blocked</html>", encoding="utf-8", request=req)
    assert list(spider.parse(resp)) == []


def test_downloader_prefers_scrapy_then_falls_back(tmp_path, monkeypatch):
    dl = Downloader(Cache(tmp_path), AppConfig())
    video = Video(id="v", url="https://www.tiktok.com/@a/video/42")
    calls = []

    def scrapy_ok(aweme_id, limit, out):
        calls.append(("scrapy", aweme_id, limit))
        return [{"id": str(i), "author": "a", "text": "t"} for i in range(40)]

    monkeypatch.setattr("minitok.scrapy_comments.run_in_subprocess", scrapy_ok)
    assert len(dl.comments(video)) == 30 and calls == [("scrapy", "42", 30)]

    dl.cache.delete_comments("v")

    def scrapy_broken(*_a):
        raise RuntimeError("blocked")

    monkeypatch.setattr("minitok.scrapy_comments.run_in_subprocess", scrapy_broken)
    monkeypatch.setattr(Downloader, "_direct_comments", lambda self, a, n: [])
    assert dl.comments(video) == []

    dl.cache.delete_comments("v")

    def direct_broken(self, a, n):
        raise DownloadError("also blocked")

    monkeypatch.setattr(Downloader, "_direct_comments", direct_broken)
    with pytest.raises(DownloadError, match="blocked"):
        dl.comments(video)


def test_real_scrapy_subprocess_against_local_server(tmp_path, monkeypatch):
    """Runs the actual spider in its child process against a fake comment API on localhost."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    from minitok.scrapy_comments import run_in_subprocess

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            cursor, count = int(q["cursor"][0]), int(q["count"][0])
            body = json.dumps(page(cursor, min(count, 20), True, cursor + 20)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("MINITOK_TIKTOK_COMMENT_API", f"http://127.0.0.1:{server.server_port}/api/comment/list/")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    try:
        items = run_in_subprocess("99", 30, tmp_path / "out.json", timeout=60)
    finally:
        server.shutdown()
    assert len(items) == 30
    assert [c["text"] for c in items[:3]] == ["c0", "c1", "c2"]
    assert not (tmp_path / "out.json").exists()
