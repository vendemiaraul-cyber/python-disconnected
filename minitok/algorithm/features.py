"""Feature extraction: turns a Video (plus who is watching) into numbers.

Every feature is scaled to roughly 0..1 so weights are comparable and so a
custom formula like ``2*interest + popularity`` behaves predictably.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..models import Video

# name -> human description (shown in the settings dialog and the formula help)
FEATURES: dict[str, str] = {
    "interest": "How well the video matches your interest profile (hashtags, creators, sounds, words)",
    "popularity": "Total views, log-scaled (10M views ≈ 1.0)",
    "like_rate": "Likes per view",
    "comment_rate": "Comments per view",
    "share_rate": "Shares per view",
    "freshness": "How new the video is (halves every ~2 days)",
    "duration_fit": "How close the length is to your preferred length",
    "novelty": "1 for creators you have not seen lately, lower for repeats",
    "random": "A fresh random number every time (pure exploration)",
}

FEATURE_NAMES = list(FEATURES)


@dataclass
class ViewerState:
    """What the ranker knows about the person watching."""

    interests: dict[str, float] = field(default_factory=dict)  # token -> weight
    preferred_duration: float = 25.0
    recent_authors: list[str] = field(default_factory=list)  # most recent last

    def interest_score(self, video: Video) -> float:
        if not self.interests:
            return 0.0
        total = sum(self.interests.get(t, 0.0) for t in video.tokens())
        # squash into -1..1; one strong match (~2) gives ~0.76
        return math.tanh(total / 2.0)


def _rate(part: int, whole: int, scale: float) -> float:
    if whole <= 0:
        return 0.0
    return min(1.0, (part / whole) * scale)


def extract(video: Video, viewer: ViewerState, rng: random.Random | None = None) -> dict[str, float]:
    rng = rng or random
    age = video.age_hours
    freshness = 0.5 if age is None else math.exp(-age / 72.0)

    if video.duration > 0 and viewer.preferred_duration > 0:
        diff = abs(video.duration - viewer.preferred_duration) / viewer.preferred_duration
        duration_fit = max(0.0, 1.0 - diff)
    else:
        duration_fit = 0.5

    author = video.author.lower().lstrip("@")
    novelty = 1.0
    if author and author in viewer.recent_authors:
        # how many videos ago did we last see this creator? (1 = the previous one)
        last = len(viewer.recent_authors) - 1 - viewer.recent_authors[::-1].index(author)
        novelty = min(1.0, (len(viewer.recent_authors) - last) / 10.0)

    return {
        "interest": viewer.interest_score(video),
        "popularity": min(1.0, math.log10(video.views + 1) / 7.0),
        "like_rate": _rate(video.likes, video.views, 8.0),
        "comment_rate": _rate(video.comment_count, video.views, 100.0),
        "share_rate": _rate(video.shares, video.views, 50.0),
        "freshness": freshness,
        "duration_fit": duration_fit,
        "novelty": novelty,
        "random": rng.random(),
    }
