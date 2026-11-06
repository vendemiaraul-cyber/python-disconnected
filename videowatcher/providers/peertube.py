"""PeerTube (federated, ad-free) via the SepiaSearch index."""

from __future__ import annotations

from typing import List
from urllib.parse import urlparse

from ..models import Video
from .base import Provider

API = "https://sepiasearch.org/api/v1/search/videos"


class PeerTubeProvider(Provider):
    key = "peertube"
    name = "PeerTube"
    domain = "joinpeertube.org"
    ad_supported = False

    def search(self, query: str, limit: int) -> List[Video]:
        data = self._get_json(
            API,
            {"search": query, "count": limit, "nsfw": "false", "sort": "-match"},
        )
        out = []
        for item in data.get("data", []):
            url, embed = item.get("url"), item.get("embedUrl")
            if not (url and embed and embed.startswith("https://")):
                continue
            host = urlparse(url).hostname or self.domain
            out.append(
                self._video(
                    id=item.get("uuid") or str(item.get("id")),
                    title=item.get("name") or "(untitled)",
                    url=url,
                    embed_url=f"{embed}?autoplay=1",
                    thumbnail=item.get("thumbnailUrl"),
                    duration=item.get("duration"),
                    channel=(item.get("channel") or {}).get("displayName")
                    or (item.get("account") or {}).get("displayName"),
                    views=item.get("views"),
                    published=(item.get("publishedAt") or "")[:10] or None,
                    # each PeerTube instance is its own website - show it
                    source_domain=host,
                )
            )
        return out
