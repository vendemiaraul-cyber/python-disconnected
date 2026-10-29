import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from minitok.config import COMMENT_LIMIT, AppConfig
from minitok.events import EventLog
from minitok.feed import Feed, FeedListener, WatchStats
from minitok.models import Comment, Video
from minitok.storage import Cache


class FakeSource:
    name = "fake"

    def __init__(self, n):
        self.videos = [Video(id=f"v{i}", url=f"https://example.com/{i}", author=f"a{i}",
                             description=f"#t{i % 3}", views=10 * i, duration=10) for i in range(n)]

    def fetch(self, limit):
        out, self.videos = self.videos[:limit], self.videos[limit:]
        return out


class FakeDownloader:
    def __init__(self, cache):
        self.cache = cache

    def download(self, video):
        path = Path(self.cache.video_template(video.id).replace("%(ext)s", "mp4"))
        path.write_bytes(b"fake video")
        return path

    def comments(self, video, limit):
        comments = [Comment(id=str(i), author="x", text="hi") for i in range(50)][:limit]
        self.cache.save_comments(video.id, comments)
        return comments


class Recorder(FeedListener):
    """Acts like the UI: starts playing as soon as candidates arrive."""

    def __init__(self):
        self.ready, self.comments, self.statuses = [], [], []
        self.feed = None

    def candidates_ready(self):
        if self.feed.current is None:
            self.feed.next()

    def video_ready(self, video, path):
        self.ready.append(video.id)

    def comments_ready(self, video, comments):
        self.comments.append((video.id, len(comments)))

    def status(self, text):
        self.statuses.append(text)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def feed(tmp_path):
    cfg = AppConfig()
    cfg.algorithm.exploration = 0.0
    cfg.prefetch = 2
    cache = Cache(tmp_path / "cache")
    rec = Recorder()
    f = Feed(cfg, cache, FakeDownloader(cache), [FakeSource(30)], EventLog(tmp_path / "events.jsonl"),
             listener=rec, executor=ThreadPoolExecutor(2))
    f.rec = rec
    rec.feed = f
    yield f
    f.executor.shutdown(wait=True)


def video_files(feed):
    return {p.stem for p in feed.cache.videos.iterdir()}


def test_feed_starts_downloads_and_caps_comments(feed):
    feed.start()
    wait_for(lambda: feed.current is not None and feed.rec.comments)
    first = feed.current.id
    wait_for(lambda: first in feed.rec.ready)
    assert feed.rec.comments[0] == (first, COMMENT_LIMIT)
    assert feed.cache.comments_file(first).exists()


def test_comments_deleted_when_moving_on_and_old_videos_cleaned(feed):
    feed.start()
    wait_for(lambda: feed.current is not None)
    seen = []
    for _ in range(6):
        cur = feed.current.id
        wait_for(lambda: feed.cache.comments_file(cur).exists())
        seen.append(cur)
        feed.next(WatchStats(watched_seconds=10, loops=1))
        assert not feed.cache.comments_file(cur).exists()
    feed.executor.shutdown(wait=True)
    files = video_files(feed)
    # only previous + current + prefetched videos remain on disk
    assert set(seen[:-1]).isdisjoint(files)
    assert files <= {seen[-1], feed.current.id, *(s.video.id for s in feed.queue)}
    assert len(files) <= 1 + 1 + feed.config.prefetch


def test_back_navigation_and_impressions_logged_once(feed):
    feed.start()
    wait_for(lambda: feed.current is not None)
    a = feed.current.id
    feed.next(WatchStats(watched_seconds=1))
    b = feed.current.id
    assert feed.previous(WatchStats(watched_seconds=3)).id == a
    assert feed.next(WatchStats(watched_seconds=1)).id == b
    logged = [imp.video.id for imp in feed.events]
    assert logged == [a, b]


def test_learning_while_watching_updates_interests(feed):
    feed.config.algorithm.interests.clear()
    feed.start()
    wait_for(lambda: feed.current is not None)
    v = feed.current
    feed.next(WatchStats(watched_seconds=10, liked=True))
    assert feed.config.algorithm.interests[f"@{v.author}"] > 0


def test_shutdown_clears_cache(feed):
    feed.start()
    wait_for(lambda: feed.current is not None and feed.rec.ready)
    feed.executor.shutdown(wait=True)
    feed.shutdown()
    assert not any(feed.cache.videos.iterdir()) and not any(feed.cache.comments.iterdir())


def test_no_sources_reports_status(tmp_path):
    cache = Cache(tmp_path / "c")
    rec = Recorder()
    f = Feed(AppConfig(), cache, FakeDownloader(cache), [], EventLog(tmp_path / "e"), listener=rec,
             executor=ThreadPoolExecutor(1))
    f.start()
    wait_for(lambda: any("No sources" in s for s in rec.statuses))
    f.executor.shutdown(wait=True)
