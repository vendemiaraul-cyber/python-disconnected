"""Base class for video sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import requests

from ..models import Video

USER_AGENT = "VideoWatcher/1.0 (+https://github.com/vendemiaraul-cyber/python-disconnected)"


class Provider(ABC):
    #: short machine key used in URLs / filters
    key: str = ""
    #: display name shown in the UI
    name: str = ""
    #: website the videos come from
    domain: str = ""
    #: True when the source shows ads in its player. For these sources we
    #: always play through the platform's official embed so its ads run
    #: exactly as the platform intends - we never strip or skip them.
    ad_supported: bool = False

    def __init__(self, timeout: float = 8.0, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    @abstractmethod
    def search(self, query: str, limit: int) -> List[Video]:
        """Return up to ``limit`` videos for ``query``."""

    def _get_json(self, url: str, params: dict) -> dict:
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _video(self, **kwargs) -> Video:
        return Video(
            source=self.key,
            source_name=self.name,
            source_domain=kwargs.pop("source_domain", self.domain),
            ad_supported=self.ad_supported,
            **kwargs,
        )

    def info(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "domain": self.domain,
            "ad_supported": self.ad_supported,
        }
