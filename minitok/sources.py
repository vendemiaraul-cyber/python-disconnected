"""Where candidate videos come from.

* ``YtDlpSource`` - TikTok ``@user``, ``#hashtag``, a TikTok link, or any URL
  yt-dlp understands (YouTube Shorts channels, playlists ...). Only metadata is
  read here; the video file itself is downloaded later, just before you watch.
* ``LocalFolderSource`` - a folder of video files on your computer. Works fully
  offline. Optional sidecar files add metadata and comments:
  ``clip.mp4`` + ``clip.info.json`` (yt-dlp format) + ``clip.comments.json``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import threading
from pathlib import Path
from typing import Any, Iterator, Protocol

from .models import Comment, Video

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}


class Source(Protocol):
    name: str

    def fetch(self, limit: int) -> list[Video]:
        """Return up to ``limit`` new candidates (an empty list when exhausted)."""
        ...


def normalize_spec(spec: str) -> str:
    spec = spec.strip()
    if spec.startswith("@"):
        return f"https://www.tiktok.com/{spec}"
    if spec.startswith("#"):
        return f"https://www.tiktok.com/tag/{spec[1:]}"
    if spec.isdigit():  # a bare TikTok video id
        return f"https://www.tiktok.com/@_/video/{spec}"
    return spec


class YtDlpSource:
    def __init__(self, spec: str, ydl_factory):
        """``ydl_factory()`` returns a configured ``yt_dlp.YoutubeDL`` (see downloader)."""
        self.spec = spec
        self.url = normalize_spec(spec)
        self.name = spec
        self._factory = ydl_factory
        self._entries: Iterator[dict[str, Any]] | None = None
        self._exhausted = False
        self._lock = threading.Lock()

    def _open(self) -> Iterator[dict[str, Any]]:
        ydl = self._factory(extract_flat="in_playlist")
        info = ydl.extract_info(self.url, download=False, process=False)
        if info is None:
            return iter(())
        if info.get("_type") in ("playlist", "multi_video"):
            return iter(info.get("entries") or ())
        return iter([info])  # a single video

    def fetch(self, limit: int) -> list[Video]:
        with self._lock:
            if self._exhausted:
                return []
            if self._entries is None:
                self._entries = self._open()
            batch = list(itertools.islice(self._entries, limit))
            if len(batch) < limit:
                self._exhausted = True
        out = []
        for entry in batch:
            if not entry:
                continue
            video = Video.from_ytdlp(entry, source=self.spec)
            if video.id and video.url:
                out.append(video)
        return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class LocalFolderSource:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder).expanduser()
        self.name = f"folder:{self.folder.name}"
        self._done = False

    @staticmethod
    def _sidecar(file: Path, suffix: str) -> Path:
        return file.with_name(file.stem + suffix)

    def fetch(self, limit: int) -> list[Video]:
        # a folder is small and finite: hand everything over once
        if self._done or not self.folder.is_dir():
            return []
        self._done = True
        videos = []
        for file in sorted(self.folder.iterdir()):
            if file.suffix.lower() not in VIDEO_EXTS:
                continue
            info = _read_json(self._sidecar(file, ".info.json")) or {}
            vid_id = "local-" + hashlib.sha1(str(file).encode()).hexdigest()[:12]
            if info:
                video = Video.from_ytdlp(info, source=self.name)
                video.id = vid_id
                video.url = video.url or file.as_uri()
            else:
                video = Video(id=vid_id, url=file.as_uri(), author="me",
                              description=file.stem.replace("_", " "), source=self.name)
            video.local_path = str(file)
            videos.append(video)
        return videos

    @classmethod
    def comments_for(cls, video: Video, limit: int) -> list[Comment] | None:
        if not video.local_path:
            return None
        file = Path(video.local_path)
        data = _read_json(cls._sidecar(file, ".comments.json"))
        if data is None:
            data = (_read_json(cls._sidecar(file, ".info.json")) or {}).get("comments")
        if not isinstance(data, list):
            return []
        return [Comment.from_ytdlp(c) for c in data[:limit] if isinstance(c, dict)]
