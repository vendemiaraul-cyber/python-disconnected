"""A small HTTP API so web pages (and the MiniTok JS library) can get what a browser can't fetch itself.

    minitok serve            # http://127.0.0.1:8787
    minitok serve --host 0.0.0.0 --port 8787

Endpoints (all GET, JSON unless a format is asked for, CORS open so any page can call them):

    /health                                  -> {"ok": true, "version": ...}
    /comments?id=<video id>|url=<tiktok link>&limit=30&format=json|csv|txt
                                             -> up to 30 comments (Scrapy, falls back to yt-dlp networking)
    /rank?videos=<id or link>,...&preset=for_you&interests=%23cats:2,@creator:1
                                             -> the videos ranked by MiniTok's algorithm

In the browser: ``MiniTok.mount(el, { comments: MiniTok.commentsFrom("http://127.0.0.1:8787") })``.
Run it on a home connection: TikTok often refuses data-centre servers.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import __version__
from . import comments as comments_io
from .algorithm.ranker import Ranker
from .config import COMMENT_LIMIT, AlgorithmConfig, AppConfig, data_dir
from .downloader import Downloader
from .models import Video
from .storage import Cache

VIDEO_ID = re.compile(r"(\d{8,25})")
CONTENT_TYPES = {"json": "application/json", "csv": "text/csv", "txt": "text/plain"}


def video_from_param(value: str) -> Video | None:
    """A TikTok link or bare id -> Video (None if it isn't one)."""
    value = (value or "").strip()
    m = re.search(r"tiktok\.com/@([\w.-]+)/(?:video|photo)/(\d{8,25})", value)
    if m:
        author, vid = m.group(1), m.group(2)
    else:
        m = VIDEO_ID.fullmatch(value) or re.search(r"/(?:video|player/v1|embed/v2)/(\d{8,25})", value)
        if not m:
            return None
        author, vid = "", m.group(1)
    return Video(id=vid, url=f"https://www.tiktok.com/@{author or '_'}/video/{vid}", author=author)


def parse_interests(text: str) -> dict[str, float]:
    out = {}
    for part in (text or "").split(","):
        tok, _, weight = part.strip().rpartition(":")
        if not tok:
            tok, weight = weight, "1"
        try:
            out[tok.lower()] = max(-3.0, min(3.0, float(weight)))
        except ValueError:
            continue
    return out


class Api:
    def __init__(self, downloader: Downloader):
        self.downloader = downloader
        self.lock = threading.Lock()  # one TikTok fetch at a time is kinder to TikTok and to the cache

    def comments(self, q: dict[str, str]) -> tuple[int, str, Any]:
        video = video_from_param(q.get("url") or q.get("id") or "")
        if video is None:
            return 400, "json", {"error": "Give a TikTok video with ?id=<number> or ?url=<link>"}
        try:
            limit = max(1, min(COMMENT_LIMIT, int(q.get("limit") or COMMENT_LIMIT)))
        except ValueError:
            limit = COMMENT_LIMIT
        fmt = (q.get("format") or "json").lower()
        if fmt not in CONTENT_TYPES:
            return 400, "json", {"error": f"format must be one of {', '.join(CONTENT_TYPES)}"}
        try:
            with self.lock:
                comments = self.downloader.comments(video, limit)
        except Exception as exc:  # noqa: BLE001 - report TikTok/network problems to the caller
            return 502, "json", {"error": str(exc)}
        finally:
            self.downloader.cache.delete_comments(video.id)  # nothing is kept after answering
        if fmt == "json":
            return 200, "json", {"video": {"id": video.id, "url": video.url}, "count": len(comments),
                                 "comments": [c.to_dict() for c in comments]}
        return 200, fmt, comments_io.render(video, comments, fmt)

    def rank(self, q: dict[str, str]) -> tuple[int, str, Any]:
        videos = [v for v in (video_from_param(x) for x in (q.get("videos") or "").split(",")) if v]
        if not videos:
            return 400, "json", {"error": "Give ?videos=<id or link>,<id or link>,..."}
        algo = AlgorithmConfig()
        preset = q.get("preset") or "for_you"
        try:
            algo.apply_preset(preset)
        except KeyError:
            return 400, "json", {"error": f"Unknown preset {preset!r}"}
        algo.exploration = 0.0
        if q.get("interests"):
            algo.interests = parse_interests(q["interests"])
        if q.get("formula"):
            algo.preset, algo.formula = "formula", q["formula"]
        ranker = Ranker(algo)
        error = ranker.validate()
        if error:
            return 400, "json", {"error": error}
        ranked = ranker.rank(videos, [])
        return 200, "json", {"preset": algo.preset, "ranked": [
            {"id": s.video.id, "url": s.video.url, "author": s.video.author, "score": round(s.score, 4),
             "features": {k: round(v, 4) for k, v in s.features.items()}} for s in ranked]}


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"MiniTok/{__version__}"

        def _send(self, status: int, kind: str, payload: Any) -> None:
            body = (json.dumps(payload, ensure_ascii=False) if kind == "json" else str(payload)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", CONTENT_TYPES[kind] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802 - CORS preflight
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            path = url.path.rstrip("/") or "/"
            if path in ("/", "/health"):
                return self._send(200, "json", {"ok": True, "name": "MiniTok API", "version": __version__,
                                                "endpoints": ["/health", "/comments", "/rank"]})
            if path == "/comments":
                return self._send(*api.comments(q))
            if path == "/rank":
                return self._send(*api.rank(q))
            self._send(404, "json", {"error": "Not found. Try /health, /comments or /rank"})

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    return Handler


def make_server(host: str = "127.0.0.1", port: int = 8787, downloader: Downloader | None = None) -> ThreadingHTTPServer:
    downloader = downloader or Downloader(Cache(data_dir() / "server_cache"), AppConfig.load())
    return ThreadingHTTPServer((host, port), make_handler(Api(downloader)))


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    server = make_server(host, port)
    print(f"MiniTok API on http://{host}:{server.server_address[1]}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
