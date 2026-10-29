"""The on-disk cache. Everything here is temporary and gets deleted as you scroll."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .models import Comment

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_name(video_id: str) -> str:
    return _SAFE.sub("_", video_id)[:120] or "video"


class Cache:
    def __init__(self, root: Path):
        self.root = root
        self.videos = root / "videos"
        self.comments = root / "comments"
        self.videos.mkdir(parents=True, exist_ok=True)
        self.comments.mkdir(parents=True, exist_ok=True)

    # -- videos -------------------------------------------------------------
    def video_template(self, video_id: str) -> str:
        """yt-dlp output template for this video."""
        return str(self.videos / f"{safe_name(video_id)}.%(ext)s")

    def video_file(self, video_id: str) -> Path | None:
        stem = safe_name(video_id)
        for p in self.videos.glob(f"{stem}.*"):
            if p.suffix in (".part", ".ytdl", ".tmp") or ".part" in p.name:
                continue
            return p
        return None

    def delete_video(self, video_id: str) -> None:
        stem = safe_name(video_id)
        for p in self.videos.glob(f"{stem}.*"):
            p.unlink(missing_ok=True)

    # -- comments -------------------------------------------------------------
    def comments_file(self, video_id: str) -> Path:
        return self.comments / f"{safe_name(video_id)}.json"

    def save_comments(self, video_id: str, comments: list[Comment]) -> Path:
        path = self.comments_file(video_id)
        path.write_text(json.dumps([c.to_dict() for c in comments], ensure_ascii=False), encoding="utf-8")
        return path

    def load_comments(self, video_id: str) -> list[Comment] | None:
        path = self.comments_file(video_id)
        if not path.exists():
            return None
        try:
            return [Comment.from_dict(c) for c in json.loads(path.read_text(encoding="utf-8"))]
        except (ValueError, TypeError):
            return None

    def delete_comments(self, video_id: str) -> None:
        self.comments_file(video_id).unlink(missing_ok=True)

    # -- housekeeping -------------------------------------------------------------
    def clear(self) -> None:
        for d in (self.videos, self.comments):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
