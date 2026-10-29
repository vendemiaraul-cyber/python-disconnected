"""Picks the next video from the candidate pool according to the algorithm config."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..models import Video
from .features import ViewerState, extract
from .formula import Formula, FormulaError


@dataclass
class Scored:
    video: Video
    score: float
    features: dict[str, float]
    explored: bool = False  # picked by the exploration roll, not by score


class Ranker:
    def __init__(self, config, rng: random.Random | None = None):
        """``config`` is an AlgorithmConfig (kept duck-typed to avoid an import cycle)."""
        self.config = config
        self.rng = rng or random.Random()
        self._formula: Formula | None = None
        self._formula_src = ""

    def formula(self) -> Formula | None:
        if self.config.preset != "formula":
            return None
        if self._formula is None or self._formula_src != self.config.formula:
            self._formula = Formula(self.config.formula)
            self._formula_src = self.config.formula
        return self._formula

    def viewer(self, recent_authors: list[str]) -> ViewerState:
        return ViewerState(
            interests=dict(self.config.interests),
            preferred_duration=float(self.config.preferred_duration or 25.0),
            recent_authors=[a.lower().lstrip("@") for a in recent_authors],
        )

    def is_blocked(self, video: Video) -> bool:
        if not self.config.blocked:
            return False
        tokens = set(video.tokens())
        desc = video.description.lower()
        for raw in self.config.blocked:
            b = raw.strip().lower()
            if not b:
                continue
            if b in tokens or (b[0] not in "#@♪" and b in desc):
                return True
        return False

    def score(self, video: Video, viewer: ViewerState) -> Scored:
        feats = extract(video, viewer, self.rng)
        formula = self.formula()
        if formula is not None:
            value = formula.evaluate(feats, video)
        else:
            value = sum(self.config.weights.get(k, 0.0) * v for k, v in feats.items())
        # diversity: push down creators we just saw
        value -= self.config.diversity * (1.0 - feats["novelty"]) * 2.0
        return Scored(video, value, feats)

    def rank(self, candidates: list[Video], recent_authors: list[str]) -> list[Scored]:
        viewer = self.viewer(recent_authors)
        scored = [self.score(v, viewer) for v in candidates if not self.is_blocked(v)]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    def pick(self, candidates: list[Video], recent_authors: list[str]) -> Scored | None:
        ranked = self.rank(candidates, recent_authors)
        if not ranked:
            return None
        if len(ranked) > 1 and self.rng.random() < self.config.exploration:
            choice = self.rng.choice(ranked)
            choice.explored = True
            return choice
        return ranked[0]

    def validate(self) -> str | None:
        """Return an error message if the current config cannot rank, else None."""
        try:
            f = self.formula()
            if f is not None:
                f.evaluate(extract(Video(id="x", url=""), ViewerState(), self.rng), Video(id="x", url=""))
        except FormulaError as exc:
            return str(exc)
        return None
