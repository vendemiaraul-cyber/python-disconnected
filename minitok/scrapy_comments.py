"""Scrapy spider that downloads up to 30 comments of one TikTok video.

Scrapy runs on Twisted, whose reactor can only start once per process, so the
app runs this module in a short-lived child process for every video:

    python -m minitok.scrapy_comments <video_id> <limit> <out.json>

It writes a JSON list of comments to ``out.json`` (an empty list if there are none).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import scrapy
from scrapy.crawler import CrawlerProcess

from .tiktok_comments import HEADERS, page_url, parse_page


class TikTokCommentsSpider(scrapy.Spider):
    name = "tiktok_comments"
    custom_settings = {
        "LOG_LEVEL": "ERROR",
        "RETRY_TIMES": 1,
        "DOWNLOAD_TIMEOUT": 20,
        "CONCURRENT_REQUESTS": 1,
        "TELNETCONSOLE_ENABLED": False,
        "COOKIES_ENABLED": True,
    }

    def __init__(self, video_id: str, limit: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.video_id = str(video_id)
        self.limit = int(limit)
        self.collected = 0

    async def start(self):
        yield self._request(0)

    def start_requests(self):  # Scrapy < 2.13
        yield self._request(0)

    def _request(self, cursor: int) -> scrapy.Request:
        return scrapy.Request(page_url(self.video_id, cursor, self.limit - self.collected),
                              headers=HEADERS, callback=self.parse, dont_filter=True)

    def parse(self, response):
        try:
            data = json.loads(response.text or "{}")
        except ValueError:
            self.logger.error("TikTok did not return JSON (status %s)", response.status)
            return
        comments, has_more, cursor = parse_page(data)
        for c in comments:
            if self.collected >= self.limit:
                return
            self.collected += 1
            yield c.to_dict()
        if has_more and self.collected < self.limit:
            yield self._request(cursor or self.collected)


def crawl(video_id: str, limit: int, out: Path) -> None:
    process = CrawlerProcess(settings={
        "FEEDS": {str(out): {"format": "json", "encoding": "utf8", "overwrite": True}},
        "LOG_LEVEL": "ERROR",
    })
    process.crawl(TikTokCommentsSpider, video_id=video_id, limit=limit)
    process.start()


def run_in_subprocess(video_id: str, limit: int, out: Path, timeout: float = 45.0) -> list[dict]:
    """Run the spider in a child process and return the scraped comment dicts."""
    out.unlink(missing_ok=True)
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parent.parent)  # works when run from a source checkout too
    env["PYTHONPATH"] = os.pathsep.join(p for p in (package_root, env.get("PYTHONPATH", "")) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "minitok.scrapy_comments", video_id, str(limit), str(out)],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    if proc.returncode != 0 or not out.exists():
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(msg[-1] if msg else f"scrapy exited with code {proc.returncode}")
    try:
        data = json.loads(out.read_text(encoding="utf-8") or "[]")
    finally:
        out.unlink(missing_ok=True)
    return data if isinstance(data, list) else []


if __name__ == "__main__":
    crawl(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]))
