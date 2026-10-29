"""Downloads video files and comments with yt-dlp."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from .config import COMMENT_LIMIT, AppConfig
from .models import Comment, Video
from .sources import LocalFolderSource
from .storage import Cache, safe_name
from .tiktok_comments import HEADERS, page_url, parse_page

TIKTOK_ID_RE = re.compile(r"tiktokv?\.com/.*?/video/(\d+)")


class DownloadError(RuntimeError):
    pass


class _QuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


class Downloader:
    def __init__(self, cache: Cache, config: AppConfig):
        self.cache = cache
        self.config = config

    def ydl(self, **extra: Any):
        import yt_dlp  # imported lazily: the app starts fast and tests don't need it

        params: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _QuietLogger(),
            "socket_timeout": 20,
            "retries": 2,
        }
        if self.config.cookies_from_browser:
            params["cookiesfrombrowser"] = (self.config.cookies_from_browser,)
        params.update(extra)
        return yt_dlp.YoutubeDL(params)

    # -- video files ----------------------------------------------------------
    def download(self, video: Video) -> Path:
        """Download (or reuse) the file for ``video``; refreshes its stats as a bonus."""
        if video.local_path:
            return Path(video.local_path)
        existing = self.cache.video_file(video.id)
        if existing:
            return existing
        h = int(self.config.max_height or 720)
        fmt = f"b[height<={h}][ext=mp4]/b[height<={h}]/b[ext=mp4]/b"
        try:
            with self.ydl(format=fmt, outtmpl=self.cache.video_template(video.id), noplaylist=True) as ydl:
                info = ydl.extract_info(video.url, download=True)
        except Exception as exc:  # yt-dlp raises many types; surface one readable error
            raise DownloadError(_short_error(exc)) from exc
        if info:
            fresh = Video.from_ytdlp(info, source=video.source)
            for attr in ("views", "likes", "comment_count", "shares", "duration", "created", "music"):
                if getattr(fresh, attr):
                    setattr(video, attr, getattr(fresh, attr))
            if not video.hashtags:
                video.hashtags = fresh.hashtags
        path = self.cache.video_file(video.id)
        if path is None:
            raise DownloadError("yt-dlp finished but no file was written")
        return path

    def metadata(self, url: str) -> Video | None:
        """Look up one video's details without downloading it (used by the learner)."""
        try:
            with self.ydl(noplaylist=True) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
        except Exception:  # noqa: BLE001
            return None
        return Video.from_ytdlp(info) if info else None

    # -- comments (max 30, read-only) ----------------------------------------------
    def comments(self, video: Video, limit: int = COMMENT_LIMIT) -> list[Comment]:
        cached = self.cache.load_comments(video.id)
        if cached is not None:
            return cached[:limit]
        local = LocalFolderSource.comments_for(video, limit)
        if local is not None:
            comments = local
        else:
            m = TIKTOK_ID_RE.search(video.url)
            comments = self._tiktok_comments(m.group(1), limit) if m else self._ytdlp_comments(video, limit)
        comments = comments[:limit]
        self.cache.save_comments(video.id, comments)
        return comments

    def _tiktok_comments(self, aweme_id: str, limit: int) -> list[Comment]:
        """Scrapy first; if Scrapy isn't installed or fails, fetch the same API with yt-dlp's networking."""
        scrapy_error = None
        if importlib.util.find_spec("scrapy") is not None:
            try:
                return self._scrapy_comments(aweme_id, limit)
            except Exception as exc:  # noqa: BLE001
                scrapy_error = exc
        try:
            return self._direct_comments(aweme_id, limit)
        except DownloadError:
            if scrapy_error is not None:
                raise DownloadError(f"Comments unavailable: {_short_error(scrapy_error)}") from scrapy_error
            raise

    def _scrapy_comments(self, aweme_id: str, limit: int) -> list[Comment]:
        from .scrapy_comments import run_in_subprocess

        out = self.cache.comments / f".scrapy_{safe_name(aweme_id)}.json"
        return [Comment.from_dict(c) for c in run_in_subprocess(aweme_id, limit, out)][:limit]

    def _direct_comments(self, aweme_id: str, limit: int) -> list[Comment]:
        from yt_dlp.networking import Request

        out: list[Comment] = []
        cursor = 0
        with self.ydl() as ydl:
            while len(out) < limit:
                try:
                    resp = ydl.urlopen(Request(page_url(aweme_id, cursor, limit - len(out)), headers=HEADERS))
                    data = json.loads(resp.read().decode("utf-8") or "{}")
                except Exception as exc:  # noqa: BLE001
                    if out:
                        break
                    raise DownloadError(f"Comments unavailable: {_short_error(exc)}") from exc
                page, has_more, cursor = parse_page(data)
                out.extend(page)
                if not has_more:
                    break
                cursor = cursor or len(out)
        return out[:limit]

    def _ytdlp_comments(self, video: Video, limit: int) -> list[Comment]:
        """Other sites: let yt-dlp fetch comments (capped where the extractor supports it)."""
        args = {"youtube": {"max_comments": [str(limit), str(limit), "0", "0"], "comment_sort": ["top"]}}
        try:
            with self.ydl(getcomments=True, noplaylist=True, extractor_args=args) as ydl:
                info = ydl.extract_info(video.url, download=False)
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(f"Comments unavailable: {_short_error(exc)}") from exc
        return [Comment.from_ytdlp(c) for c in (info or {}).get("comments") or []][:limit]


def _short_error(exc: BaseException) -> str:
    text = str(exc).replace("ERROR: ", "").strip()
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text.splitlines()[0][:300] if text else exc.__class__.__name__
