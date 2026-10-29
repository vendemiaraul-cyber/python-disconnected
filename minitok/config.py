"""Settings that survive restarts, stored as JSON in the MiniTok data folder."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .algorithm.features import FEATURE_NAMES
from .algorithm.presets import DEFAULT_PRESET, PRESETS

COMMENT_LIMIT = 30  # comments downloaded per video


def data_dir() -> Path:
    root = Path(os.environ.get("MINITOK_HOME") or Path.home() / ".minitok")
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class AlgorithmConfig:
    preset: str = DEFAULT_PRESET
    weights: dict[str, float] = field(default_factory=lambda: dict(PRESETS[DEFAULT_PRESET].weights))
    formula: str = "3*interest + like_rate + 0.5*freshness + 0.2*random"
    exploration: float = PRESETS[DEFAULT_PRESET].exploration
    diversity: float = PRESETS[DEFAULT_PRESET].diversity
    interests: dict[str, float] = field(default_factory=dict)  # token -> weight
    blocked: list[str] = field(default_factory=list)  # words / #tags / @creators never shown
    preferred_duration: float = 25.0
    learn_while_watching: bool = True
    learned_weights: dict[str, float] = field(default_factory=dict)

    def apply_preset(self, key: str) -> None:
        preset = PRESETS[key]
        self.preset = key
        if key == "learned" and self.learned_weights:
            self.weights = {n: float(self.learned_weights.get(n, 0.0)) for n in FEATURE_NAMES}
        else:
            self.weights = dict(preset.weights)
        self.exploration = preset.exploration
        self.diversity = preset.diversity

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlgorithmConfig":
        cfg = cls()
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        if cfg.preset not in PRESETS:
            cfg.preset = DEFAULT_PRESET
        cfg.weights = {n: float(cfg.weights.get(n, 0.0)) for n in FEATURE_NAMES}
        cfg.interests = {str(k): float(v) for k, v in cfg.interests.items()}
        cfg.exploration = min(1.0, max(0.0, float(cfg.exploration)))
        cfg.diversity = min(1.0, max(0.0, float(cfg.diversity)))
        return cfg


@dataclass
class AppConfig:
    algorithm: AlgorithmConfig = field(default_factory=AlgorithmConfig)
    # where candidate videos come from: TikTok @users, #hashtags, or any URL yt-dlp understands
    sources: list[str] = field(default_factory=list)
    local_folder: str = ""  # a folder of video files to use as (extra) candidates
    prefetch: int = 2  # videos downloaded ahead of the one you are watching
    max_height: int = 720  # download quality cap - smaller files, faster start
    volume: float = 0.8
    muted: bool = False
    cookies_from_browser: str = ""  # e.g. "firefox" - helps when TikTok blocks anonymous requests

    @property
    def path(self) -> Path:
        return data_dir() / "settings.json"

    def save(self) -> None:
        data = asdict(self)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        try:
            data = json.loads(cfg.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cfg
        for key, value in data.items():
            if key == "algorithm":
                cfg.algorithm = AlgorithmConfig.from_dict(value or {})
            elif hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg
