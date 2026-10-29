"""The For You feed: picks videos with the algorithm, prefetches a few, cleans up behind you.

Storage rules
  * video files: only the previous, current and next few videos are kept on disk;
    everything else is deleted as you scroll (files from a local folder are never touched);
  * comments: up to 30 per video are downloaded when the video comes on screen
    and deleted as soon as you move to another video.

This class has no Qt dependency. Work runs on a thread pool and results are
reported through a ``FeedListener`` whose methods may be called from worker
threads (the UI forwards them to the main thread with Qt signals).
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .algorithm.ranker import Ranker, Scored
from .config import COMMENT_LIMIT, AppConfig
from .downloader import Downloader
from .events import EventLog, Impression, update_interests
from .models import Comment, Video
from .sources import Source
from .storage import Cache

LOW_POOL = 8  # refill candidates when fewer than this many are left
PER_SOURCE = 15  # candidates requested from each source per refill


class FeedListener:
    def video_ready(self, video: Video, path: Path) -> None: ...
    def video_failed(self, video: Video, error: str) -> None: ...
    def comments_ready(self, video: Video, comments: list[Comment]) -> None: ...
    def comments_failed(self, video: Video, error: str) -> None: ...
    def status(self, text: str) -> None: ...
    def candidates_ready(self) -> None:
        """New candidates arrived - the UI may now call ``Feed.next()``."""


@dataclass
class WatchStats:
    watched_seconds: float = 0.0
    loops: int = 0
    liked: bool = False
    opened_comments: bool = False


class Feed:
    def __init__(
        self,
        config: AppConfig,
        cache: Cache,
        downloader: Downloader,
        sources: list[Source],
        events: EventLog,
        listener: FeedListener | None = None,
        executor: ThreadPoolExecutor | None = None,
        ranker: Ranker | None = None,
    ):
        self.config = config
        self.cache = cache
        self.downloader = downloader
        self.sources = list(sources)
        self.events = events
        self.listener = listener or FeedListener()
        self.executor = executor or ThreadPoolExecutor(max_workers=3, thread_name_prefix="minitok")
        self.ranker = ranker or Ranker(config.algorithm)

        self.history: list[Scored] = []
        self.index = -1
        self.queue: list[Scored] = []  # picked, not shown yet (being prefetched)
        self.pool: dict[str, Video] = {}
        self.seen: set[str] = set()
        self._recorded: set[int] = set()  # history indexes already logged as impressions
        self._downloads: dict[str, Future] = {}
        self._ready: dict[str, Path] = {}
        self._lock = threading.RLock()
        self._refilling = False
        self._closed = False

    # -- public API (call from the UI thread) -----------------------------------------
    @property
    def current(self) -> Video | None:
        with self._lock:
            return self.history[self.index].video if 0 <= self.index < len(self.history) else None

    @property
    def current_scored(self) -> Scored | None:
        with self._lock:
            return self.history[self.index] if 0 <= self.index < len(self.history) else None

    def start(self) -> None:
        self.listener.status("Finding videos…")
        self._refill()

    def can_advance(self) -> bool:
        with self._lock:
            return self.index + 1 < len(self.history) or bool(self.queue) or bool(self.pool)

    def request_more(self) -> None:
        self._refill()

    def next(self, stats: WatchStats | None = None) -> Video | None:
        """Move to the next video. Returns None (and fetches more) when nothing is available yet."""
        with self._lock:
            if self.index + 1 < len(self.history):
                self._leave_current(stats)
                self.index += 1
            else:
                item = self.queue.pop(0) if self.queue else self._pick()
                if item is None:
                    self._refill()
                    return None
                self._leave_current(stats)
                self.history.append(item)
                self.index = len(self.history) - 1
            self._arrive()
            return self.current

    def previous(self, stats: WatchStats | None = None) -> Video | None:
        with self._lock:
            if self.index <= 0:
                return None
            self._leave_current(stats)
            self.index -= 1
            self._arrive()
            return self.current

    def reload_algorithm(self) -> None:
        """Settings changed: forget the upcoming picks and choose again with the new rules."""
        with self._lock:
            self.ranker = Ranker(self.config.algorithm, self.ranker.rng)
            for item in self.queue:
                self.pool[item.video.id] = item.video
                self.seen.discard(item.video.id)
            self.queue.clear()
            self._cleanup()
            self._prefetch()

    def reload_sources(self, sources: list[Source]) -> None:
        with self._lock:
            self.sources = list(sources)
            self.pool.clear()
            self.queue.clear()
            self._cleanup()
        self._refill()

    def comments_for_current(self) -> None:
        video = self.current
        if video is not None:
            self._submit(self._load_comments, video)

    def shutdown(self, stats: WatchStats | None = None) -> None:
        """Log the last video, stop background work and wipe the cache."""
        with self._lock:
            self._leave_current(stats)
            self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.cache.clear()

    def upcoming(self) -> list[Scored]:
        with self._lock:
            return list(self.queue)

    # -- internals (lock held) ----------------------------------------------------------
    def _leave_current(self, stats: WatchStats | None) -> None:
        item = self.current_scored
        if item is None:
            return
        # comments are only kept while the video is on screen
        self.cache.delete_comments(item.video.id)
        if stats is not None and self.index not in self._recorded:
            self._recorded.add(self.index)
            imp = Impression(
                video=item.video,
                features=item.features,
                watched_seconds=stats.watched_seconds,
                loops=stats.loops,
                liked=stats.liked,
                opened_comments=stats.opened_comments,
                explored=item.explored,
                preset=self.config.algorithm.preset,
            )
            try:
                self.events.append(imp)
            except OSError:
                pass
            algo = self.config.algorithm
            if algo.learn_while_watching and algo.preset not in ("random", "formula"):
                update_interests(algo.interests, imp)
                try:
                    self.config.save()
                except OSError:
                    pass

    def _arrive(self) -> None:
        video = self.current
        assert video is not None
        self._cleanup()
        self._prefetch()
        self._ensure_download(video)
        self._submit(self._load_comments, video)
        if len(self.pool) < LOW_POOL:
            self._refill()

    def _recent_authors(self) -> list[str]:
        return [s.video.author for s in self.history[: self.index + 1] if s.video.author][-30:]

    def _pick(self) -> Scored | None:
        candidates = [v for v in self.pool.values() if v.id not in self.seen]
        recent = self._recent_authors() + [s.video.author for s in self.queue if s.video.author]
        try:
            item = self.ranker.pick(candidates, recent)
        except Exception as exc:  # a broken custom formula must not kill the feed
            self.listener.status(f"Algorithm error: {exc} - using random order")
            self.config.algorithm.preset = "random"
            self.ranker = Ranker(self.config.algorithm, self.ranker.rng)
            item = self.ranker.pick(candidates, recent)
        if item is not None:
            self.pool.pop(item.video.id, None)
            self.seen.add(item.video.id)
        return item

    def _prefetch(self) -> None:
        want = max(1, int(self.config.prefetch))
        while len(self.queue) < want:
            item = self._pick()
            if item is None:
                break
            self.queue.append(item)
        for item in self.queue[:want]:
            self._ensure_download(item.video)

    def _keep_ids(self) -> set[str]:
        keep = {s.video.id for s in self.history[max(0, self.index - 1): self.index + 2]}
        keep |= {s.video.id for s in self.queue[: max(1, int(self.config.prefetch))]}
        return keep

    def _cleanup(self) -> None:
        keep = self._keep_ids()
        for vid in list(self._ready):
            if vid not in keep:
                self._ready.pop(vid, None)
                self.cache.delete_video(vid)
        for vid, fut in list(self._downloads.items()):
            if vid not in keep and fut.cancel():
                self._downloads.pop(vid, None)

    def _ensure_download(self, video: Video) -> None:
        if video.id in self._ready:
            if video is self.current:
                self.listener.video_ready(video, self._ready[video.id])
            return
        if video.id not in self._downloads:
            self._downloads[video.id] = self._submit(self._download, video)

    # -- worker-thread jobs ---------------------------------------------------------------
    def _submit(self, fn, *args) -> Future:
        if self._closed:
            fut: Future = Future()
            fut.cancel()
            return fut
        return self.executor.submit(fn, *args)

    def _download(self, video: Video) -> None:
        try:
            path = self.downloader.download(video)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._downloads.pop(video.id, None)
                in_queue = [s for s in self.queue if s.video.id == video.id]
                for s in in_queue:  # silently replace a failed prefetch
                    self.queue.remove(s)
                is_current = self.current is not None and self.current.id == video.id
                if in_queue and not self._closed:
                    self._prefetch()
            if is_current:
                self.listener.video_failed(video, str(exc))
            return
        with self._lock:
            self._downloads.pop(video.id, None)
            if video.id not in self._keep_ids() and not video.local_path:
                self.cache.delete_video(video.id)  # we scrolled past it while it downloaded
                return
            self._ready[video.id] = path
            is_current = self.current is not None and self.current.id == video.id
        if is_current:
            self.listener.video_ready(video, path)

    def _load_comments(self, video: Video) -> None:
        try:
            comments = self.downloader.comments(video, COMMENT_LIMIT)
        except Exception as exc:  # noqa: BLE001
            if self.current is not None and self.current.id == video.id:
                self.listener.comments_failed(video, str(exc))
            return
        if self.current is None or self.current.id != video.id:
            self.cache.delete_comments(video.id)  # finished after we scrolled away
            return
        self.listener.comments_ready(video, comments)

    def _refill(self) -> None:
        with self._lock:
            if self._refilling or self._closed:
                return
            self._refilling = True
            sources = list(self.sources)
        self._submit(self._refill_job, sources)

    def _refill_job(self, sources: list[Source]) -> None:
        added = 0
        errors = []
        try:
            for src in sources:
                try:
                    found = src.fetch(PER_SOURCE)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{src.name}: {exc}")
                    continue
                with self._lock:
                    for v in found:
                        if v.id not in self.seen and v.id not in self.pool:
                            self.pool[v.id] = v
                            added += 1
        finally:
            with self._lock:
                self._refilling = False
        if errors and not added:
            self.listener.status("Could not load videos - " + "; ".join(errors)[:300])
        with self._lock:
            if self._closed:
                return
            if added:
                self._prefetch()
            has_any = bool(self.queue or self.pool)
        if has_any:
            self.listener.candidates_ready()
        elif not errors:
            if not sources:
                self.listener.status("No sources yet - tap ⚙ and add TikTok @creators, #hashtags or a folder")
            else:
                self.listener.status("No more videos from your sources - add more in ⚙ Settings")
