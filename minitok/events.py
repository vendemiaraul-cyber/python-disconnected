"""Watch history recorded inside the app - the training data for "Learned from my data".

One JSON line per video you left: what it was, the features the ranker saw,
and how you reacted (watch time, loops, like). Nothing leaves your machine.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from .models import Video

MAX_INTEREST_TOKENS = 300


@dataclass
class Impression:
    video: Video
    features: dict[str, float] = field(default_factory=dict)
    shown_at: float = field(default_factory=time.time)
    watched_seconds: float = 0.0
    loops: int = 0  # how many times it played to the end
    liked: bool = False
    opened_comments: bool = False
    explored: bool = False
    preset: str = ""

    @property
    def watch_ratio(self) -> float:
        if self.video.duration <= 0:
            return 0.0
        return (self.loops * self.video.duration + self.watched_seconds) / self.video.duration

    def engagement(self) -> float:
        """0 = clear skip, 1 = clearly enjoyed. Used both online and for training."""
        if self.liked:
            return 1.0
        if self.loops >= 1 or self.watch_ratio >= 0.9:
            return 1.0
        total = self.loops * self.video.duration + self.watched_seconds
        if total < 2.0 or (self.video.duration > 0 and self.watch_ratio < 0.2):
            return 0.0
        score = min(1.0, self.watch_ratio) if self.video.duration > 0 else min(1.0, total / 30.0)
        if self.opened_comments:
            score = min(1.0, score + 0.3)
        return score

    def to_dict(self) -> dict:
        d = asdict(self)
        d["video"] = self.video.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Impression":
        d = dict(d)
        video = Video.from_dict(d.pop("video"))
        known = set(cls.__dataclass_fields__)
        return cls(video=video, **{k: v for k, v in d.items() if k in known})


class EventLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, imp: Impression) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(imp.to_dict(), ensure_ascii=False) + "\n")

    def __iter__(self) -> Iterator[Impression]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield Impression.from_dict(json.loads(line))
                except (ValueError, TypeError, KeyError):
                    continue  # skip a corrupted line rather than losing the whole history

    def count(self) -> int:
        return sum(1 for _ in self)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


def update_interests(interests: dict[str, float], imp: Impression, rate: float = 1.0) -> dict[str, float]:
    """Online learning: nudge the interest profile after each video."""
    e = imp.engagement()
    delta = (e - 0.4) * rate  # skipping (0) pushes down, finishing (1) pushes up
    if imp.liked:
        delta += 0.3 * rate
    if abs(delta) < 1e-6:
        return interests
    for tok in imp.video.tokens():
        strength = 1.0 if tok[0] in "#@♪" else 0.3  # caption words are a weak signal
        interests[tok] = max(-3.0, min(3.0, interests.get(tok, 0.0) + delta * strength))
    if len(interests) > MAX_INTEREST_TOKENS:
        keep = sorted(interests.items(), key=lambda kv: abs(kv[1]), reverse=True)[:MAX_INTEREST_TOKENS]
        interests.clear()
        interests.update(keep)
    return interests
