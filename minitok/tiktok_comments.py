"""TikTok's public web comment list - shared by the Scrapy spider and the fallback fetcher."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from .models import Comment

# overridable so the tests can point the spider at a local fake server
API = os.environ.get("MINITOK_TIKTOK_COMMENT_API", "https://www.tiktok.com/api/comment/list/")
PAGE_MAX = 50
HEADERS = {"Referer": "https://www.tiktok.com/", "Accept": "application/json"}


def page_url(aweme_id: str, cursor: int, count: int) -> str:
    query = {"aweme_id": aweme_id, "count": min(PAGE_MAX, max(1, count)), "cursor": cursor, "aid": 1988}
    return f"{API}?{urlencode(query)}"


def parse_page(data: dict[str, Any]) -> tuple[list[Comment], bool, int]:
    """Return (comments, has_more, next_cursor) for one API response."""
    out = []
    for c in data.get("comments") or []:
        user = c.get("user") or {}
        out.append(Comment(
            id=str(c.get("cid") or ""),
            author=user.get("unique_id") or user.get("nickname") or "unknown",
            text=c.get("text") or "",
            likes=int(c.get("digg_count") or 0),
            created=float(c.get("create_time") or 0),
            reply_count=int(c.get("reply_comment_total") or 0),
        ))
    has_more = bool(data.get("has_more")) and bool(out)
    return out, has_more, int(data.get("cursor") or 0)
