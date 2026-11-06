"""Dailymotion search via its public REST API (no key needed)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from ..models import Video
from .base import Provider

API = "https://api.dailymotion.com/videos"
FIELDS = "id,title,url,thumbnail_360_url,duration,owner.screenname,views_total,created_time"


class DailymotionProvider(Provider):
    key = "dailymotion"
    name = "Dailymotion"
    domain = "dailymotion.com"
    ad_supported = True

    def search(self, query: str, limit: int) -> List[Video]:
        data = self._get_json(
            API,
            {"search": query, "limit": limit, "fields": FIELDS, "family_filter": "true"},
        )
        out = []
        for item in data.get("list", []):
            vid = item.get("id")
            if not vid:
                continue
            created = item.get("created_time")
            out.append(
                self._video(
                    id=vid,
                    title=item.get("title") or "(untitled)",
                    url=item.get("url") or f"https://www.dailymotion.com/video/{vid}",
                    embed_url=f"https://geo.dailymotion.com/player.html?video={vid}&autoplay=1",
                    thumbnail=item.get("thumbnail_360_url"),
                    duration=item.get("duration"),
                    channel=item.get("owner.screenname"),
                    views=item.get("views_total"),
                    published=(
                        datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat()
                        if created is not None
                        else None
                    ),
                )
            )
        return out
