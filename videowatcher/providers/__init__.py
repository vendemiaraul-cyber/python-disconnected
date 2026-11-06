"""Registry of available video sources."""

from __future__ import annotations

from typing import Dict, Type

from .archive import ArchiveProvider
from .base import Provider
from .dailymotion import DailymotionProvider
from .peertube import PeerTubeProvider
from .youtube import YouTubeProvider

PROVIDER_CLASSES: Dict[str, Type[Provider]] = {
    cls.key: cls
    for cls in (YouTubeProvider, DailymotionProvider, PeerTubeProvider, ArchiveProvider)
}


def build_providers(timeout: float = 8.0) -> Dict[str, Provider]:
    return {key: cls(timeout=timeout) for key, cls in PROVIDER_CLASSES.items()}


__all__ = ["Provider", "PROVIDER_CLASSES", "build_providers"]
