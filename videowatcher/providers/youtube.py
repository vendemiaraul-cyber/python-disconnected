"""YouTube search via yt-dlp (no API key needed).

Only metadata is fetched. Playback always goes through YouTube's official
embedded player (``youtube.com/embed``), so YouTube serves its normal ads.
"""

from __future__ import annotations

from typing import List

from ..models import Video
from .base import Provider


class YouTubeProvider(Provider):
    key = "youtube"
    name = "YouTube"
    domain = "youtube.com"
    ad_supported = True

    def search(self, query: str, limit: int) -> List[Video]:
        import yt_dlp  # imported lazily: it is slow to import

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "socket_timeout": self.timeout,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return [v for v in (self._parse(e) for e in info.get("entries") or []) if v]

    def _parse(self, entry: dict) -> Video | None:
        vid = entry.get("id")
        if not vid or entry.get("ie_key", "Youtube") != "Youtube":
            return None  # skip channels / playlists that can show up in search
        thumbs = entry.get("thumbnails") or []
        thumb = thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        duration = entry.get("duration")
        return self._video(
            id=vid,
            title=entry.get("title") or "(untitled)",
            url=f"https://www.youtube.com/watch?v={vid}",
            embed_url=f"https://www.youtube.com/embed/{vid}?autoplay=1&rel=0",
            thumbnail=thumb,
            duration=int(duration) if duration else None,
            channel=entry.get("channel") or entry.get("uploader"),
            views=entry.get("view_count"),
        )
