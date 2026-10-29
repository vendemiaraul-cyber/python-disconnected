"""Plain data objects shared by the feed, the algorithm and the UI."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)


@dataclass
class Video:
    id: str
    url: str
    author: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    music: str = ""
    duration: float = 0.0
    views: int = 0
    likes: int = 0
    comment_count: int = 0
    shares: int = 0
    created: float = 0.0  # unix timestamp, 0 = unknown
    source: str = ""
    local_path: str | None = None  # a file we may play directly (never deleted)
    thumbnail: str = ""

    def __post_init__(self) -> None:
        if not self.hashtags and self.description:
            self.hashtags = extract_hashtags(self.description)
        self.hashtags = [h.lower().lstrip("#") for h in self.hashtags]

    @property
    def age_hours(self) -> float | None:
        if not self.created:
            return None
        return max(0.0, (time.time() - self.created) / 3600.0)

    def tokens(self) -> list[str]:
        """Interest tokens this video carries (used by the interest profile)."""
        toks = [f"#{h}" for h in self.hashtags]
        if self.author:
            toks.append(f"@{self.author.lower().lstrip('@')}")
        if self.music:
            toks.append(f"♪{self.music.lower()}")
        text = HASHTAG_RE.sub(" ", self.description.lower())
        toks.extend(w for w in re.findall(r"[^\W\d_]{4,}", text))
        # de-duplicate, keep order
        return list(dict.fromkeys(toks))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Video":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_ytdlp(cls, info: dict[str, Any], source: str = "") -> "Video":
        """Build a Video from a yt-dlp info dict (works for TikTok and most sites)."""
        desc = info.get("description") or info.get("title") or ""
        tags = info.get("tags") or []
        hashtags = list(dict.fromkeys([*extract_hashtags(desc), *(t for t in tags if t)]))
        created = info.get("timestamp") or 0
        if not created and info.get("upload_date"):
            try:
                created = time.mktime(time.strptime(info["upload_date"], "%Y%m%d"))
            except ValueError:
                created = 0
        author = (
            info.get("uploader")
            or info.get("uploader_id")
            or info.get("channel")
            or info.get("creator")
            or ""
        )
        music = info.get("track") or ""
        if info.get("artist") and music:
            music = f"{music} - {info['artist']}"
        return cls(
            id=str(info.get("id") or info.get("url") or ""),
            url=info.get("webpage_url") or info.get("original_url") or info.get("url") or "",
            author=str(author),
            description=desc,
            hashtags=hashtags,
            music=music,
            duration=float(info.get("duration") or 0),
            views=int(info.get("view_count") or 0),
            likes=int(info.get("like_count") or 0),
            comment_count=int(info.get("comment_count") or 0),
            shares=int(info.get("repost_count") or 0),
            created=float(created or 0),
            source=source,
            thumbnail=info.get("thumbnail") or "",
        )


@dataclass
class Comment:
    id: str
    author: str
    text: str
    likes: int = 0
    created: float = 0.0
    reply_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Comment":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_ytdlp(cls, c: dict[str, Any]) -> "Comment":
        return cls(
            id=str(c.get("id") or ""),
            author=str(c.get("author") or c.get("author_id") or "unknown"),
            text=str(c.get("text") or ""),
            likes=int(c.get("like_count") or 0),
            created=float(c.get("timestamp") or 0),
            reply_count=int(c.get("reply_count") or 0),
        )


def extract_hashtags(text: str) -> list[str]:
    return [h.lower() for h in HASHTAG_RE.findall(text or "")]


def human_count(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B".replace(".0B", "B")
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def tiktok_count(n: int) -> str:
    """Counts the way TikTok shows them under its buttons: 441, 4489, 18.490, 1,2M."""
    if n < 10_000:
        return str(n)
    if n < 1_000_000:
        return f"{n:,}".replace(",", ".")
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M").replace(".", ",")
    return f"{n / 1_000_000_000:.1f}B".replace(".0B", "B").replace(".", ",")
