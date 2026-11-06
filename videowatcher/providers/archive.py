"""Internet Archive moving-image collection (non-profit, ad-free)."""

from __future__ import annotations

from typing import List

from ..models import Video
from .base import Provider

API = "https://archive.org/advancedsearch.php"


class ArchiveProvider(Provider):
    key = "archive"
    name = "Internet Archive"
    domain = "archive.org"
    ad_supported = False

    def search(self, query: str, limit: int) -> List[Video]:
        # TV-news captures can't be embedded, so leave them out.
        q = f"({query}) AND mediatype:(movies) AND NOT collection:(tvnews OR tvarchive)"
        params = [
            ("q", q),
            ("rows", limit),
            ("output", "json"),
            ("sort[]", "downloads desc"),
        ] + [("fl[]", f) for f in ("identifier", "title", "creator", "date", "downloads")]
        resp = self.session.get(API, params=params, timeout=self.timeout)
        resp.raise_for_status()
        out = []
        for doc in resp.json().get("response", {}).get("docs", []):
            ident = doc.get("identifier")
            if not ident:
                continue
            creator = doc.get("creator")
            if isinstance(creator, list):
                creator = ", ".join(creator)
            title = doc.get("title")
            if isinstance(title, list):
                title = title[0] if title else None
            out.append(
                self._video(
                    id=ident,
                    title=title or ident,
                    url=f"https://archive.org/details/{ident}",
                    embed_url=f"https://archive.org/embed/{ident}?autoplay=1",
                    thumbnail=f"https://archive.org/services/img/{ident}",
                    channel=creator,
                    views=doc.get("downloads"),
                    published=(doc.get("date") or "")[:10] or None,
                )
            )
        return out
