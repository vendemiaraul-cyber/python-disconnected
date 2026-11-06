"""Data model shared by every provider."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Video:
    """One search result, normalised across sources."""

    id: str
    title: str
    url: str  # canonical watch page on the source website
    embed_url: str  # official embeddable player for the source
    source: str  # provider key, e.g. "youtube"
    source_name: str  # human name, e.g. "YouTube"
    source_domain: str  # website the video came from, e.g. "youtube.com"
    ad_supported: bool  # the source monetises playback with ads
    thumbnail: Optional[str] = None
    duration: Optional[int] = None  # seconds
    channel: Optional[str] = None
    views: Optional[int] = None
    published: Optional[str] = None  # ISO-8601 date when known
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
